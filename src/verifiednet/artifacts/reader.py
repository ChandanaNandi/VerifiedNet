"""Reader for a canonical run directory — offline reconstruction and validation.

"Replay" here means offline load + integrity validation, NOT re-executing the
network incident. The reader refuses ``.INCOMPLETE`` directories, verifies every
hash and structural rule before returning trusted data, and never executes
commands, contacts Docker, or mutates files. On any integrity failure it raises
:class:`ArtifactIntegrityError` naming the failed rules — it never repairs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from verifiednet.artifacts.layout import (
    INCOMPLETE_MARKER,
    ArtifactRole,
    RunLayout,
)
from verifiednet.artifacts.verify import ArtifactIntegrityError, verify_run_dir
from verifiednet.faults.ledger import LedgerRecord
from verifiednet.runtime.transcript import TranscriptEntry
from verifiednet.schemas.evidence import EvidenceBundle
from verifiednet.schemas.incident import IncidentRecord
from verifiednet.schemas.manifests import EnvironmentManifest, RunManifest

_EVIDENCE_ROLE_PATH = {
    ArtifactRole.EVIDENCE_BASELINE: "evidence/baseline.json",
    ArtifactRole.EVIDENCE_ONSET: "evidence/onset.json",
    ArtifactRole.EVIDENCE_RECOVERY: "evidence/recovery.json",
}


@dataclass(frozen=True)
class LoadedRun:
    """A fully-loaded, hash-verified run directory."""

    root: Path
    run_id: str
    run_digest: str
    layout: RunLayout
    incident: IncidentRecord
    run_manifest: RunManifest
    environment_manifest: EnvironmentManifest
    transcript: tuple[TranscriptEntry, ...]
    ledger: tuple[LedgerRecord, ...]
    evidence: dict[ArtifactRole, EvidenceBundle]


def load_run(root: str | Path) -> LoadedRun:
    """Load and verify a run directory; raise ``ArtifactIntegrityError`` on failure."""
    root = Path(root)
    if (root / INCOMPLETE_MARKER).exists():
        raise ArtifactIntegrityError(f"run directory is marked .INCOMPLETE: {root}")

    result = verify_run_dir(root)
    if not result.verified:
        raise ArtifactIntegrityError(
            "run directory failed verification: "
            + "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        )

    layout = RunLayout.model_validate_json((root / "layout.json").read_bytes())
    incident = IncidentRecord.model_validate_json((root / "incident.json").read_bytes())
    run_manifest = RunManifest.model_validate_json((root / "run_manifest.json").read_bytes())
    environment_manifest = EnvironmentManifest.model_validate_json(
        (root / "environment_manifest.json").read_bytes()
    )
    transcript = _load_lines(root / "transcript.jsonl", TranscriptEntry)
    ledger = _load_lines(root / "ledger.jsonl", LedgerRecord)

    listed_roles = {entry.role for entry in layout.artifacts}
    evidence: dict[ArtifactRole, EvidenceBundle] = {}
    for role, rel in _EVIDENCE_ROLE_PATH.items():
        if role in listed_roles:
            evidence[role] = EvidenceBundle.model_validate_json((root / rel).read_bytes())

    return LoadedRun(
        root=root,
        run_id=result.run_id,
        run_digest=result.run_digest,
        layout=layout,
        incident=incident,
        run_manifest=run_manifest,
        environment_manifest=environment_manifest,
        transcript=transcript,
        ledger=ledger,
        evidence=evidence,
    )


def _load_lines[M](path: Path, model: type[M]) -> tuple[M, ...]:
    items: list[M] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(model.model_validate_json(line))  # type: ignore[attr-defined]
    return tuple(items)
