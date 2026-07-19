"""Gate 18B — build the v2 evidence-observation training corpus.

Mirrors ``build_training_corpus`` but renders each supervised input from the
Gate 18A v2 observable features (resolved from the authoritative baseline/onset
evidence bundles) via the shared ``render_training_input_v2``, instead of the
v1/contract-aligned observation block. The target is the UNCHANGED canonical
diagnosis JSON. Every example passes the training leakage audit and is
content-addressed; the corpus binds the v3 evidence-observation input template
and the v2 feature policy. Never imports the evaluation package.
"""

from __future__ import annotations

from pathlib import Path

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.datasets.evidence_resolution import resolve_features_v2
from verifiednet.datasets.features import AcceptedLabels
from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.training.corpus import (
    SupervisedTrainingExample,
    SupervisedTrainingInput,
    SupervisedTrainingTarget,
    TrainingCorpus,
    TrainingCorpusError,
    TrainingTraceMetadata,
    audit_training_example,
    derive_training_corpus_id,
    derive_training_example_id,
)
from verifiednet.training.policy import (
    EVIDENCE_OBSERVATION_TEMPLATE_VERSION,
    TrainingDataPolicy,
    TrainingInputTemplate,
    TrainingTargetTemplate,
    render_training_input_v2,
)


def build_evidence_observation_corpus(
    prepared: LoadedPrepared,
    *,
    run_root: Path | str,
    feature_policy_v2: FeaturePolicyV2,
    training_data_policy: TrainingDataPolicy,
    input_template: TrainingInputTemplate,
    target_template: TrainingTargetTemplate,
) -> TrainingCorpus:
    """Build the v2 evidence-observation supervised training corpus.

    Deterministic; reads only the observable evidence bundles the sources point
    at; fails closed on a policy/template mismatch, a leaking example, or missing
    evidence.
    """
    if input_template.template_version != EVIDENCE_OBSERVATION_TEMPLATE_VERSION:
        raise TrainingCorpusError("input template is not the v3 contract")
    if training_data_policy.input_template_id != input_template.input_template_id:
        raise TrainingCorpusError("policy input_template_id does not match the template")
    if training_data_policy.target_template_id != target_template.target_template_id:
        raise TrainingCorpusError("policy target_template_id does not match the template")
    if input_template.feature_policy_id != feature_policy_v2.policy_id:
        raise TrainingCorpusError("input template binds a different feature policy")

    manifest = prepared.manifest
    examples: list[SupervisedTrainingExample] = []
    seen: set[str] = set()
    for source in prepared.examples:  # already example-id sorted
        if source.trace.partition is not DatasetPartition.TRAIN:
            continue
        if source.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        labels = source.labels
        if not isinstance(labels, AcceptedLabels):
            raise TrainingCorpusError(
                f"train example {source.trace.example_id} lacks accepted labels")
        if source.trace.example_id in seen:
            raise TrainingCorpusError(
                f"duplicate source example: {source.trace.example_id}")
        seen.add(source.trace.example_id)

        features_v2 = resolve_features_v2(
            source, run_root=run_root, policy=feature_policy_v2)
        rendered_input = render_training_input_v2(features_v2)
        rendered_target = target_template.render(labels.fault_family)
        trace = TrainingTraceMetadata(
            source_example_id=source.trace.example_id,
            source_group_id=source.trace.group_id,
            task_id=training_data_policy.task_id,
            training_data_policy_id=training_data_policy.training_data_policy_id,
            input_template_id=input_template.input_template_id,
            target_template_id=target_template.target_template_id,
            feature_policy_id=feature_policy_v2.policy_id,
            label_policy_id=manifest.label_policy_id,
            source_schema_version=source.schema_version,
        )
        example = SupervisedTrainingExample(
            training_example_id=derive_training_example_id(
                source_example_id=trace.source_example_id,
                task_id=trace.task_id,
                training_data_policy_id=trace.training_data_policy_id,
                input_template_id=trace.input_template_id,
                target_template_id=trace.target_template_id,
                rendered_input=rendered_input, rendered_target=rendered_target),
            input=SupervisedTrainingInput(text=rendered_input),
            target=SupervisedTrainingTarget(text=rendered_target),
            trace=trace)
        audit = audit_training_example(example)
        if not audit.passed:
            codes = ", ".join(sorted({f.code.value for f in audit.errors}))
            raise TrainingCorpusError(f"training leakage detected: {codes}")
        examples.append(example)

    ordered = tuple(sorted(examples, key=lambda e: e.trace.source_example_id))
    corpus_id = derive_training_corpus_id(
        task_id=training_data_policy.task_id,
        training_data_policy_id=training_data_policy.training_data_policy_id,
        input_template_id=input_template.input_template_id,
        target_template_id=target_template.target_template_id,
        training_example_ids=tuple(e.training_example_id for e in ordered))
    return TrainingCorpus(
        training_corpus_id=corpus_id, task_id=training_data_policy.task_id,
        policy=training_data_policy, input_template=input_template,
        target_template=target_template,
        source_prepared_digest=manifest.prepared_digest,
        source_dataset_digest=manifest.source_dataset_digest,
        feature_policy_id=feature_policy_v2.policy_id,
        label_policy_id=manifest.label_policy_id, examples=ordered)
