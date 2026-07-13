"""Dataset models — the read-only PROJECTION of a verified run (Gate 6.1/6.2).

A ``DatasetExample`` is NOT truth. Truth is owned by the authoritative
``IncidentRecord`` inside the verified run directory (ADR-0018). An example is a
frozen, content-addressed pointer set + stable identity; it embeds NO copy of
evidence/transcript/ledger, NO model output, and NO inferred field.

Gate 6.2 adds, additively: the example KIND (accepted-fault vs abstention), the
stable scenario identity (so leakage grouping is independently re-checkable), a
deterministic ``SplitPolicy``, a separate ``AssignedDatasetExample`` that binds
one example to a partition without touching the source example's identity, and a
structured leakage-audit result.

All models are Pydantic v2, frozen, ``extra="forbid"``, versioned, fully typed
(no ``Any``, no mutable defaults).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel

_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REL_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class DatasetExampleKind(StrEnum):
    """Semantic kind of a dataset example (a stable property of the source run)."""

    ACCEPTED_FAULT = "accepted_fault"
    ABSTENTION = "abstention"


class DatasetPartition(StrEnum):
    """The partition an example belongs to. Abstention is NOT a train/dev/test
    split — it is the eval-only home of rejected (no-fault-label) runs."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    ABSTENTION = "abstention"


class LeakageSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LeakageFindingCode(StrEnum):
    GROUP_SPANS_SPLITS = "group_spans_splits"
    DUPLICATE_EXAMPLE_ID = "duplicate_example_id"
    DUPLICATE_SOURCE_RUN = "duplicate_source_run"
    GROUP_ID_MISMATCH = "group_id_mismatch"
    EXAMPLE_ID_MISMATCH = "example_id_mismatch"
    INVALID_ABSTENTION_ASSIGNMENT = "invalid_abstention_assignment"
    INVALID_ACCEPTED_ASSIGNMENT = "invalid_accepted_assignment"
    #: Informational sibling signals (never hard leakage failures).
    ORIENTATION_SIBLING = "orientation_sibling"
    PARAMETER_SIBLING = "parameter_sibling"


class ArtifactReference(StrictModel):
    """A verifiable pointer to one immutable file inside a verified run.

    Integrity is anchored by the enclosing ``DatasetExample.run_digest`` (the
    run is re-verified through the run index before the referenced file is
    read) — the reference itself embeds no copy and no per-file hash.
    """

    schema_version: Literal[1] = 1
    run_id: str
    relative_path: str

    @field_validator("relative_path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or not _REL_PATH_RE.match(value):
            raise ValueError(f"unsafe or absolute relative_path: {value!r}")
        if ".." in value.split("/"):
            raise ValueError(f"path traversal in relative_path: {value!r}")
        return value


class StableScenarioIdentity(StrictModel):
    """The STABLE identity that defines a leakage group (ADR-0018 §5).

    Contains ONLY timestamp-free, run-independent facts, so two runs of the same
    scenario share it exactly. ``group_id`` is a pure hash of this model.
    """

    schema_version: Literal[1] = 1
    template_id: str
    scenario_id: str
    target_node: str
    target_session: str
    parameters: dict[str, str | int]
    topology_hash: str
    backend: str

    @field_validator("topology_hash")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"topology_hash must be 64 lowercase hex: {value!r}")
        return value


class DatasetExample(StrictModel):
    """One verified run projected as a dataset example (references only)."""

    schema_version: Literal[1] = 1

    # -- identity ----------------------------------------------------------
    example_id: str  # unique per run: "ex-<hex16>"
    group_id: str  # leakage group (stable scenario identity): "grp-<hex16>"
    example_kind: DatasetExampleKind
    stable_identity: StableScenarioIdentity
    run_id: str
    run_digest: str
    template_id: str
    scenario_id: str
    topology_hash: str
    backend: str
    acceptance_status: Literal["accepted", "rejected"]

    # -- rejected-run SOURCE FACTS (never a fault-family label) ------------
    rejection_code: str | None = None
    failed_phase: str | None = None

    # -- references to authoritative artifacts (never embedded copies) -----
    incident_reference: ArtifactReference
    #: Points at the artifact that CONTAINS the model-free GroundTruth (the
    #: incident file) for accepted runs; ``None`` for rejected runs.
    ground_truth_reference: ArtifactReference | None = None
    transcript_reference: ArtifactReference
    ledger_reference: ArtifactReference
    baseline_reference: ArtifactReference
    onset_reference: ArtifactReference | None = None
    recovery_reference: ArtifactReference | None = None

    # -- provenance --------------------------------------------------------
    code_commit: str
    oracle_version: str | None = None
    source_index_digest: str

    @field_validator("example_id")
    @classmethod
    def _valid_example_id(cls, value: str) -> str:
        if not (value.startswith("ex-") and _HEX16_RE.match(value[3:])):
            raise ValueError(f"example_id must be 'ex-<16 hex>': {value!r}")
        return value

    @field_validator("group_id")
    @classmethod
    def _valid_group_id(cls, value: str) -> str:
        if not (value.startswith("grp-") and _HEX16_RE.match(value[4:])):
            raise ValueError(f"group_id must be 'grp-<16 hex>': {value!r}")
        return value

    @field_validator("run_digest", "topology_hash", "source_index_digest")
    @classmethod
    def _valid_hex(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"expected 64 lowercase hex: {value!r}")
        return value

    @model_validator(mode="after")
    def _kind_status_consistency(self) -> DatasetExample:
        if self.acceptance_status == "accepted":
            if self.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
                raise ValueError("accepted run must have example_kind accepted_fault")
            if self.ground_truth_reference is None:
                raise ValueError("accepted example requires a ground_truth_reference")
            if self.rejection_code is not None or self.failed_phase is not None:
                raise ValueError("accepted example must not carry rejection facts")
        else:
            if self.example_kind is not DatasetExampleKind.ABSTENTION:
                raise ValueError("rejected run must have example_kind abstention")
            if self.ground_truth_reference is not None:
                raise ValueError("rejected example must not reference ground truth")
            if self.onset_reference is not None or self.recovery_reference is not None:
                raise ValueError("rejected example must not reference onset/recovery evidence")
        return self


