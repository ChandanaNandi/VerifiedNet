"""Verified dataset engine — a read-only, deterministic projection of the Gate 5
verified run library (Gate 6.1/6.2).

This package is a CONSUMER of already-verified run artifacts, never a producer
of truth (ADR-0018). It never modifies a run, an IncidentRecord, evidence, a
transcript, a ledger, a manifest, or the run index; it never rewrites a run
digest; it never contacts Docker/FRR, re-runs the lab, re-derives evidence,
re-runs verification/oracle, infers labels, or invokes any model. Truth flows
one way: verified runs -> dataset projection -> (future) evaluation -> (future)
models, never backward.

Gate 6.1: dataset models, verified-run discovery, and pure projection (stable
leakage ``group_id`` + unique ``example_id``). Gate 6.2 Part 2: rejected runs as
EVAL-ONLY abstention examples, a deterministic ``SplitPolicy`` with integer
bucket splitting, and a fail-closed leakage audit. Gate 6.2 Part 3: the immutable
exported dataset (corpus ``DatasetManifest``, self-validating ``dataset_digest``,
writer/reader/verifier, reproducibility). Gate 6.2 Part 4: explicit
feature/label/trace separation (``DatasetFeatures``/``DatasetLabels``/
``DatasetTraceMetadata``), versioned feature/label policies, a feature-leakage
audit, and the persisted "prepared" corpus with a model-facing features-only
loader and an evaluator-facing loader.
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
from verifiednet.datasets.feature_leakage import (
    FORBIDDEN_FEATURE_KEYS,
    FeatureLeakageCode,
    FeatureLeakageError,
    FeatureLeakageFinding,
    FeatureLeakageResult,
    audit_feature_payload,
    audit_separated_example,
)
from verifiednet.datasets.features import (
    FEATURE_ALLOWLIST_V1,
    FEATURE_POLICY_VERSION,
    LABEL_POLICY_VERSION,
    AbstentionLabels,
    AcceptedLabels,
    DatasetFeatures,
    DatasetTraceMetadata,
    FeatureEvidenceRef,
    FeaturePolicy,
    LabelPolicy,
    SeparatedDatasetExample,
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
from verifiednet.datasets.prepared import (
    EXPECTED_PREPARED_FILES,
    PREPARED_MANIFEST_FILE,
    LoadedPrepared,
    PreparedError,
    PreparedExport,
    PreparedManifest,
    PreparedVerificationResult,
    WrittenPrepared,
    build_prepared,
    compute_prepared_digest,
    load_features,
    load_prepared,
    verify_prepared,
    write_prepared,
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
from verifiednet.datasets.separation import (
    SeparationError,
    separate_dataset,
    separate_example,
)
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
    "EXPECTED_PREPARED_FILES",
    "EXPECTED_SPLIT_FILES",
    "FEATURE_ALLOWLIST_V1",
    "FEATURE_POLICY_VERSION",
    "FORBIDDEN_FEATURE_KEYS",
    "LABEL_POLICY_VERSION",
    "PREPARED_MANIFEST_FILE",
    "SPLIT_BUCKET_COUNT",
    "SPLIT_FILE_BY_PARTITION",
    "AbstentionLabels",
    "AcceptedLabels",
    "AcceptedProjectionError",
    "ArtifactReference",
    "AssignedDatasetExample",
    "DatasetCheck",
    "DatasetDiscoveryError",
    "DatasetExample",
    "DatasetExampleKind",
    "DatasetExportError",
    "DatasetFeatures",
    "DatasetFileHash",
    "DatasetManifest",
    "DatasetPartition",
    "DatasetPartitionCounts",
    "DatasetReadError",
    "DatasetSplitError",
    "DatasetTraceMetadata",
    "DatasetVerificationResult",
    "DatasetWriteError",
    "DiscoveredRun",
    "ExportedDataset",
    "FeatureEvidenceRef",
    "FeatureLeakageCode",
    "FeatureLeakageError",
    "FeatureLeakageFinding",
    "FeatureLeakageResult",
    "FeaturePolicy",
    "LabelPolicy",
    "LeakageAuditResult",
    "LeakageFinding",
    "LeakageFindingCode",
    "LeakageSeverity",
    "LoadedDataset",
    "LoadedPrepared",
    "PreparedError",
    "PreparedExport",
    "PreparedManifest",
    "PreparedVerificationResult",
    "ProjectionError",
    "RejectedProjectionError",
    "SeparatedDatasetExample",
    "SeparationError",
    "SplitPolicy",
    "StableScenarioIdentity",
    "UnsupportedRejectedSubtypeError",
    "WrittenDataset",
    "WrittenPrepared",
    "assign_example_split",
    "assign_group_split",
    "assign_splits",
    "audit_feature_payload",
    "audit_leakage",
    "audit_separated_example",
    "build_dataset",
    "build_prepared",
    "build_stable_identity",
    "compute_dataset_digest",
    "compute_example_id",
    "compute_group_id",
    "compute_prepared_digest",
    "discover_verified_runs",
    "group_id_for_identity",
    "load_features",
    "load_prepared",
    "parse_split_bytes",
    "project_accepted_run",
    "project_rejected_run",
    "project_verified_run",
    "read_dataset",
    "separate_dataset",
    "separate_example",
    "split_policy_id",
    "verify_dataset",
    "verify_prepared",
    "write_dataset",
    "write_prepared",
]
