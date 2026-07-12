"""VerifiedNet data contracts (Wave A). DB-free, versioned, strict, frozen."""

from verifiednet.schemas.evidence import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    Phase,
    PhaseField,
    SealedBundleViolation,
)
from verifiednet.schemas.fault import FaultInjection
from verifiednet.schemas.ground_truth import GroundTruth
from verifiednet.schemas.incident import (
    IncidentRecord,
    ProvenanceInfo,
    RejectionCode,
    RejectionInfo,
    RestorationMetadata,
)
from verifiednet.schemas.manifests import EnvironmentManifest, RunManifest
from verifiednet.schemas.scenario import ScenarioDefinition, ScenarioTimeouts
from verifiednet.schemas.topology import (
    BgpSessionSpec,
    ImageSpec,
    LinkEndpoint,
    LinkSpec,
    NodeSpec,
    SessionEndpoint,
    TopologySpec,
)
from verifiednet.schemas.verification import (
    Predicate,
    Verdict,
    VerificationCheck,
    VerificationResult,
)

__all__ = [
    "BgpSessionSpec",
    "EnvironmentManifest",
    "EvidenceBundle",
    "EvidenceRecord",
    "EvidenceSource",
    "FaultInjection",
    "GroundTruth",
    "ImageSpec",
    "IncidentRecord",
    "LinkEndpoint",
    "LinkSpec",
    "NodeSpec",
    "Phase",
    "PhaseField",
    "Predicate",
    "ProvenanceInfo",
    "RejectionCode",
    "RejectionInfo",
    "RestorationMetadata",
    "RunManifest",
    "ScenarioDefinition",
    "ScenarioTimeouts",
    "SealedBundleViolation",
    "SessionEndpoint",
    "TopologySpec",
    "Verdict",
    "VerificationCheck",
    "VerificationResult",
]
