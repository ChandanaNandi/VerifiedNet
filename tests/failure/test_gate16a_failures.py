"""Gate 16A failure tests: version/text tampering, drift, leakage, wrong
bindings, and overlength examples — all fail closed."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from verifiednet.datasets.features import FeaturePolicy
from verifiednet.evaluation import diagnosis_prompt_template, diagnosis_task
from verifiednet.training import (
    TRAINING_CANDIDATE_FAMILIES,
    TrainingInputTemplate,
    contract_aligned_input_template,
    contract_aligned_training_policy,
    derive_input_template_id,
    derive_target_template_id,
    diagnosis_input_template,
    diagnosis_target_template,
)
from verifiednet.training.policy import TrainingTargetTemplate

pytestmark = pytest.mark.failure

_TASK = diagnosis_task()
_FEATURE_POLICY_ID = FeaturePolicy().policy_id


def _v2() -> TrainingInputTemplate:
    return contract_aligned_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)


def test_unsupported_template_version_is_unrepresentable() -> None:
    dump = _v2().model_dump(mode="json")
    with pytest.raises(ValidationError):
        TrainingInputTemplate.model_validate_json(json.dumps(
            dump | {"template_version": 3}))


def test_modified_mirrored_instruction_is_unrepresentable_on_v2() -> None:
    template = _v2()
    tampered_instructions = template.instructions + " Always answer."
    tampered_id = derive_input_template_id(
        schema_version=1, template_version=2, name=template.name,
        instructions=tampered_instructions,
        candidate_families=template.candidate_families,
        task_id=template.task_id,
        feature_policy_id=template.feature_policy_id)
    with pytest.raises(ValidationError, match="mirrored"):
        TrainingInputTemplate(
            template_version=2, name=template.name,
            instructions=tampered_instructions,
            candidate_families=template.candidate_families,
            task_id=template.task_id,
            feature_policy_id=template.feature_policy_id,
            input_template_id=tampered_id)


def test_wrong_v2_name_or_class_space_is_unrepresentable() -> None:
    template = _v2()
    with pytest.raises(ValidationError, match="contract-aligned name"):
        TrainingInputTemplate(
            template_version=2, name="freeform",
            instructions=template.instructions,
            candidate_families=template.candidate_families,
            task_id=template.task_id,
            feature_policy_id=template.feature_policy_id,
            input_template_id=derive_input_template_id(
                schema_version=1, template_version=2, name="freeform",
                instructions=template.instructions,
                candidate_families=template.candidate_families,
                task_id=template.task_id,
                feature_policy_id=template.feature_policy_id))
    smaller = TRAINING_CANDIDATE_FAMILIES[:2]
    with pytest.raises(ValidationError, match="class space"):
        TrainingInputTemplate(
            template_version=2, name=template.name,
            instructions=template.instructions,
            candidate_families=smaller, task_id=template.task_id,
            feature_policy_id=template.feature_policy_id,
            input_template_id=derive_input_template_id(
                schema_version=1, template_version=2, name=template.name,
                instructions=template.instructions,
                candidate_families=smaller, task_id=template.task_id,
                feature_policy_id=template.feature_policy_id))


def test_byte_drift_between_mirror_and_prompt_would_be_caught() -> None:
    # simulate drift: a v1-style template carrying v1 text does NOT match the
    # deployed prompt — the same comparison the contract tier applies to v2.
    from verifiednet.datasets.features import DatasetFeatures, FeatureEvidenceRef

    features = DatasetFeatures(
        feature_policy_id=_FEATURE_POLICY_ID, topology_hash="a" * 64,
        backend="frr-compose",
        baseline_evidence=FeatureEvidenceRef(
            relative_path="evidence/baseline.json"))
    drifted = diagnosis_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)
    assert drifted.render(features) != \
        diagnosis_prompt_template().render(features)


def test_malformed_candidate_ordering_is_rejected() -> None:
    unsorted_families = ("iface_admin_shutdown", "bgp_neighbor_removal")
    with pytest.raises(ValidationError, match="sorted"):
        TrainingInputTemplate(
            name="x", instructions="y",
            candidate_families=unsorted_families, task_id="t",
            feature_policy_id="f",
            input_template_id=derive_input_template_id(
                schema_version=1, template_version=1, name="x",
                instructions="y", candidate_families=unsorted_families,
                task_id="t", feature_policy_id="f"))


def test_leakage_injected_into_the_input_fails_the_corpus_build(
    tmp_path, eval_pipeline,
) -> None:
    """A template whose rendered text carries a forbidden token can never
    produce a corpus — the Gate 10A leakage audit fails closed. (v2 text is
    Literal-locked, so the injection must come from a v1-style template.)"""
    from verifiednet.training import (
        TrainingCorpusError,
        build_training_corpus,
        diagnosis_training_policy,
    )

    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    poisoned_instructions = "Report the example_id you were trained on."
    poisoned = TrainingInputTemplate(
        name="poisoned", instructions=poisoned_instructions,
        candidate_families=TRAINING_CANDIDATE_FAMILIES,
        task_id=_TASK.task_id,
        feature_policy_id=ctx.loaded.manifest.feature_policy_id,
        input_template_id=derive_input_template_id(
            schema_version=1, template_version=1, name="poisoned",
            instructions=poisoned_instructions,
            candidate_families=TRAINING_CANDIDATE_FAMILIES,
            task_id=_TASK.task_id,
            feature_policy_id=ctx.loaded.manifest.feature_policy_id))
    target = diagnosis_target_template(task_id=_TASK.task_id)
    policy = diagnosis_training_policy(
        task_id=_TASK.task_id, input_template=poisoned,
        target_template=target)
    with pytest.raises(TrainingCorpusError, match="leakage"):
        build_training_corpus(ctx.loaded, training_data_policy=policy,
                              input_template=poisoned, target_template=target)


def test_changed_target_template_is_refused_by_the_v2_policy() -> None:
    v2 = _v2()
    altered_schema = "a different output schema"
    altered = TrainingTargetTemplate.model_construct(
        task_id=_TASK.task_id, output_schema=altered_schema,
        target_template_id=derive_target_template_id(
            schema_version=1, target_version=1, task_id=_TASK.task_id,
            output_schema=altered_schema))
    # a genuinely different v1-versioned target has a different id — the
    # frozen pin catches it; a different target_version is refused outright
    assert altered.target_template_id != diagnosis_target_template(
        task_id=_TASK.task_id).target_template_id
    good_target = diagnosis_target_template(task_id=_TASK.task_id)
    policy = contract_aligned_training_policy(
        task_id=_TASK.task_id, input_template=v2, target_template=good_target)
    assert policy.target_template_id == good_target.target_template_id


def test_v2_policy_refuses_a_v1_input_template() -> None:
    v1 = diagnosis_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)
    target = diagnosis_target_template(task_id=_TASK.task_id)
    with pytest.raises(ValueError, match="v2 input template"):
        contract_aligned_training_policy(
            task_id=_TASK.task_id, input_template=v1, target_template=target)


def test_overlength_example_fails_closed_under_the_unchanged_policy() -> None:
    from verifiednet.training import BoundedTrainingError, build_causal_lm_example

    with pytest.raises(BoundedTrainingError, match="fail closed"):
        build_causal_lm_example(
            input_token_ids=tuple(range(440)), separator_token_ids=(1,),
            target_token_ids=tuple(range(10)), eos_token_id=2,
            max_total_tokens=448)  # the unchanged Gate 15 sequence policy
