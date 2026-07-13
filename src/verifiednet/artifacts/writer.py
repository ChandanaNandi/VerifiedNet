"""Writer for the canonical per-run artifact directory.

Durability:
- canonical JSON files (layout, incident, manifests, evidence, hashes,
  verification report) are written atomically: canonical bytes → temp sibling →
  flush → ``os.fsync`` → ``os.replace`` → parent-dir fsync;
- JSONL files (transcript, ledger) are FINALIZED: every verified canonical line
  is written to a temp file, fsynced, then atomically installed. This is a
  one-shot finalization of an already-complete in-memory history, NOT live
  write-ahead appending (the runtime owns write-ahead during execution).

A ``.INCOMPLETE`` marker exists throughout construction and is removed ONLY
after independent verification succeeds. On any failure the marker remains and
the directory is never reported complete.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.artifacts.layout import (
    EVIDENCE_DIR,
    HASH_INDEX_FILE,
    INCOMPLETE_MARKER,
    ROLE_TO_PATH,
    VERIFICATION_REPORT_FILE,
    ArtifactEntry,
    ArtifactHash,
    ArtifactHashIndex,
    ArtifactRole,
    RunLayout,
    is_safe_run_id,
)
from verifiednet.artifacts.verify import (
    ArtifactIntegrityError,
    compute_run_digest,
    verify_run_dir,
)
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes
from verifiednet.faults.ledger import LedgerRecord
from verifiednet.runtime.transcript import TranscriptEntry
from verifiednet.schemas.incident import IncidentRecord
from verifiednet.schemas.manifests import EnvironmentManifest, RunManifest


class ArtifactWriteError(VerifiedNetError):
    """Writing the run artifact directory failed (target collision, IO, etc.)."""


@dataclass(frozen=True)
class WrittenRun:
    """Result of writing one run directory."""

    root: Path
    run_id: str
    run_digest: str
    file_count: int
    index: ArtifactHashIndex


def _jsonl_bytes(items: Sequence[BaseModel]) -> bytes:
    lines = [canonical_json_bytes(item) for item in items]
    return b"".join(line + b"\n" for line in lines)


def _canonical_files(incident: IncidentRecord) -> dict[ArtifactRole, str]:
    """Roles present for this incident (evidence phases follow the incident)."""
    roles = {
        ArtifactRole.LAYOUT,
        ArtifactRole.INCIDENT,
        ArtifactRole.RUN_MANIFEST,
        ArtifactRole.ENVIRONMENT_MANIFEST,
        ArtifactRole.TRANSCRIPT,
        ArtifactRole.LEDGER,
        ArtifactRole.EVIDENCE_BASELINE,
    }
    if incident.onset_evidence is not None:
        roles.add(ArtifactRole.EVIDENCE_ONSET)
    if incident.recovery_evidence is not None:
        roles.add(ArtifactRole.EVIDENCE_RECOVERY)
    return {role: ROLE_TO_PATH[role] for role in roles}


def write_run_artifacts(
    *,
    out_root: str | Path,
    run_manifest: RunManifest,
    environment_manifest: EnvironmentManifest,
    incident: IncidentRecord,
    transcript_entries: Sequence[TranscriptEntry],
    ledger_records: Sequence[LedgerRecord],
) -> WrittenRun:
    """Write one self-contained, verified run directory. Fail loudly on any error.

    Evidence files are derived from the incident's own sealed bundles
    (``baseline_evidence``/``onset_evidence``/``recovery_evidence``) — the single
    source of truth — so a rejected run cannot gain fabricated onset/recovery
    files and an accepted run must carry all three.
    """
    run_id = run_manifest.run_id
    if not is_safe_run_id(run_id):
        raise ArtifactWriteError(f"unsafe run_id for a directory: {run_id!r}")
    if incident.run_id != run_id:
        raise ArtifactWriteError(
            f"incident.run_id {incident.run_id!r} != run_manifest.run_id {run_id!r}"
        )

    # Consistency preconditions (honest phase files).
    if incident.status == "accepted":
        if incident.onset_evidence is None or incident.recovery_evidence is None:
            raise ArtifactWriteError("accepted incident requires onset and recovery evidence")
    elif incident.onset_evidence is not None or incident.recovery_evidence is not None:
        raise ArtifactWriteError("rejected incident must not carry onset/recovery evidence")

    root = Path(out_root) / run_id
    if root.exists() and any(root.iterdir()):
        raise ArtifactWriteError(f"target run directory already exists and is non-empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    (root / EVIDENCE_DIR).mkdir(exist_ok=True)
    marker = root / INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)

    try:
        present = _canonical_files(incident)

        # canonical JSON payloads by role (layout computed last)
        payloads: dict[ArtifactRole, bytes] = {
            ArtifactRole.INCIDENT: canonical_json_bytes(incident),
            ArtifactRole.RUN_MANIFEST: canonical_json_bytes(run_manifest),
            ArtifactRole.ENVIRONMENT_MANIFEST: canonical_json_bytes(environment_manifest),
            ArtifactRole.EVIDENCE_BASELINE: canonical_json_bytes(
                incident.baseline_evidence.seal()
            ),
        }
        if ArtifactRole.EVIDENCE_ONSET in present and incident.onset_evidence is not None:
            payloads[ArtifactRole.EVIDENCE_ONSET] = canonical_json_bytes(
                incident.onset_evidence.seal()
            )
        if ArtifactRole.EVIDENCE_RECOVERY in present and incident.recovery_evidence is not None:
            payloads[ArtifactRole.EVIDENCE_RECOVERY] = canonical_json_bytes(
                incident.recovery_evidence.seal()
            )

        # JSONL finalization payloads
        payloads[ArtifactRole.TRANSCRIPT] = _jsonl_bytes(list(transcript_entries))
        payloads[ArtifactRole.LEDGER] = _jsonl_bytes(list(ledger_records))

        # layout.json lists every truth-bearing file (including itself)
        entries = tuple(
            ArtifactEntry(relative_path=present[role], role=role)
            for role in sorted(present, key=lambda r: present[r])
        )
        layout = RunLayout(
            run_id=run_id, acceptance_status=incident.status, artifacts=entries
        )
        payloads[ArtifactRole.LAYOUT] = canonical_json_bytes(layout)

        # write every truth-bearing file (JSONL via finalization = same atomic install)
        for role, rel in present.items():
            atomic_write_bytes(root / rel, payloads[role])

        # hash index over final bytes, then run digest, then hashes.json
        hashes = tuple(
            ArtifactHash(
                relative_path=rel,
                role=role,
                sha256=sha256_bytes((root / rel).read_bytes()),
                size=(root / rel).stat().st_size,
            )
            for role, rel in sorted(present.items(), key=lambda kv: kv[1])
        )
        digest = compute_run_digest(hashes)
        index = ArtifactHashIndex(run_id=run_id, run_digest=digest, entries=hashes)
        atomic_write_bytes(root / HASH_INDEX_FILE, canonical_json_bytes(index))

        # independent verification BEFORE declaring the run complete
        result = verify_run_dir(root, allow_incomplete_marker=True)
        if not result.verified:
            raise ArtifactIntegrityError(
                "post-write verification failed: "
                + "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
            )
        atomic_write_bytes(root / VERIFICATION_REPORT_FILE, canonical_json_bytes(result))
    except Exception:
        # Leave .INCOMPLETE in place; never report the directory as complete.
        raise

    marker.unlink()
    fsync_dir(root)
    file_count = sum(1 for p in root.rglob("*") if p.is_file())
    return WrittenRun(
        root=root, run_id=run_id, run_digest=digest, file_count=file_count, index=index
    )
