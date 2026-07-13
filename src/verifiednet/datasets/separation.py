"""Pure feature/label/metadata separation transform (Gate 6.2 Part 4).

``separate_example`` is a PURE function of one ``AssignedDatasetExample`` plus the
feature/label policies and the source dataset identity — no filesystem, network,
subprocess, randomness, or timestamps. It explicitly CONSTRUCTS the three
projections from an allowlist (never by deleting a blacklist from a full dump)
and then runs the feature-leakage audit, failing closed if any evaluator-only
data reached the model-visible features.

``separate_dataset`` maps a whole verified, loaded corpus and enforces a single
feature-policy id and a single label-policy id across the batch (fail closed on
mixed policies). Output ordering is deterministic (by ``example_id``).
"""

from __future__ import annotations

from collections.abc import Iterable

from verifiednet.common.errors import VerifiedNetError
from verifiednet.datasets.feature_leakage import audit_separated_example
from verifiednet.datasets.features import (
    AbstentionLabels,
    AcceptedLabels,
    DatasetFeatures,
    DatasetTraceMetadata,
    FeatureEvidenceRef,
    FeaturePolicy,
    LabelPolicy,
    SeparatedDatasetExample,
)
from verifiednet.datasets.models import (
    AssignedDatasetExample,
    DatasetExampleKind,
)


class SeparationError(VerifiedNetError):
    """An example could not be separated into features/labels/metadata."""


def _accepted_labels(
    ex: object, *, label_policy_id: str
) -> AcceptedLabels:
    # ``ex`` is a DatasetExample; validated by the caller for status == accepted.
    if ex.ground_truth_reference is None:  # type: ignore[attr-defined]
        raise SeparationError("accepted example has no ground-truth reference")
    if ex.recovery_reference is None:  # type: ignore[attr-defined]
        raise SeparationError("accepted example has no recovery reference")
    return AcceptedLabels(
        label_policy_id=label_policy_id,
        fault_family=ex.template_id,  # type: ignore[attr-defined]
        scenario_id=ex.scenario_id,  # type: ignore[attr-defined]
        ground_truth_reference=ex.ground_truth_reference,  # type: ignore[attr-defined]
        recovery_reference=ex.recovery_reference,  # type: ignore[attr-defined]
    )


def _abstention_labels(ex: object, *, label_policy_id: str) -> AbstentionLabels:
    if ex.rejection_code is None or ex.failed_phase is None:  # type: ignore[attr-defined]
        raise SeparationError("abstention example missing rejection facts")
    return AbstentionLabels(
        label_policy_id=label_policy_id,
        rejection_code=ex.rejection_code,  # type: ignore[attr-defined]
        failed_phase=ex.failed_phase,  # type: ignore[attr-defined]
    )


def separate_example(
    assigned: AssignedDatasetExample,
    *,
    feature_policy: FeaturePolicy,
    label_policy: LabelPolicy,
    dataset_version: str,
    source_index_digest: str,
) -> SeparatedDatasetExample:
    """Separate one assigned example; fail closed on invalid data or leakage."""
    ex = assigned.example
    accepted = ex.example_kind is DatasetExampleKind.ACCEPTED_FAULT

    # --- features (explicit allowlist construction) ----------------------
    onset_evidence: FeatureEvidenceRef | None = None
    if accepted:
        if not feature_policy.include_onset:
            raise SeparationError(
                "feature policy omits onset, but accepted examples require it"
            )
        if ex.onset_reference is None:
            raise SeparationError("accepted example has no onset reference")
        onset_evidence = FeatureEvidenceRef(
            relative_path=ex.onset_reference.relative_path
        )
    features = DatasetFeatures(
        feature_policy_id=feature_policy.policy_id,
        topology_hash=ex.topology_hash,
        backend=ex.backend,
        baseline_evidence=FeatureEvidenceRef(
            relative_path=ex.baseline_reference.relative_path
        ),
        onset_evidence=onset_evidence,
    )

    # --- labels (discriminated union) ------------------------------------
    labels: AcceptedLabels | AbstentionLabels
    if accepted:
        labels = _accepted_labels(ex, label_policy_id=label_policy.policy_id)
    else:
        labels = _abstention_labels(ex, label_policy_id=label_policy.policy_id)

    # --- trace metadata (never model-visible) ----------------------------
    trace = DatasetTraceMetadata(
        example_id=ex.example_id,
        group_id=ex.group_id,
        run_id=ex.run_id,
        run_digest=ex.run_digest,
        example_kind=ex.example_kind,
        partition=assigned.partition,
        split_policy_id=assigned.split_policy_id,
        dataset_version=dataset_version,
        source_index_digest=source_index_digest,
        example_schema_version=ex.schema_version,
        incident_reference=ex.incident_reference,
    )

    separated = SeparatedDatasetExample(features=features, labels=labels, trace=trace)

    # Fail closed if anything evaluator-only reached the model-visible features.
    audit = audit_separated_example(separated)
    if not audit.passed:
        paths = ", ".join(f.json_path for f in audit.errors)
        raise SeparationError(f"feature leakage detected: {paths}")
    return separated


def separate_dataset(
    assigned_examples: Iterable[AssignedDatasetExample],
    *,
    feature_policy: FeaturePolicy,
    label_policy: LabelPolicy,
    dataset_version: str,
    source_index_digest: str,
) -> tuple[SeparatedDatasetExample, ...]:
    """Separate a whole corpus deterministically (sorted by ``example_id``)."""
    separated = [
        separate_example(
            a,
            feature_policy=feature_policy,
            label_policy=label_policy,
            dataset_version=dataset_version,
            source_index_digest=source_index_digest,
        )
        for a in assigned_examples
    ]
    # Single-policy invariant across the batch (fail closed on mixed policies).
    feat_ids = {s.features.feature_policy_id for s in separated}
    label_ids = {s.labels.label_policy_id for s in separated}
    if len(feat_ids) > 1:
        raise SeparationError(f"mixed feature policies: {sorted(feat_ids)}")
    if len(label_ids) > 1:
        raise SeparationError(f"mixed label policies: {sorted(label_ids)}")
    return tuple(sorted(separated, key=lambda s: s.trace.example_id))
