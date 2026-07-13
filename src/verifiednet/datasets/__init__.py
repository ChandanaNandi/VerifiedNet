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
from verifiednet.datasets.export import (
    DATASET_INCOMPLETE_MARKER,
    DATASET_MANIFEST_FILE,
    DATASET_SPLITS_DIR,
    EXPECTED_SPLIT_FILES,
    SPLIT_FILE_BY_PARTITION,
    DatasetExportError,
    ExportedDataset,
    build_dataset,
    parse_split_bytes,
)
from verifiednet.datasets.leakage import audit_leakage
from verifiednet.datasets.models import (
    DATASET_EXPORT_VERSION,
    DATASET_GENERATOR,
    SPLIT_BUCKET_COUNT,
    ArtifactReference,
    AssignedDatasetExample,
    DatasetExample,
    DatasetExampleKind,
    DatasetFileHash,
    DatasetManifest,
    DatasetPartition,
    DatasetPartitionCounts,
    LeakageAuditResult,
    LeakageFinding,
    LeakageFindingCode,
    LeakageSeverity,
    SplitPolicy,
    StableScenarioIdentity,
    compute_dataset_digest,
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
from verifiednet.datasets.reader import DatasetReadError, LoadedDataset, read_dataset
from verifiednet.datasets.splitting import (
    ABSTENTION_POLICY_ID,
    DatasetSplitError,
    assign_example_split,
    assign_group_split,
    assign_splits,
    split_policy_id,
)
from verifiednet.datasets.verifier import (
    DatasetCheck,
    DatasetVerificationResult,
    verify_dataset,
)
from verifiednet.datasets.writer import (
    DatasetWriteError,
    WrittenDataset,
    write_dataset,
)

__all__ = [
    "ABSTENTION_POLICY_ID",
    "DATASET_EXPORT_VERSION",
    "DATASET_GENERATOR",
    "DATASET_INCOMPLETE_MARKER",
    "DATASET_MANIFEST_FILE",
    "DATASET_SPLITS_DIR",
    "EXPECTED_SPLIT_FILES",
    "SPLIT_BUCKET_COUNT",
    "SPLIT_FILE_BY_PARTITION",
    "AcceptedProjectionError",
    "ArtifactReference",
    "AssignedDatasetExample",
    "DatasetCheck",
    "DatasetDiscoveryError",
    "DatasetExample",
    "DatasetExampleKind",
    "DatasetExportError",
    "DatasetFileHash",
    "DatasetManifest",
    "DatasetPartition",
    "DatasetPartitionCounts",
    "DatasetReadError",
    "DatasetSplitError",
    "DatasetVerificationResult",
    "DatasetWriteError",
    "DiscoveredRun",
    "ExportedDataset",
    "LeakageAuditResult",
    "LeakageFinding",
    "LeakageFindingCode",
    "LeakageSeverity",
    "LoadedDataset",
    "ProjectionError",
    "RejectedProjectionError",
    "SplitPolicy",
    "StableScenarioIdentity",
    "UnsupportedRejectedSubtypeError",
    "WrittenDataset",
    "assign_example_split",
    "assign_group_split",
    "assign_splits",
    "audit_leakage",
    "build_dataset",
    "build_stable_identity",
    "compute_dataset_digest",
    "compute_example_id",
    "compute_group_id",
    "discover_verified_runs",
    "group_id_for_identity",
    "parse_split_bytes",
    "project_accepted_run",
    "project_rejected_run",
    "project_verified_run",
    "read_dataset",
    "split_policy_id",
    "verify_dataset",
    "write_dataset",
]
