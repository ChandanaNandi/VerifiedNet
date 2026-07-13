"""Verified dataset engine — a read-only, deterministic projection of the Gate 5
verified run library (Gate 6.1/6.2).

This package is a CONSUMER of already-verified run artifacts, never a producer
of truth (ADR-0018). It never modifies a run, an IncidentRecord, evidence, a
transcript, a ledger, a manifest, or the run index; it never rewrites a run
digest; it never contacts Docker/FRR, re-runs the lab, re-derives evidence,
re-runs verification/oracle, infers labels, or invokes any model. Truth flows
one way: verified runs -> dataset projection -> (future) evaluation -> (future)
models, never backward.

Gate 6.1 scope: dataset models, verified-run discovery, and pure projection
(including the stable leakage ``group_id`` and the unique ``example_id``).
Gate 6.2 (this increment) adds: rejected runs projected as EVAL-ONLY abstention
examples, a deterministic randomness-free ``SplitPolicy`` with integer bucket
splitting, a separate ``AssignedDatasetExample`` binding, and a fail-closed
leakage audit. Corpus writing, dataset digests, manifests, and export are Part 3.
"""

from verifiednet.datasets.discovery import (
    DatasetDiscoveryError,
    DiscoveredRun,
    discover_verified_runs,
)
from verifiednet.datasets.leakage import audit_leakage
from verifiednet.datasets.models import (
    SPLIT_BUCKET_COUNT,
    ArtifactReference,
    AssignedDatasetExample,
    DatasetExample,
    DatasetExampleKind,
    DatasetManifest,
    DatasetPartition,
    LeakageAuditResult,
    LeakageFinding,
    LeakageFindingCode,
    LeakageSeverity,
    SplitPolicy,
    StableScenarioIdentity,
)
from verifiednet.datasets.projection import (
    AcceptedProjectionError,
    ProjectionError,
    RejectedProjectionError,
    UnsupportedRejectedSubtypeError,
    build_stable_identity,
    compute_example_id,
    compute_group_id,
    group_id_for_identity,
    project_accepted_run,
    project_rejected_run,
    project_verified_run,
)
from verifiednet.datasets.splitting import (
    ABSTENTION_POLICY_ID,
    DatasetSplitError,
    assign_example_split,
    assign_group_split,
    assign_splits,
    split_policy_id,
)

__all__ = [
    "ABSTENTION_POLICY_ID",
    "SPLIT_BUCKET_COUNT",
    "AcceptedProjectionError",
    "ArtifactReference",
    "AssignedDatasetExample",
    "DatasetDiscoveryError",
    "DatasetExample",
    "DatasetExampleKind",
    "DatasetManifest",
    "DatasetPartition",
    "DatasetSplitError",
    "DiscoveredRun",
    "LeakageAuditResult",
    "LeakageFinding",
    "LeakageFindingCode",
    "LeakageSeverity",
    "ProjectionError",
    "RejectedProjectionError",
    "SplitPolicy",
    "StableScenarioIdentity",
    "UnsupportedRejectedSubtypeError",
    "assign_example_split",
    "assign_group_split",
    "assign_splits",
    "audit_leakage",
    "build_stable_identity",
    "compute_example_id",
    "compute_group_id",
    "discover_verified_runs",
    "group_id_for_identity",
    "project_accepted_run",
    "project_rejected_run",
    "project_verified_run",
    "split_policy_id",
]
