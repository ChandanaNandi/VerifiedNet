"""IncidentRecord — the canonical verified incident (or the honest rejected one).

RecoveryResult was merged into the ``recovery`` section per the approved Gate 2.5
recommendation: restoration metadata, forced-reset (clear-BGP) annotation, restore
transcript refs, phase-tagged VerificationResults, recovery evidence refs,
completion status and failure reason.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.schemas.base import StrictModel, UtcDatetime
from verifiednet.schemas.evidence import EvidenceBundle
from verifiednet.schemas.fault import FaultInjection
from verifiednet.schemas.ground_truth import GroundTruth
from verifiednet.schemas.scenario import ScenarioDefinition
from verifiednet.schemas.topology import TopologySpec
from verifiednet.schemas.verification import VerificationResult


class RejectionCode(StrEnum):
    PRECONDITION_FAILED = "precondition_failed"
    INJECT_FAILED = "inject_failed"
    ONSET_NOT_VERIFIED = "onset_not_verified"
    RESTORE_FAILED = "restore_failed"
    RECOVERY_NOT_VERIFIED = "recovery_not_verified"
    TRANSCRIPT_INCOMPLETE = "transcript_incomplete"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    EVIDENCE_CONTRADICTORY = "evidence_contradictory"
    INTERNAL_ERROR = "internal_error"


class RejectionInfo(StrictModel):
    code: RejectionCode
    details: str = ""
    failed_phase: str = ""


class RestorationMetadata(StrictModel):
    """Merged RecoveryResult content (owner-approved Gate 2.5 merge)."""

    method: str  # e.g. "vtysh-remote-as-revert"
    forced_reset_used: bool  # clear-BGP annotation (Gate 2.5 W13)
    forced_reset_command: str = ""  # e.g. "clear bgp <peer>"
    transcript_refs: tuple[int, ...] = Field(default_factory=tuple)
    completed: bool
    failure_reason: str = ""
    attempted: bool = True


class ProvenanceInfo(StrictModel):
    generator: str  # e.g. "verifiednet.faults.bgp_remote_as_mismatch"
    generator_version: str
    code_commit: str
    environment_manifest_sha256: str | None = None
    run_manifest_sha256: str | None = None


class IncidentRecord(StrictModel):
    schema_version: Literal[1] = 1
    incident_id: str
    run_id: str
    # scenario identity
    scenario: ScenarioDefinition
    # lab identity
    backend: str
    topology: TopologySpec
    topology_hash: str
    # what happened
    fault: FaultInjection | None = None  # None only on precondition-rejected records
    ground_truth: GroundTruth | None = None  # None on rejected records
    # evidence (phase-grouped bundles; sealed)
    baseline_evidence: EvidenceBundle
    onset_evidence: EvidenceBundle | None = None
    recovery_evidence: EvidenceBundle | None = None
    # verification results grouped by phase
    precondition_results: tuple[VerificationResult, ...] = Field(default_factory=tuple)
    onset_results: tuple[VerificationResult, ...] = Field(default_factory=tuple)
    recovery_results: tuple[VerificationResult, ...] = Field(default_factory=tuple)
    # recovery section (merged RecoveryResult)
    restoration: RestorationMetadata | None = None
    # provenance & bookkeeping
    provenance: ProvenanceInfo
    oracle_version: str | None = None
    completed_phases: tuple[str, ...] = Field(default_factory=tuple)
    cleanup_status: Literal["clean", "restore_failed", "teardown_failed", "unknown"] = "unknown"
    created_at: UtcDatetime
    status: Literal["accepted", "rejected"]
    rejection: RejectionInfo | None = None
    # dataset linkage (populated at Gate 6; kept for the mandated field set)
    dataset_group_id: str | None = None
    dataset_split: str | None = None

    @model_validator(mode="after")
    def _status_consistency(self) -> IncidentRecord:
        if self.status == "accepted":
            if self.rejection is not None:
                raise ValueError("accepted record must not carry rejection info")
            if self.ground_truth is None:
                raise ValueError("accepted record requires ground truth")
            if self.fault is None:
                raise ValueError("accepted record requires the fault injection record")
            if self.restoration is None or not self.restoration.completed:
                raise ValueError("accepted record requires completed restoration")
            if not (self.baseline_evidence.sealed and self.onset_evidence is not None):
                raise ValueError("accepted record requires sealed baseline and onset evidence")
        else:
            if self.rejection is None:
                raise ValueError("rejected record must carry machine-readable rejection info")
            if self.ground_truth is not None:
                raise ValueError("rejected record must not claim ground truth")
        return self
