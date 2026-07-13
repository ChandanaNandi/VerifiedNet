"""Projection — a verified run -> a DatasetExample (Gate 6.1).

``project_verified_run`` is a PURE function of an already-verified run: no
filesystem, no Docker, no randomness, no timestamps, no side effects. It reads
the immutable ``LoadedRun`` and emits references + stable identity. It never
re-serializes a run, never rewrites canonical bytes, and never touches the
authoritative ``IncidentRecord`` (including the reserved ``dataset_*`` fields).
"""

from __future__ import annotations

from verifiednet.artifacts.layout import ROLE_TO_PATH, ArtifactRole
from verifiednet.artifacts.reader import LoadedRun
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.discovery import DiscoveredRun
from verifiednet.datasets.models import ArtifactReference, DatasetExample

#: The lab backend identifier for the current library. Runs record it on the
#: IncidentRecord; kept explicit so ``group_id`` includes the backend.
_BACKEND_FIELD = "backend"


class ProjectionError(VerifiedNetError):
    """A verified run could not be projected into a dataset example."""


def compute_group_id(loaded: LoadedRun) -> str:
    """Leakage group id from the STABLE scenario identity ONLY (ADR-0018 §5).

    Uses template + scenario id + orientation + stable parameters + topology +
    backend. It NEVER uses ``run_id``, ``incident_id``, ``run_digest``,
    ``injected_at``, or any timestamp — those differ across two runs of the same
    scenario, so keying on them would silently leak equivalent runs across
    splits. Two runs of the same catalog case therefore share one ``group_id``.
    """
    incident = loaded.incident
    scenario = incident.scenario
    params = scenario.parameters
    group_key = {
        "template_id": scenario.template_id,
        "scenario_id": scenario.scenario_id,
        "target_node": str(params.get("target_node", "")),
        "target_session": str(params.get("target_session", "")),
        "parameters": {k: params[k] for k in sorted(params)},
        "topology_hash": incident.topology_hash,
        "backend": incident.backend,
    }
    return "grp-" + sha256_canonical(group_key)[:16]


def compute_example_id(loaded: LoadedRun) -> str:
    """Unique per-run id from the immutable run identity (distinct from group_id).

    Keyed on ``run_id`` (unique per run) so every verified run yields exactly one
    example id; deterministic (projecting the same run twice yields the same id).
    Contrast ``group_id``, which is intentionally SHARED across equivalent runs.
    """
    return "ex-" + sha256_canonical({"run_id": loaded.run_id})[:16]


def _ref(run_id: str, role: ArtifactRole) -> ArtifactReference:
    return ArtifactReference(run_id=run_id, relative_path=ROLE_TO_PATH[role])


def project_verified_run(discovered: DiscoveredRun) -> DatasetExample:
    """Project one verified run into a frozen, reference-only dataset example."""
    loaded = discovered.loaded
    incident = loaded.incident
    run_id = loaded.run_id

    accepted = incident.status == "accepted"
    # Sanity (already guaranteed by IncidentRecord validation): accepted <=> GT.
    if accepted and incident.ground_truth is None:
        raise ProjectionError(f"accepted run {run_id} has no ground truth")
    if not accepted and incident.ground_truth is not None:
        raise ProjectionError(f"rejected run {run_id} unexpectedly carries ground truth")

    onset_ref = (
        _ref(run_id, ArtifactRole.EVIDENCE_ONSET)
        if ArtifactRole.EVIDENCE_ONSET in loaded.evidence
        else None
    )
    recovery_ref = (
        _ref(run_id, ArtifactRole.EVIDENCE_RECOVERY)
        if ArtifactRole.EVIDENCE_RECOVERY in loaded.evidence
        else None
    )
    # Ground truth lives INSIDE incident.json; the reference points there for
    # accepted runs and stays None for rejected runs (label absent by design).
    ground_truth_ref = _ref(run_id, ArtifactRole.INCIDENT) if accepted else None

    return DatasetExample(
        example_id=compute_example_id(loaded),
        group_id=compute_group_id(loaded),
        run_id=run_id,
        run_digest=loaded.run_digest,
        template_id=incident.scenario.template_id,
        scenario_id=incident.scenario.scenario_id,
        topology_hash=incident.topology_hash,
        backend=incident.backend,
        acceptance_status=incident.status,
        incident_reference=_ref(run_id, ArtifactRole.INCIDENT),
        ground_truth_reference=ground_truth_ref,
        transcript_reference=_ref(run_id, ArtifactRole.TRANSCRIPT),
        ledger_reference=_ref(run_id, ArtifactRole.LEDGER),
        baseline_reference=_ref(run_id, ArtifactRole.EVIDENCE_BASELINE),
        onset_reference=onset_ref,
        recovery_reference=recovery_ref,
        code_commit=incident.provenance.code_commit,
        oracle_version=incident.oracle_version,
        source_index_digest=discovered.source_index_digest,
    )
