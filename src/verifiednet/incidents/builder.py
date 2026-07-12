"""IncidentRecord builders: the accepted record and the honest rejected one.

Rejected records retain everything the run produced up to the failure point
(Gate 3 Step 9): baseline/onset/recovery evidence, phase results, restoration
metadata, completed phases and cleanup status — plus machine-readable
``RejectionInfo``. They never carry ground truth (schema-enforced).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from verifiednet.common.hashing import sha256_canonical
from verifiednet.common.runctx import RunContext
from verifiednet.schemas.evidence import EvidenceBundle
from verifiednet.schemas.fault import FaultInjection
from verifiednet.schemas.ground_truth import GroundTruth
from verifiednet.schemas.incident import (
    IncidentRecord,
    ProvenanceInfo,
    RejectionCode,
    RejectionInfo,
    RestorationMetadata,
)
from verifiednet.schemas.scenario import ScenarioDefinition
from verifiednet.schemas.topology import TopologySpec
from verifiednet.schemas.verification import VerificationResult

CleanupStatus = Literal["clean", "restore_failed", "teardown_failed", "unknown"]


def _sealed(bundle: EvidenceBundle) -> EvidenceBundle:
    return bundle if bundle.sealed else bundle.seal()


def _sealed_opt(bundle: EvidenceBundle | None) -> EvidenceBundle | None:
    return None if bundle is None else _sealed(bundle)


def _incident_id(
    run_ctx: RunContext,
    scenario: ScenarioDefinition,
    fault: FaultInjection | None,
    topology_hash: str,
) -> str:
    return run_ctx.content_id(
        "inc",
        {
            "scenario": scenario.scenario_id,
            "fault": None if fault is None else fault.model_dump(mode="json"),
            "topology_hash": topology_hash,
        },
    )


def build_accepted_record(
    *,
    run_ctx: RunContext,
    scenario: ScenarioDefinition,
    topology: TopologySpec,
    fault: FaultInjection,
    ground_truth: GroundTruth,
    baseline: EvidenceBundle,
    onset: EvidenceBundle,
    recovery: EvidenceBundle | None,
    precondition_results: Sequence[VerificationResult],
    onset_results: Sequence[VerificationResult],
    recovery_results: Sequence[VerificationResult],
    restoration: RestorationMetadata,
    provenance: ProvenanceInfo,
    completed_phases: Sequence[str],
    cleanup_status: CleanupStatus,
) -> IncidentRecord:
    """Build the canonical accepted incident record; bundles are sealed here."""
    topology_hash = sha256_canonical(topology)
    return IncidentRecord(
        incident_id=_incident_id(run_ctx, scenario, fault, topology_hash),
        run_id=run_ctx.run_id,
        scenario=scenario,
        backend=topology.backend,
        topology=topology,
        topology_hash=topology_hash,
        fault=fault,
        ground_truth=ground_truth,
        baseline_evidence=_sealed(baseline),
        onset_evidence=_sealed(onset),
        recovery_evidence=_sealed_opt(recovery),
        precondition_results=tuple(precondition_results),
        onset_results=tuple(onset_results),
        recovery_results=tuple(recovery_results),
        restoration=restoration,
        provenance=provenance,
        oracle_version=ground_truth.oracle_version,
        completed_phases=tuple(completed_phases),
        cleanup_status=cleanup_status,
        created_at=run_ctx.now(),
        status="accepted",
        rejection=None,
    )


def build_rejected_record(
    *,
    run_ctx: RunContext,
    scenario: ScenarioDefinition,
    topology: TopologySpec,
    baseline: EvidenceBundle,
    rejection_code: RejectionCode,
    details: str,
    failed_phase: str,
    fault: FaultInjection | None = None,
    onset: EvidenceBundle | None = None,
    recovery: EvidenceBundle | None = None,
    precondition_results: Sequence[VerificationResult] = (),
    onset_results: Sequence[VerificationResult] = (),
    recovery_results: Sequence[VerificationResult] = (),
    restoration: RestorationMetadata | None = None,
    provenance: ProvenanceInfo,
    completed_phases: Sequence[str],
    cleanup_status: CleanupStatus,
) -> IncidentRecord:
    """Build an honest rejected record retaining all evidence gathered so far."""
    topology_hash = sha256_canonical(topology)
    return IncidentRecord(
        incident_id=_incident_id(run_ctx, scenario, fault, topology_hash),
        run_id=run_ctx.run_id,
        scenario=scenario,
        backend=topology.backend,
        topology=topology,
        topology_hash=topology_hash,
        fault=fault,
        ground_truth=None,
        baseline_evidence=_sealed(baseline),
        onset_evidence=_sealed_opt(onset),
        recovery_evidence=_sealed_opt(recovery),
        precondition_results=tuple(precondition_results),
        onset_results=tuple(onset_results),
        recovery_results=tuple(recovery_results),
        restoration=restoration,
        provenance=provenance,
        oracle_version=None,
        completed_phases=tuple(completed_phases),
        cleanup_status=cleanup_status,
        created_at=run_ctx.now(),
        status="rejected",
        rejection=RejectionInfo(
            code=rejection_code,
            details=details,
            failed_phase=failed_phase,
        ),
    )
