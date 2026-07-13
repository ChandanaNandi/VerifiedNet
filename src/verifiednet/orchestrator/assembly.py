"""One-call assembly of a verified, indexed run (Gate 4 Step 6).

``assemble_verified_run`` builds both manifests, writes the canonical run
directory, verifies it independently, adds it to the run index atomically,
verifies the index, loads the run back through the index, and returns typed
paths + digests + loaded models. It owns NO Docker and NO fault execution — the
live composition root calls it only after a run reaches a valid terminal
outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from verifiednet.artifacts import (
    ArtifactIntegrityError,
    LoadedRun,
    RunIndexEntry,
    add_run_to_index,
    load_verified_run_from_index,
    verify_run_dir,
    verify_run_index,
    write_run_artifacts,
)
from verifiednet.artifacts.index import load_run_index
from verifiednet.faults.ledger import LedgerRecord
from verifiednet.orchestrator.manifests import (
    build_environment_manifest,
    build_run_manifest,
    transcript_sha256,
)
from verifiednet.runtime.transcript import TranscriptEntry
from verifiednet.schemas.incident import IncidentRecord


@dataclass(frozen=True)
class AssembledRun:
    """Result of assembling one verified, indexed run."""

    run_dir: Path
    index_root: Path
    run_id: str
    run_digest: str
    index_digest: str
    loaded: LoadedRun
    index_entry: RunIndexEntry


def assemble_verified_run(
    *,
    out_root: str | Path,
    incident: IncidentRecord,
    environment_metadata: Mapping[str, str],
    transcript_entries: Sequence[TranscriptEntry],
    ledger_records: Sequence[LedgerRecord],
    git_rev: str,
    lock_hash: str,
    started_at: datetime,
    finished_at: datetime,
) -> AssembledRun:
    """Persist, verify, index, and load-back one completed run."""
    out_root = Path(out_root)
    run_manifest = build_run_manifest(
        incident=incident,
        git_rev=git_rev,
        lock_hash=lock_hash,
        transcript_sha=transcript_sha256(list(transcript_entries)),
        started_at=started_at,
        finished_at=finished_at,
    )
    environment_manifest = build_environment_manifest(
        environment_metadata, captured_at=finished_at
    )

    written = write_run_artifacts(
        out_root=out_root,
        run_manifest=run_manifest,
        environment_manifest=environment_manifest,
        incident=incident,
        transcript_entries=transcript_entries,
        ledger_records=ledger_records,
    )
    # Independent verification of the run directory before touching the index.
    run_result = verify_run_dir(written.root)
    if not run_result.verified:
        raise ArtifactIntegrityError(
            "assembled run failed verification: "
            + "; ".join(f"{c.rule}: {c.detail}" for c in run_result.failures)
        )

    add_run_to_index(out_root, written.run_id)
    index_result = verify_run_index(out_root)
    if not index_result.verified:
        raise ArtifactIntegrityError(
            "run index failed verification after update: "
            + "; ".join(f"{c.rule}: {c.detail}" for c in index_result.failures)
        )

    loaded = load_verified_run_from_index(out_root, written.run_id)
    entry = next(
        e for e in load_run_index(out_root).entries if e.run_id == written.run_id
    )
    return AssembledRun(
        run_dir=written.root,
        index_root=out_root,
        run_id=written.run_id,
        run_digest=written.run_digest,
        index_digest=index_result.index_digest,
        loaded=loaded,
        index_entry=entry,
    )