# ---------------------------------------------------------------------------
# Split policy + assignment (Gate 6.2)
# ---------------------------------------------------------------------------

#: Fixed bucket space for deterministic split assignment (integer, no floats).
SPLIT_BUCKET_COUNT = 10_000


class SplitPolicy(StrictModel):
    """A deterministic, randomness-free split policy (integer bucket space).

    Ratios are expressed as integer bucket counts out of ``SPLIT_BUCKET_COUNT``
    (so ``ratio = buckets / SPLIT_BUCKET_COUNT``) — exact, with no floating-point
    instability. The ``salt`` is explicit and versioned; there is no RNG, no
    global/implicit salt, no environment- or date-derived salt, and no Python
    ``hash()``.
    """

    schema_version: Literal[1] = 1
    algorithm_version: Literal[1] = 1
    salt: str = Field(min_length=1)
    train_buckets: int = Field(ge=0)
    validation_buckets: int = Field(ge=0)
    test_buckets: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid_ratios(self) -> SplitPolicy:
        total = self.train_buckets + self.validation_buckets + self.test_buckets
        if total != SPLIT_BUCKET_COUNT:
            raise ValueError(
                f"buckets must sum to {SPLIT_BUCKET_COUNT}, got {total}"
            )
        if self.train_buckets <= 0:
            raise ValueError("train_buckets must be positive")
        if self.test_buckets <= 0:
            raise ValueError("test_buckets must be positive")
        if self.validation_buckets <= 0:
            raise ValueError("validation_buckets must be positive")
        return self

    @property
    def policy_id(self) -> str:
        """Deterministic content id of this policy (salt + ratios + versions).

        Pure hash of the versioned policy content — no timestamp, environment,
        run list, dataset size, or iteration order. ``splitting.split_policy_id``
        delegates here so there is exactly one formula.
        """
        payload = {
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "salt": self.salt,
            "train_buckets": self.train_buckets,
            "validation_buckets": self.validation_buckets,
            "test_buckets": self.test_buckets,
        }
        return "split-" + sha256_canonical(payload)[:16]


class AssignedDatasetExample(StrictModel):
    """One example bound to a partition under a policy — the source example's
    identity is untouched (its ``example_id``/``group_id`` never change)."""

    schema_version: Literal[1] = 1
    example: DatasetExample
    partition: DatasetPartition
    split_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _kind_partition_consistency(self) -> AssignedDatasetExample:
        kind = self.example.example_kind
        if kind is DatasetExampleKind.ABSTENTION:
            if self.partition is not DatasetPartition.ABSTENTION:
                raise ValueError("abstention example must be assigned the abstention partition")
        else:  # ACCEPTED_FAULT
            if self.partition is DatasetPartition.ABSTENTION:
                raise ValueError("accepted example must not be assigned abstention")
        return self


# ---------------------------------------------------------------------------
# Leakage audit (Gate 6.2)
# ---------------------------------------------------------------------------


class LeakageFinding(StrictModel):
    schema_version: Literal[1] = 1
    code: LeakageFindingCode
    severity: LeakageSeverity
    group_id: str | None = None
    example_ids: tuple[str, ...] = Field(default_factory=tuple)
    detail: str = ""


