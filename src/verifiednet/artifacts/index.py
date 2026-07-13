"""Deterministic, integrity-verifiable index of completed run directories.

An index root holds one ``index.json`` plus one subdirectory per run
(``<index_root>/<run_id>/``). The index is canonical JSON, atomically written,
and every entry is added ONLY after its run directory independently verifies.
Reading the index re-verifies every referenced run before returning trusted data.

Digest rule (non-recursive): ``index_digest`` = ``sha256_canonical`` of the
run-id-sorted list of ``{run_id, run_dir, run_digest, status, topology_hash}``.
It hashes the entries only, never itself.

This module is low-level persistence: it imports schemas + common + the sibling
artifact modules; it never imports live execution, labs, or the orchestrator.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from verifiednet.artifacts.durable import atomic_write_bytes
from verifiednet.artifacts.layout import (
    INCOMPLETE_MARKER,
    CheckOutcome,
    is_safe_relative_path,
    is_safe_run_id,
)
from verifiednet.artifacts.reader import LoadedRun, load_run
from verifiednet.artifacts.verify import ArtifactIntegrityError, verify_run_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.common.runctx import utc_now
from verifiednet.schemas.base import StrictModel, UtcDatetime

INDEX_FILE = "index.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RunIndexError(VerifiedNetError):
    """A run-index operation failed (duplicate, missing run, corruption)."""


class RunIndexEntry(StrictModel):
    """One indexed run: identity, status, relative path, and digests."""

    schema_version: Literal[1] = 1
    run_id: str
    incident_id: str
    scenario_id: str
    template_id: str
    acceptance_status: Literal["accepted", "rejected"]
    run_dir: str  # relative path == run_id
    run_digest: str
    topology_hash: str
    layout_schema_version: int
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None

    @field_validator("run_dir")
    @classmethod
    def _validate_dir(cls, value: str) -> str:
        if not is_safe_relative_path(value):
            raise ValueError(f"unsafe or absolute run_dir: {value!r}")
        return value

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        if not is_safe_run_id(value):
            raise ValueError(f"unsafe run_id: {value!r}")
        return value

    @field_validator("run_digest", "topology_hash")
    @classmethod
    def _validate_hex(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"expected 64 lowercase hex: {value!r}")
        return value


class RunIndex(StrictModel):
    """``index.json`` — the run-id-ordered set of indexed runs plus its digest."""

    schema_version: Literal[1] = 1
    index_digest: str
    entries: tuple[RunIndexEntry, ...] = Field(default_factory=tuple)

    @field_validator("index_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"index_digest must be 64 lowercase hex: {value!r}")
        return value


class RunIndexVerificationResult(StrictModel):
    """Structured index integrity result (not a bool)."""

    schema_version: Literal[1] = 1
    verified: bool
    index_digest: str
    checks: tuple[CheckOutcome, ...] = Field(min_length=1)
    verified_at: UtcDatetime

    @property
    def failures(self) -> tuple[CheckOutcome, ...]:
        return tuple(c for c in self.checks if not c.passed)


def compute_index_digest(entries: tuple[RunIndexEntry, ...]) -> str:
    """Non-recursive digest over the run-id-sorted entry identities."""
    items = [
        {
            "run_id": e.run_id,
            "run_dir": e.run_dir,
            "run_digest": e.run_digest,
            "status": e.acceptance_status,
            "topology_hash": e.topology_hash,
        }
        for e in sorted(entries, key=lambda e: e.run_id)
    ]
    return sha256_canonical(items)


def _entry_from_loaded(loaded: LoadedRun) -> RunIndexEntry:
    inc = loaded.incident
    return RunIndexEntry(
        run_id=loaded.run_id,
        incident_id=inc.incident_id,
        scenario_id=inc.scenario.scenario_id,
        template_id=inc.scenario.template_id,
        acceptance_status=inc.status,
        run_dir=loaded.run_id,
        run_digest=loaded.run_digest,
        topology_hash=inc.topology_hash,
        layout_schema_version=loaded.layout.layout_schema_version,
        started_at=loaded.run_manifest.started_at,
        finished_at=loaded.run_manifest.finished_at,
    )


def load_run_index(index_root: str | Path) -> RunIndex:
    """Parse ``index.json`` (raise ``RunIndexError`` if missing or invalid)."""
    path = Path(index_root) / INDEX_FILE
    try:
        return RunIndex.model_validate_json(path.read_bytes())
    except OSError as exc:
        raise RunIndexError(f"index not found at {path}: {exc}") from exc
    except ValueError as exc:
        raise RunIndexError(f"index is corrupt at {path}: {exc}") from exc


def add_run_to_index(index_root: str | Path, run_id: str) -> RunIndex:
    """Add ``<index_root>/<run_id>`` to the index AFTER it verifies. Atomic write.

    Rejects a duplicate ``run_id``. A duplicate ``incident_id`` is ALLOWED and
    expected: incident ids are content-derived, so two runs of the same accepted
    scenario legitimately share one — the run_id remains the unique key.
    """
    index_root = Path(index_root)
    if not is_safe_run_id(run_id):
        raise RunIndexError(f"unsafe run_id: {run_id!r}")
    run_dir = index_root / run_id
    if (run_dir / INCOMPLETE_MARKER).exists():
        raise RunIndexError(f"refusing to index an .INCOMPLETE run: {run_id}")
    loaded = load_run(run_dir)  # verifies the whole run or raises

    existing: tuple[RunIndexEntry, ...] = ()
    if (index_root / INDEX_FILE).exists():
        existing = load_run_index(index_root).entries
    if any(entry.run_id == run_id for entry in existing):
        raise RunIndexError(f"duplicate run_id already indexed: {run_id}")

    entries = tuple(sorted((*existing, _entry_from_loaded(loaded)), key=lambda e: e.run_id))
    index = RunIndex(index_digest=compute_index_digest(entries), entries=entries)
    atomic_write_bytes(index_root / INDEX_FILE, canonical_json_bytes(index))
    return index


def _c(rule: str, passed: bool, detail: str = "") -> CheckOutcome:
    return CheckOutcome(rule=rule, passed=passed, detail=detail)


def verify_run_index(index_root: str | Path) -> RunIndexVerificationResult:
    """Verify the index and every referenced run; report unindexed run dirs."""
    index_root = Path(index_root)
    checks: list[CheckOutcome] = []
    try:
        index = load_run_index(index_root)
        checks.append(_c("index_parses", True))
    except RunIndexError as exc:
        checks.append(_c("index_parses", False, str(exc)))
        return _finish("0" * 64, checks)

    recomputed = compute_index_digest(index.entries)
    checks.append(_c("index_digest_matches", recomputed == index.index_digest,
                     f"stored={index.index_digest} recomputed={recomputed}"))
    canon = canonical_json_bytes(index)
    stored = (index_root / INDEX_FILE).read_bytes()
    checks.append(_c("index_canonical_bytes", canon == stored))

    indexed_dirs: set[str] = set()
    seen_ids: set[str] = set()
    for entry in index.entries:
        indexed_dirs.add(entry.run_dir)
        if entry.run_id in seen_ids:
            checks.append(_c(f"unique_run_id:{entry.run_id}", False, "duplicate entry"))
        seen_ids.add(entry.run_id)
        if not is_safe_relative_path(entry.run_dir) or entry.run_dir != entry.run_id:
            checks.append(_c(f"safe_run_dir:{entry.run_id}", False, entry.run_dir))
            continue
        run_dir = index_root / entry.run_dir
        if (run_dir / INCOMPLETE_MARKER).exists():
            checks.append(_c(f"not_incomplete:{entry.run_id}", False, ".INCOMPLETE indexed"))
            continue
        result = verify_run_dir(run_dir)
        checks.append(_c(f"run_verifies:{entry.run_id}", result.verified,
                         "" if result.verified else str([c.rule for c in result.failures])))
        checks.append(_c(f"run_digest_matches:{entry.run_id}",
                         result.run_digest == entry.run_digest))

    # unindexed run directories (a dir with a hashes.json not present in the index)
    for child in sorted(index_root.iterdir()):
        if child.is_dir() and child.name not in indexed_dirs and (child / "hashes.json").exists():
            checks.append(_c(f"no_unindexed_run:{child.name}", False, "unindexed run directory"))

    return _finish(index.index_digest, checks)


def _finish(index_digest: str, checks: list[CheckOutcome]) -> RunIndexVerificationResult:
    return RunIndexVerificationResult(
        verified=all(c.passed for c in checks),
        index_digest=index_digest,
        checks=tuple(checks),
        verified_at=utc_now(),
    )


def load_verified_run_from_index(index_root: str | Path, run_id: str) -> LoadedRun:
    """Verify the index, resolve exactly one entry, verify + load that run."""
    index_root = Path(index_root)
    result = verify_run_index(index_root)
    if not result.verified:
        raise ArtifactIntegrityError(
            "index failed verification: "
            + "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        )
    index = load_run_index(index_root)
    matches = [e for e in index.entries if e.run_id == run_id]
    if len(matches) != 1:
        raise RunIndexError(
            f"expected exactly one index entry for {run_id!r}, found {len(matches)}"
        )
    entry = matches[0]
    if not is_safe_relative_path(entry.run_dir) or entry.run_dir != run_id:
        raise RunIndexError(f"unsafe run_dir for {run_id!r}: {entry.run_dir!r}")
    run_dir = index_root / entry.run_dir
    verified = verify_run_dir(run_dir)
    if not verified.verified:
        raise ArtifactIntegrityError(f"referenced run {run_id} failed verification")
    if verified.run_digest != entry.run_digest:
        raise ArtifactIntegrityError(f"run_digest mismatch for {run_id}")
    return load_run(run_dir)
