"""Append-only fault-lifecycle ledger with explicit legal transitions.

Provenance: modeled on closcall ``chaos/ledger.py`` (commit d192bf3),
REIMPLEMENTED FROM SPECIFICATION — closcall has no published license — with
torn-line tolerance added per Gate 2.5: a malformed FINAL line of a ledger
file is tolerated (a crash mid-write must not make the whole ledger
unreadable), while a malformed NON-final line is corruption and raises.

The ledger is append-only: no record is ever rewritten, and every transition
is checked against ``LEGAL_TRANSITIONS`` before it is recorded. There is no
module-global state; each ``Ledger`` instance owns its own record list.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from verifiednet.common.errors import LedgerError, PhaseTransitionError
from verifiednet.common.runctx import RunContext


class LifecyclePhase(StrEnum):
    PENDING = "pending"
    PRECHECKED = "prechecked"
    INJECTING = "injecting"
    INJECTED = "injected"
    ONSET_VERIFIED = "onset_verified"
    RESTORING = "restoring"
    RESTORED = "restored"
    RECOVERY_VERIFIED = "recovery_verified"


# INJECTING -> RESTORING is deliberately legal: if the injection command fails
# midway the ledger stays visibly in INJECTING, and the mutation-failure
# recovery path must still be allowed to attempt restoration from there.
LEGAL_TRANSITIONS: dict[LifecyclePhase, frozenset[LifecyclePhase]] = {
    LifecyclePhase.PENDING: frozenset({LifecyclePhase.PRECHECKED}),
    LifecyclePhase.PRECHECKED: frozenset({LifecyclePhase.INJECTING}),
    LifecyclePhase.INJECTING: frozenset({LifecyclePhase.INJECTED, LifecyclePhase.RESTORING}),
    LifecyclePhase.INJECTED: frozenset(
        {LifecyclePhase.ONSET_VERIFIED, LifecyclePhase.RESTORING}
    ),
    LifecyclePhase.ONSET_VERIFIED: frozenset({LifecyclePhase.RESTORING}),
    LifecyclePhase.RESTORING: frozenset({LifecyclePhase.RESTORED}),
    LifecyclePhase.RESTORED: frozenset({LifecyclePhase.RECOVERY_VERIFIED}),
    LifecyclePhase.RECOVERY_VERIFIED: frozenset(),
}


def _require_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("ledger timestamps must be timezone-aware")
    return value


class LedgerRecord(BaseModel):
    """One immutable, append-only lifecycle entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=1)
    phase: LifecyclePhase
    at: Annotated[datetime, AfterValidator(_require_tz)]
    detail: str = ""


class Ledger:
    """In-memory (optionally file-backed) append-only lifecycle ledger."""

    def __init__(self, run_ctx: RunContext, path: Path | None = None) -> None:
        self._run_ctx = run_ctx
        self._path = path
        self._records: list[LedgerRecord] = []

    @property
    def current(self) -> LifecyclePhase:
        return self._records[-1].phase if self._records else LifecyclePhase.PENDING

    @property
    def records(self) -> tuple[LedgerRecord, ...]:
        return tuple(self._records)

    def append(self, phase: LifecyclePhase, detail: str = "") -> LedgerRecord:
        current = self.current
        if phase not in LEGAL_TRANSITIONS[current]:
            raise PhaseTransitionError(f"{current} -> {phase} illegal")
        record = LedgerRecord(
            seq=self._run_ctx.next_seq(),
            phase=phase,
            at=self._run_ctx.now(),
            detail=detail,
        )
        if self._path is not None:
            self._write_line(record)
        self._records.append(record)
        return record

    def _write_line(self, record: LedgerRecord) -> None:
        line = json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        try:
            with self._path.open("a", encoding="utf-8") as handle:  # type: ignore[union-attr]
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerError(f"ledger write failed at {self._path}: {exc}") from exc

    @staticmethod
    def read(path: Path) -> tuple[tuple[LedgerRecord, ...], bool]:
        """Parse a ledger file; a torn FINAL line is tolerated (returned flag).

        Returns ``(records, torn)``. A malformed non-final line is corruption
        and raises ``LedgerError``.
        """
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise LedgerError(f"ledger read failed at {path}: {exc}") from exc
        records: list[LedgerRecord] = []
        for index, line in enumerate(lines):
            try:
                records.append(LedgerRecord.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValidationError) as exc:
                if index == len(lines) - 1:
                    return tuple(records), True
                raise LedgerError(f"corruption at line {index + 1}") from exc
        return tuple(records), False
