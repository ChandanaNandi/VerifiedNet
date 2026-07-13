"""Projection — a verified run -> a DatasetExample (Gate 6.1/6.2).

``project_verified_run`` is a PURE function of an already-verified run: no
filesystem, no Docker, no randomness, no timestamps, no side effects. It reads
the immutable ``LoadedRun`` and emits references + stable identity. It never
re-serializes a run, never rewrites canonical bytes, and never touches the
authoritative ``IncidentRecord`` (including the reserved ``dataset_*`` fields).

Gate 6.2 splits projection into two explicit, status-checked functions:

* ``project_accepted_run`` — an accepted fault run becomes an
  ``ACCEPTED_FAULT`` example carrying a ground-truth reference.
* ``project_rejected_run`` — a rejected precondition run becomes an
  ``ABSTENTION`` example that carries NO fault-family label, NO ground truth,
  and NO onset/recovery evidence. Rejected runs are EVAL-ONLY; they are never
  negative training labels (ADR-0018).

``project_verified_run`` remains the status dispatcher. Every invariant the
source record already guarantees is RE-checked here and fails closed with a
typed error rather than silently emitting a malformed example.
"""

from __future__ import annotations

from verifiednet.artifacts.layout import ROLE_TO_PATH, ArtifactRole
from verifiednet.artifacts.reader import LoadedRun
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.discovery import DiscoveredRun
from verifiednet.datasets.models import (
    ArtifactReference,
    DatasetExample,
    DatasetExampleKind,
    StableScenarioIdentity,
)

#: The failed phase a rejected precondition run must report (Gate 4/6 invariant).
_PRECONDITION_PHASE = "precondition"


class ProjectionError(VerifiedNetError):
    """A verified run could not be projected into a dataset example."""


class AcceptedProjectionError(ProjectionError):
    """An accepted run violated an accepted-example invariant."""


class RejectedProjectionError(ProjectionError):
    """A rejected run violated a rejected/abstention-example invariant."""


class UnsupportedRejectedSubtypeError(RejectedProjectionError):
    """A rejected run failed for a phase this projection does not yet support."""


# ---------------------------------------------------------------------------
# Stable identity + ids
# ---------------------------------------------------------------------------


def build_stable_identity(loaded: LoadedRun) -> StableScenarioIdentity:
    """The STABLE, timestamp-free scenario identity of a verified run.

    Contains only run-independent facts, so two runs of the same scenario share
    it byte-for-byte. ``group_id`` is a pure hash of these fields.
    """
    incident = loaded.incident
    scenario = incident.scenario
    params = scenario.parameters
    return StableScenarioIdentity(
        template_id=scenario.template_id,
        scenario_id=scenario.scenario_id,
        target_node=str(params.get("target_node", "")),
        target_session=str(params.get("target_session", "")),
        parameters={k: params[k] for k in sorted(params)},
        topology_hash=incident.topology_hash,
        backend=incident.backend,
    )


def _group_key(identity: StableScenarioIdentity) -> dict[str, object]:
    """The canonical dict hashed into a ``group_id``.

    Kept identical to the Gate 6.1 key (no ``schema_version``) so the emitted
    ``group_id`` value is preserved and the leakage audit can independently
    recompute it from ``DatasetExample.stable_identity`` alone.
    """
    return {
        "template_id": identity.template_id,
        "scenario_id": identity.scenario_id,
        "target_node": identity.target_node,
        "target_session": identity.target_session,
        "parameters": {k: identity.parameters[k] for k in sorted(identity.parameters)},
        "topology_hash": identity.topology_hash,
        "backend": identity.backend,
    }


def group_id_for_identity(identity: StableScenarioIdentity) -> str:
    """Deterministic ``group_id`` from a stable identity (audit + projection)."""
    return "grp-" + sha256_canonical(_group_key(identity))[:16]


def compute_group_id(loaded: LoadedRun) -> str:
    """Leakage group id from the STABLE scenario identity ONLY (ADR-0018 §5).

    Uses template + scenario id + orientation + stable parameters + topology +
    backend. It NEVER uses ``run_id``, ``incident_id``, ``run_digest``,
    ``injected_at``, or any timestamp — those differ across two runs of the same
    scenario, so keying on them would silently leak equivalent runs across
    splits. Two runs of the same catalog case therefore share one ``group_id``.
    """
    return group_id_for_identity(build_stable_identity(loaded))


def example_id_for_run_id(run_id: str) -> str:
    """Pure ``example_id`` from a run id (audit + projection share this)."""
    return "ex-" + sha256_canonical({"run_id": run_id})[:16]


def compute_example_id(loaded: LoadedRun) -> str:
    """Unique per-run id from the immutable run identity (distinct from group_id).

    Keyed on ``run_id`` (unique per run) so every verified run yields exactly one
    example id; deterministic (projecting the same run twice yields the same id).
    Contrast ``group_id``, which is intentionally SHARED across equivalent runs.
    """
    return example_id_for_run_id(loaded.run_id)


def _ref(run_id: str, role: ArtifactRole) -> ArtifactReference:
    return ArtifactReference(run_id=run_id, relative_path=ROLE_TO_PATH[role])


# ---------------------------------------------------------------------------
# Accepted / rejected projection
# ---------------------------------------------------------------------------


