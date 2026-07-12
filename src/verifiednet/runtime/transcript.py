"""Command transcripts — an ordered, durable record of every executed command.

The mutation executor uses these entries in a write-ahead pattern (see
``verifiednet.runtime.mutation``); provenance for that usage: modeled on the
closcall ``guarded_mutation`` write-ahead pattern, reimplemented from
specification (closcall license unresolved).

The timezone-awareness validator is deliberately reimplemented locally rather
than imported from ``verifiednet.schemas`` (schemas stay import-clean of
implementation packages and vice versa per the AST boundary policy intent).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import AfterValidator, BaseModel, ConfigDict

from verifiednet.common.canonical import canonical_json_str
from verifiednet.common.errors import TranscriptWriteError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.runtime.invocation import CommandInvocation


def _require_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("started_at must be timezone-aware (UTC)")
    return value


_AwareDatetime = Annotated[datetime, AfterValidator(_require_tz)]


class TranscriptEntry(BaseModel):
    """One immutable transcript line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int
    mode: Literal["read", "mutation"]
    stage: Literal["pending", "completed"]
    target: str
    argv: tuple[str, ...]
    status: str
    started_at: _AwareDatetime
    duration_s: float = 0.0
    #: Gate 4 (additive, optional): pairs pending/terminal entries by the same
    #: ``command_id`` and retains both logical and transport argv. ``None`` for
    #: Gate 3-style entries, so v0.3-serialized transcript lines still validate.
    invocation: CommandInvocation | None = None


class TranscriptWriter(Protocol):
    """Sink for transcript entries."""

    def append(self, entry: TranscriptEntry) -> None:
        """Append one entry; MUST raise ``TranscriptWriteError`` on failure."""
        ...


class InMemoryTranscript:
    """List-backed transcript; ``fail_after`` injects write failures for tests."""

    def __init__(self, fail_after: int | None = None) -> None:
        self._entries: list[TranscriptEntry] = []
        self._fail_after = fail_after

    @property
    def entries(self) -> tuple[TranscriptEntry, ...]:
        return tuple(self._entries)

    def append(self, entry: TranscriptEntry) -> None:
        if self._fail_after is not None and len(self._entries) >= self._fail_after:
            raise TranscriptWriteError(
                f"injected transcript failure after {self._fail_after} entries"
            )
        self._entries.append(entry)

    def sha256(self) -> str:
        """SHA-256 of the canonical JSON list of entries (deterministic)."""
        return sha256_canonical(list(self._entries))


class FileTranscript:
    """JSONL transcript file; each append is one canonical-JSON line, fsynced."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, entry: TranscriptEntry) -> None:
        line = canonical_json_str(entry)
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise TranscriptWriteError(
                f"transcript write failed for {self._path}: {exc}"
            ) from exc