class LeakageAuditResult(StrictModel):
    schema_version: Literal[1] = 1
    passed: bool
    findings: tuple[LeakageFinding, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _fail_closed(self) -> LeakageAuditResult:
        # An ERROR finding can never coexist with passed=True (fail closed).
        has_error = any(f.severity is LeakageSeverity.ERROR for f in self.findings)
        if self.passed and has_error:
            raise ValueError("audit cannot pass with ERROR-severity findings")
        return self

    @property
    def errors(self) -> tuple[LeakageFinding, ...]:
        return tuple(f for f in self.findings if f.severity is LeakageSeverity.ERROR)


# ---------------------------------------------------------------------------
# Exported dataset manifest + digest (Gate 6.2 Part 3)
# ---------------------------------------------------------------------------

#: On-disk export layout version. Bump ONLY when the serialized bytes/layout of
#: an exported dataset change (independent of the model ``schema_version``).
DATASET_EXPORT_VERSION = 1

#: The deterministic tool identity recorded on every manifest. A constant (never
#: a username, hostname, or timestamp) so exports stay reproducible.
DATASET_GENERATOR = "verifiednet.datasets.export"


class DatasetFileHash(StrictModel):
    """Per-file integrity record for one exported content file (splits only)."""

    schema_version: Literal[1] = 1
    relative_path: str
    sha256: str
    size: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or not _REL_PATH_RE.match(value):
            raise ValueError(f"unsafe or absolute relative_path: {value!r}")
        if ".." in value.split("/"):
            raise ValueError(f"path traversal in relative_path: {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _valid_hash(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"sha256 must be 64 lowercase hex: {value!r}")
        return value


class DatasetPartitionCounts(StrictModel):
    """Example counts per partition (deterministic, derived from the corpus)."""

    schema_version: Literal[1] = 1
    train: int = Field(ge=0)
    validation: int = Field(ge=0)
    test: int = Field(ge=0)
    abstention: int = Field(ge=0)

    @property
    def accepted_total(self) -> int:
        return self.train + self.validation + self.test

    @property
    def total(self) -> int:
        return self.accepted_total + self.abstention


def compute_dataset_digest(
    *,
    schema_version: int,
    export_version: int,
    dataset_version: str,
    generated_by: str,
    source_index_digest: str,
    split_policy_id: str,
    partition_counts: DatasetPartitionCounts,
    files: tuple[DatasetFileHash, ...],
) -> str:
    """Non-recursive digest over the exported dataset's content + config.

    Derived ONLY from the exported content (per-file hashes) and the deterministic
    build config (versions, dataset label, source index pin, split-policy id,
    counts). It never includes itself, a timestamp, a machine identity, an
    absolute path, or filesystem ordering (the file list is path-sorted). Two
    exports of identical source data therefore share one ``dataset_digest``.
    """
    payload = {
        "schema_version": schema_version,
        "export_version": export_version,
        "dataset_version": dataset_version,
        "generated_by": generated_by,
        "source_index_digest": source_index_digest,
        "split_policy_id": split_policy_id,
        "partition_counts": {
            "train": partition_counts.train,
            "validation": partition_counts.validation,
            "test": partition_counts.test,
            "abstention": partition_counts.abstention,
        },
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return sha256_canonical(payload)


class DatasetManifest(StrictModel):
    """The immutable corpus manifest of an exported dataset (Gate 6.2 Part 3).

    It fully describes one export: the dataset/label + schema/export versions, the
    pinned ``source_index_digest``, the exact ``SplitPolicy`` and its derived id,
    the accepted/rejected/partition counts, the per-file content hashes, and the
    self-validating ``dataset_digest``. The manifest carries NO timestamp,
    username, hostname, or machine identity — only deterministic build metadata.
    """

    schema_version: Literal[1] = 1
    export_version: Literal[1] = 1
    dataset_version: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    source_index_digest: str
    split_policy: SplitPolicy
    split_policy_id: str = Field(min_length=1)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    example_count: int = Field(ge=0)
    partition_counts: DatasetPartitionCounts
    files: tuple[DatasetFileHash, ...] = Field(default_factory=tuple)
    dataset_digest: str

    @field_validator("source_index_digest", "dataset_digest")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"expected 64 lowercase hex digest: {value!r}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> DatasetManifest:
        if self.split_policy_id != self.split_policy.policy_id:
            raise ValueError("split_policy_id does not match split_policy")
        if self.example_count != self.accepted_count + self.rejected_count:
            raise ValueError("example_count must equal accepted_count + rejected_count")
        if self.partition_counts.accepted_total != self.accepted_count:
            raise ValueError("train+validation+test must equal accepted_count")
        if self.partition_counts.abstention != self.rejected_count:
            raise ValueError("abstention count must equal rejected_count")
        # files must be path-sorted and unique (stable, deduplicated layout).
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("manifest files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("manifest files must be unique by path")
        # The digest is self-validating: it must equal a fresh recomputation.
        expected = compute_dataset_digest(
            schema_version=self.schema_version,
            export_version=self.export_version,
            dataset_version=self.dataset_version,
            generated_by=self.generated_by,
            source_index_digest=self.source_index_digest,
            split_policy_id=self.split_policy_id,
            partition_counts=self.partition_counts,
            files=self.files,
        )
        if self.dataset_digest != expected:
            raise ValueError("dataset_digest does not match manifest content")
        return self
