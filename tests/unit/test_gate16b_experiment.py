"""Gate 16B unit tests: the contract-aligned-conditioning experiment binds
the v2 policy, differs from a v1-bound spec only by the intended variable,
selects the same ordered sources, and classifies outcomes honestly."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.experiment import (
    ExperimentRuntimeEnvelope,
    build_experiment_spec,
    build_success_policy,
)
from verifiednet.training import (
    contract_aligned_input_template,
    contract_aligned_training_policy,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
)

pytestmark = pytest.mark.unit

# Pinned Gate 16A / Gate 15 identities (contract-checked at import elsewhere).
V2_INPUT_TEMPLATE_ID = "traintmpl-c0513ab53036ae9b"
V2_POLICY_ID = "trainpolicy-336332a846b0f791"
V1_POLICY_ID = "trainpolicy-47cd597b27119125"
TARGET_TEMPLATE_ID = "traintgt-286e4ecdff06833e"
OBJECTIVE_POLICY_ID = "objpol-e5f36da1a1292f3d"
PROMPT_TEMPLATE_ID = "prompt-93808d932655a347"
SUCCESS_POLICY_ID = "esucc-ab21b8d6e2ab7a70"
V3_CORPUS_ID = "evalcorpus-8c932345efc3e6e6"
V3_CORPUS_DIGEST = "ecdig-e72927cc7d4b6fd0fa141462"
READINESS_ID = "ready-0b128bea7400a13f"
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"

_TASK = diagnosis_task()

#: The exact Gate 15 training envelope — reused byte-for-byte.
GATE15_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)


def _spec(*, policy_id: str, corpus_id: str, corpus_digest: str,
          experiment_name: str = "gate16-contract-aligned-conditioning"):
    return build_experiment_spec(
        experiment_name=experiment_name, experiment_version=1,
        scientific_question="does contract-aligned conditioning help?",
        hypothesis="v2 conditioning increases valid structured output",
        evaluation_corpus_id=V3_CORPUS_ID,
        evaluation_corpus_digest=V3_CORPUS_DIGEST,
        readiness_assessment_id=READINESS_ID,
        source_prepared_digest="prep-" + "0" * 24,
        training_corpus_policy_id=policy_id,
        training_corpus_id=corpus_id, training_corpus_digest=corpus_digest,
        eligible_train_examples=128, training_example_cap=64,
        cap_rationale="the Gate 10F Literal envelope permits at most 64",
        model_approval_id="modelappr-" + "0" * 16,
        model_artifact_id="modelart-" + "0" * 16,
        tokenizer_artifact_id="tokart-" + "0" * 16,
        model_identifier=MODEL_ID, model_revision=MODEL_REVISION,
        tokenizer_revision=MODEL_REVISION,
        training_spec_id="trainspec-" + "0" * 16,
        training_plan_id="trainplan-" + "0" * 24,
        training_plan_digest="plandig-" + "0" * 24,
        bounded_model_policy_id="bmodel-" + "0" * 16,
        objective_policy_id=OBJECTIVE_POLICY_ID,
        runtime_envelope=GATE15_ENVELOPE,
        prompt_template_id=PROMPT_TEMPLATE_ID,
        decoding=DecodingConfig(max_tokens=64),
        normalization_policy_id=_TASK.normalization.policy_id,
        scoring_policy_version=_TASK.scoring_policy_version,
        interpretation_policy_id=(
            build_default_interpretation_policy().interpretation_policy_id),
        success_policy=build_success_policy())


def test_gate16b_spec_binds_the_v2_policy_and_is_one_run_locked() -> None:
    spec = _spec(policy_id=V2_POLICY_ID, corpus_id="traincorpus-" + "a" * 16,
                 corpus_digest="traindig-" + "a" * 24)
    assert spec.experiment_id.startswith("exp-")
    assert spec.training_corpus_policy_id == V2_POLICY_ID
    assert spec.objective_policy_id == OBJECTIVE_POLICY_ID
    assert spec.prompt_template_id == PROMPT_TEMPLATE_ID
    assert spec.maximum_training_runs == 1
    assert spec.runtime_envelope.max_training_runs == 1
    assert spec.runtime_envelope.max_treatment_checkpoints == 1
    assert spec.readiness_outcome == "ready_for_controlled_experiment"


def test_experiment_id_is_deterministic() -> None:
    a = _spec(policy_id=V2_POLICY_ID, corpus_id="traincorpus-" + "a" * 16,
              corpus_digest="traindig-" + "a" * 24)
    b = _spec(policy_id=V2_POLICY_ID, corpus_id="traincorpus-" + "a" * 16,
              corpus_digest="traindig-" + "a" * 24)
    assert a == b
    assert a.experiment_id == b.experiment_id


def test_only_the_conditioning_variable_changes_the_experiment_id() -> None:
    """A v2-bound spec differs from an otherwise-identical v1-bound spec ONLY
    through the training-policy id (and the corpus id/digest that the v2
    conditioning produces) — every frozen control is byte-equal."""
    v1_spec = _spec(policy_id=V1_POLICY_ID,
                    corpus_id="traincorpus-" + "b" * 16,
                    corpus_digest="traindig-" + "b" * 24)
    v2_spec = _spec(policy_id=V2_POLICY_ID,
                    corpus_id="traincorpus-" + "a" * 16,
                    corpus_digest="traindig-" + "a" * 24)
    assert v1_spec.experiment_id != v2_spec.experiment_id
    # every frozen control is identical
    for field in ("evaluation_corpus_id", "evaluation_corpus_digest",
                  "readiness_assessment_id", "objective_policy_id",
                  "prompt_template_id", "normalization_policy_id",
                  "scoring_policy_version", "model_identifier",
                  "model_revision", "tokenizer_revision",
                  "training_example_cap", "primary_metrics",
                  "secondary_metrics", "success_criteria",
                  "failure_criteria"):
        assert getattr(v1_spec, field) == getattr(v2_spec, field), field
    assert v1_spec.success_policy == v2_spec.success_policy
    assert v1_spec.runtime_envelope == v2_spec.runtime_envelope
    assert v1_spec.decoding == v2_spec.decoding


def test_same_64_source_equality_between_v1_and_v2(
    tmp_path: Path, gate14b_corpus_pipeline, gate16_corpora,
) -> None:
    """The Gate 16B corpus selects EXACTLY the ordered sources Gate 15's v1
    corpus did — the same first-N cap over the same prepared chain — with
    identical targets and trace bindings; only the input differs."""
    ctx, _accepted, _rejected = gate14b_corpus_pipeline(tmp_path, runs_cap=1)
    v1, v2 = gate16_corpora(ctx.loaded, max_example_count=64)
    v1_sources = [e.trace.source_example_id for e in v1.examples]
    v2_sources = [e.trace.source_example_id for e in v2.examples]
    assert v1_sources == v2_sources  # exact ordered equality
    assert len(v1_sources) == len(set(v1_sources))
    for left, right in zip(v1.examples, v2.examples, strict=True):
        assert left.target.text == right.target.text
        assert left.trace.source_group_id == right.trace.source_group_id
        assert left.trace.target_template_id == right.trace.target_template_id
        assert left.trace.feature_policy_id == right.trace.feature_policy_id
        assert left.trace.label_policy_id == right.trace.label_policy_id
        assert left.input.text != right.input.text  # the ONLY difference
        assert left.trace.input_template_id != right.trace.input_template_id
    # the v2 corpus binds the v2 template; the target binding is unchanged
    assert v2.policy.input_template_id == V2_INPUT_TEMPLATE_ID_FOR(ctx.loaded)
    assert v2.policy.target_template_id == TARGET_TEMPLATE_ID
    assert v2.training_corpus_id != v1.training_corpus_id


def V2_INPUT_TEMPLATE_ID_FOR(prepared) -> str:
    return contract_aligned_input_template(
        task_id=_TASK.task_id,
        feature_policy_id=prepared.manifest.feature_policy_id
    ).input_template_id


def test_no_held_out_partition_enters_the_v2_training_corpus(
    tmp_path: Path, gate14b_corpus_pipeline, gate16_corpora,
) -> None:
    from verifiednet.datasets.models import (
        DatasetExampleKind,
        DatasetPartition,
    )

    ctx, _a, _r = gate14b_corpus_pipeline(tmp_path, runs_cap=1)
    _v1, v2 = gate16_corpora(ctx.loaded, max_example_count=64)
    by_example = {e.trace.example_id: e for e in ctx.loaded.examples}
    for example in v2.examples:
        source = by_example[example.trace.source_example_id]
        assert source.trace.partition is DatasetPartition.TRAIN
        assert source.trace.example_kind is DatasetExampleKind.ACCEPTED_FAULT


def test_v2_policy_builder_refuses_a_changed_target() -> None:
    from verifiednet.evaluation import diagnosis_task

    task_id = diagnosis_task().task_id
    v2 = contract_aligned_input_template(
        task_id=task_id, feature_policy_id="feat-x")
    target = diagnosis_target_template(task_id=task_id)
    policy = contract_aligned_training_policy(
        task_id=task_id, input_template=v2, target_template=target)
    assert policy.target_template_id == TARGET_TEMPLATE_ID
    # a v1 input template is refused by the v2 policy builder (Gate 16A)
    v1 = diagnosis_input_template(task_id=task_id, feature_policy_id="feat-x")
    with pytest.raises(ValueError, match="v2 input template"):
        contract_aligned_training_policy(
            task_id=task_id, input_template=v1, target_template=target)
    _ = diagnosis_training_policy  # referenced for symmetry with v1 path