def project_accepted_run(discovered: DiscoveredRun) -> DatasetExample:
    """Project an ACCEPTED fault run into a ground-truth-bearing example.

    Re-checks every accepted invariant and fails closed with
    ``AcceptedProjectionError`` rather than emitting a malformed example.
    """
    loaded = discovered.loaded
    incident = loaded.incident
    run_id = loaded.run_id

    if incident.status != "accepted":
        raise AcceptedProjectionError(
            f"run {run_id} is not accepted (status={incident.status!r})"
        )
    if incident.ground_truth is None:
        raise AcceptedProjectionError(f"accepted run {run_id} has no ground truth")
    if incident.fault is None:
        raise AcceptedProjectionError(f"accepted run {run_id} has no fault")
    onset_present = ArtifactRole.EVIDENCE_ONSET in loaded.evidence
    recovery_present = ArtifactRole.EVIDENCE_RECOVERY in loaded.evidence
    if not onset_present or not recovery_present:
        raise AcceptedProjectionError(
            f"accepted run {run_id} missing onset/recovery evidence"
        )

    return DatasetExample(
        example_id=compute_example_id(loaded),
        group_id=compute_group_id(loaded),
        example_kind=DatasetExampleKind.ACCEPTED_FAULT,
        stable_identity=build_stable_identity(loaded),
        run_id=run_id,
        run_digest=loaded.run_digest,
        template_id=incident.scenario.template_id,
        scenario_id=incident.scenario.scenario_id,
        topology_hash=incident.topology_hash,
        backend=incident.backend,
        acceptance_status="accepted",
        # Ground truth lives INSIDE incident.json; the reference points there.
        incident_reference=_ref(run_id, ArtifactRole.INCIDENT),
        ground_truth_reference=_ref(run_id, ArtifactRole.INCIDENT),
        transcript_reference=_ref(run_id, ArtifactRole.TRANSCRIPT),
        ledger_reference=_ref(run_id, ArtifactRole.LEDGER),
        baseline_reference=_ref(run_id, ArtifactRole.EVIDENCE_BASELINE),
        onset_reference=_ref(run_id, ArtifactRole.EVIDENCE_ONSET),
        recovery_reference=_ref(run_id, ArtifactRole.EVIDENCE_RECOVERY),
        code_commit=incident.provenance.code_commit,
        oracle_version=incident.oracle_version,
        source_index_digest=discovered.source_index_digest,
    )


def project_rejected_run(discovered: DiscoveredRun) -> DatasetExample:
    """Project a REJECTED precondition run into an ABSTENTION example.

    An abstention example carries NO fault-family label, NO ground truth, and NO
    onset/recovery evidence. It is EVAL-ONLY (never a negative training label).
    Every rejected invariant is re-checked; violations fail closed with
    ``RejectedProjectionError`` (or ``UnsupportedRejectedSubtypeError``).
    """
    loaded = discovered.loaded
    incident = loaded.incident
    run_id = loaded.run_id

    if incident.status != "rejected":
        raise RejectedProjectionError(
            f"run {run_id} is not rejected (status={incident.status!r})"
        )
    if incident.ground_truth is not None:
        raise RejectedProjectionError(
            f"rejected run {run_id} unexpectedly carries ground truth"
        )
    if incident.fault is not None:
        raise RejectedProjectionError(
            f"rejected run {run_id} unexpectedly carries a fault"
        )
    if incident.restoration is not None:
        raise RejectedProjectionError(
            f"rejected run {run_id} unexpectedly carries a restoration"
        )
    if incident.rejection is None:
        raise RejectedProjectionError(
            f"rejected run {run_id} has no rejection information"
        )
    if ArtifactRole.EVIDENCE_BASELINE not in loaded.evidence:
        raise RejectedProjectionError(
            f"rejected run {run_id} has no sealed baseline evidence"
        )
    if (
        ArtifactRole.EVIDENCE_ONSET in loaded.evidence
        or ArtifactRole.EVIDENCE_RECOVERY in loaded.evidence
    ):
        raise RejectedProjectionError(
            f"rejected run {run_id} unexpectedly carries onset/recovery evidence"
        )
    mutation_entries = [e for e in loaded.transcript if e.mode == "mutation"]
    if mutation_entries:
        raise RejectedProjectionError(
            f"rejected run {run_id} has a non-empty mutation transcript"
        )

    failed_phase = incident.rejection.failed_phase
    if failed_phase != _PRECONDITION_PHASE:
        raise UnsupportedRejectedSubtypeError(
            f"rejected run {run_id} failed_phase={failed_phase!r} is unsupported"
        )

    return DatasetExample(
        example_id=compute_example_id(loaded),
        group_id=compute_group_id(loaded),
        example_kind=DatasetExampleKind.ABSTENTION,
        stable_identity=build_stable_identity(loaded),
        run_id=run_id,
        run_digest=loaded.run_digest,
        template_id=incident.scenario.template_id,
        scenario_id=incident.scenario.scenario_id,
        topology_hash=incident.topology_hash,
        backend=incident.backend,
        acceptance_status="rejected",
        # SOURCE FACTS ONLY — never a fault-family label inferred from scenario.
        rejection_code=str(incident.rejection.code),
        failed_phase=failed_phase,
        incident_reference=_ref(run_id, ArtifactRole.INCIDENT),
        ground_truth_reference=None,
        transcript_reference=_ref(run_id, ArtifactRole.TRANSCRIPT),
        ledger_reference=_ref(run_id, ArtifactRole.LEDGER),
        baseline_reference=_ref(run_id, ArtifactRole.EVIDENCE_BASELINE),
        onset_reference=None,
        recovery_reference=None,
        code_commit=incident.provenance.code_commit,
        oracle_version=incident.oracle_version,
        source_index_digest=discovered.source_index_digest,
    )


def project_verified_run(discovered: DiscoveredRun) -> DatasetExample:
    """Dispatch a verified run to the accepted/rejected projection by status."""
    status = discovered.loaded.incident.status
    if status == "accepted":
        return project_accepted_run(discovered)
    if status == "rejected":
        return project_rejected_run(discovered)
    raise ProjectionError(
        f"run {discovered.loaded.run_id} has unsupported status {status!r}"
    )
