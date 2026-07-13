"""Feature / label / trace-metadata separation models (Gate 6.2 Part 4).

An exported ``AssignedDatasetExample`` mixes three concerns that MUST NOT reach a
model together: the model-visible input FEATURES, the evaluation-only LABELS
(authoritative expected answer), and non-model TRACE METADATA (identity,
provenance, split). Part 4 defines three explicit, immutable projections plus a
``SeparatedDatasetExample`` that binds them with strong cross-kind validators.

Design rules (see ``docs/architecture/gate6/feature-label-separation.md``):

* Features are an EXPLICIT ALLOWLIST — the model type literally has only the
  permitted fields. Features are never derived by deleting a blacklist from a
  full dump.
* Labels are a discriminated union (accepted vs abstention) so an invalid
  cross-kind combination cannot even be constructed.
* Trace metadata is a separate type; the model-facing loader never returns it.
* Evidence is referenced by a GENERIC role path only (``FeatureEvidenceRef``) —
  never by ``run_id``, ``run_digest``, or any bookkeeping identity — so no
  identity leaks into the model-visible payload. The evaluator joins the role
  path with ``trace.run_id`` to resolve the actual artifact.

All models are Pydantic v2, frozen, ``extra="forbid"``, versioned, fully typed.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.models import (
    ArtifactReference,
    DatasetExampleKind,
    DatasetPartition,
)
from verifiednet.schemas.base import StrictModel

#: Policy versions. Bump when the feature/label CONTRACT changes; the derived
#: policy id changes with the version + configuration, never with time/env.
FEATURE_POLICY_VERSION = 1
LABEL_POLICY_VERSION = 1

#: The canonical v1 model-visible feature allowlist (the audit surface). These
#: are exactly the field names ``DatasetFeatures`` exposes — nothing else may be
#: model-visible. Kept as an explicit constant so a reviewer can audit it.
FEATURE_ALLOWLIST_V1: tuple[str, ...] = (
    "backend",
    "baseline_evidence",
    "onset_evidence",
    "topology_hash",
)

_TRAINABLE = frozenset(
    {DatasetPartition.TRAIN, DatasetPartition.VALIDATION, DatasetPartition.TEST}
)


class FeatureEvidenceRef(StrictModel):
    """A model-visible evidence pointer carrying ONLY a generic role path.

    It deliberately omits ``run_id``/``run_digest`` so no identity leaks into the
    features; the evaluator resolves the concrete artifact by joining this path
    with ``DatasetTraceMetadata.run_id``.
    """

    schema_version: Literal[1] = 1
    relative_path: str


class FeaturePolicy(StrictModel):
    """A versioned, deterministic feature policy (the model-visible contract)."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    allowed_fields: tuple[str, ...] = FEATURE_ALLOWLIST_V1
    include_onset: bool = True

    @model_validator(mode="after")
    def _canonical_allowlist(self) -> FeaturePolicy:
        if tuple(sorted(self.allowed_fields)) != tuple(self.allowed_fields):
            raise ValueError("allowed_fields must be sorted")
        if self.allowed_fields != FEATURE_ALLOWLIST_V1:
            raise ValueError("allowed_fields must equal the canonical v1 allowlist")
        return self

    @property
    def policy_id(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "allowed_fields": list(self.allowed_fields),
            "include_onset": self.include_onset,
        }
        return "feat-" + sha256_canonical(payload)[:16]


class LabelPolicy(StrictModel):
    """A versioned, deterministic label policy (the evaluation-target contract)."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1

    @property
    def policy_id(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
        }
        return "label-" + sha256_canonical(payload)[:16]


class DatasetFeatures(StrictModel):
    """The ONLY values a future model may receive (explicit allowlist).

    Contains permitted inference-time context (``topology_hash``, ``backend``)
    and generic evidence pointers. It carries NO fault-family label, ground
    truth, diagnosis, recovery, rejection code/phase, identity, split, or
    bookkeeping digest. Abstention examples legitimately have no onset evidence.
    """

    schema_version: Literal[1] = 1
    feature_policy_id: str = Field(min_length=1)
    topology_hash: str
    backend: str
    baseline_evidence: FeatureEvidenceRef
    onset_evidence: FeatureEvidenceRef | None = None


class AcceptedLabels(StrictModel):
    """The authoritative evaluation target for an accepted fault example."""

    schema_version: Literal[1] = 1
    kind: Literal["accepted_fault"] = "accepted_fault"
    label_policy_id: str = Field(min_length=1)
    fault_family: str = Field(min_length=1)  # = source template_id (diagnosis target)
    scenario_id: str = Field(min_length=1)
    ground_truth_reference: ArtifactReference
    recovery_reference: ArtifactReference


class AbstentionLabels(StrictModel):
    """The evaluation target for a rejected precondition (abstention) example.

    The target IS abstention; it carries only authoritative machine facts and
    NEVER a fault-family, healthy, negative, or ``no fault`` label.
    """

    schema_version: Literal[1] = 1
    kind: Literal["abstention"] = "abstention"
    label_policy_id: str = Field(min_length=1)
    expected_outcome: Literal["abstain"] = "abstain"
    rejection_code: str = Field(min_length=1)
    failed_phase: str = Field(min_length=1)


DatasetLabels = Annotated[
    AcceptedLabels | AbstentionLabels, Field(discriminator="kind")
]


class DatasetTraceMetadata(StrictModel):
    """Non-model provenance/audit/orchestration metadata (never model-visible)."""

    schema_version: Literal[1] = 1
    example_id: str
    group_id: str
    run_id: str
    run_digest: str
    example_kind: DatasetExampleKind
    partition: DatasetPartition
    split_policy_id: str
    dataset_version: str
    source_index_digest: str
    example_schema_version: int
    incident_reference: ArtifactReference


class SeparatedDatasetExample(StrictModel):
    """One example split into features + labels + trace, with strong validators."""

    schema_version: Literal[1] = 1
    features: DatasetFeatures
    labels: AcceptedLabels | AbstentionLabels = Field(discriminator="kind")
    trace: DatasetTraceMetadata

    @model_validator(mode="after")
    def _consistent(self) -> SeparatedDatasetExample:
        kind = self.trace.example_kind
        if kind is DatasetExampleKind.ACCEPTED_FAULT:
            if not isinstance(self.labels, AcceptedLabels):
                raise ValueError("accepted example requires accepted labels")
            if self.trace.partition not in _TRAINABLE:
                raise ValueError("accepted example must be in a train/val/test split")
            if self.features.onset_evidence is None:
                raise ValueError("accepted example requires onset evidence in features")
        else:  # ABSTENTION
            if not isinstance(self.labels, AbstentionLabels):
                raise ValueError("abstention example requires abstention labels")
            if self.trace.partition is not DatasetPartition.ABSTENTION:
                raise ValueError("abstention example must be in the abstention partition")
            if self.features.onset_evidence is not None:
                raise ValueError("abstention example must not carry onset evidence")
        return self
