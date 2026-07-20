"""Gate 19B contract tests: the balanced-corpus experiment spec differs from the
Gate 18B spec ONLY by the training-corpus identity (the source-selection policy),
every other frozen control is byte-equal, and the Gate 19A composition the corpus
comes from is exactly 20/20/20/4."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import DatasetFeaturesV2, FeaturePolicyV2
from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.evaluation.prompt import render_diagnosis_prompt_v2
from verifiednet.experiment import (
    ExperimentRuntimeEnvelope,
    build_experiment_spec,
    build_success_policy,
)
from verifiednet.training import boundary_aligned_objective_policy
from verifiednet.training.policy import render_training_input_v2
from verifiednet.training.selection import family_balanced_selection_policy

pytestmark = pytest.mark.contract

V2_FEAT = "feat-228b357dd9f256fa"
V2_PROMPT = "prompt-d4ff1ee1c637ea70"
OBJ = "objpol-7e6428964eae2db8"
SUCCESS = "esucc-ab21b8d6e2ab7a70"
_TASK = diagnosis_task()
_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)


def test_frozen_ids_unchanged() -> None:
    assert FeaturePolicyV2().policy_id == V2_FEAT
    assert boundary_aligned_objective_policy().objective_policy_id == OBJ
    assert build_success_policy().success_policy_id == SUCCESS


def _spec(*, training_corpus_id: str, training_corpus_digest: str):
    return build_experiment_spec(
        experiment_name="gate19-family-balanced-corpus", experiment_version=1,
        scientific_question="does a family-balanced training corpus reduce collapse?",
        hypothesis="balanced composition improves held-out macro accuracy",
        evaluation_corpus_id="evalcorpus-8c932345efc3e6e6",
        evaluation_corpus_digest="ecdig-e72927cc7d4b6fd0fa141462",
        readiness_assessment_id="ready-0b128bea7400a13f",
        source_prepared_digest="prep-" + "0" * 24,
        training_corpus_policy_id="trainpolicy-b74aac32d850a3b0",
        training_corpus_id=training_corpus_id,
        training_corpus_digest=training_corpus_digest,
        eligible_train_examples=128, training_example_cap=64,
        cap_rationale="the Gate 10F Literal envelope permits at most 64",
        model_approval_id="modelappr-" + "0" * 16,
        model_artifact_id="modelart-" + "0" * 16,
        tokenizer_artifact_id="tokart-" + "0" * 16,
        model_identifier="Qwen/Qwen2.5-0.5B-Instruct",
        model_revision="7ae557604adf67be50417f59c2c2f167def9a775",
        tokenizer_revision="7ae557604adf67be50417f59c2c2f167def9a775",
        training_spec_id="trainspec-" + "0" * 16,
        training_plan_id="trainplan-" + "0" * 24,
        training_plan_digest="plandig-" + "0" * 24,
        bounded_model_policy_id="bmodel-" + "0" * 16,
        objective_policy_id=OBJ, runtime_envelope=_ENVELOPE,
        prompt_template_id=V2_PROMPT,
        decoding=DecodingConfig(max_tokens=64),
        normalization_policy_id=_TASK.normalization.policy_id,
        scoring_policy_version=_TASK.scoring_policy_version,
        interpretation_policy_id=(
            build_default_interpretation_policy().interpretation_policy_id),
        success_policy=build_success_policy())


def test_gate19b_differs_from_gate18b_only_by_training_corpus() -> None:
    # Gate 18B: v2 prompt + natural first-64 corpus. Gate 19B: SAME v2 prompt and
    # data policy + family-balanced corpus. Holding all else, the experiment ids
    # differ only because of the training-corpus identity, and every other frozen
    # control is byte-equal.
    g18b = _spec(training_corpus_id="traincorpus-" + "b" * 16,
                 training_corpus_digest="traindig-" + "b" * 24)
    g19b = _spec(training_corpus_id="traincorpus-" + "c" * 16,
                 training_corpus_digest="traindig-" + "c" * 24)
    assert g18b.experiment_id != g19b.experiment_id
    for field in ("evaluation_corpus_id", "objective_policy_id",
                  "normalization_policy_id", "scoring_policy_version",
                  "model_identifier", "model_revision", "training_example_cap",
                  "prompt_template_id", "training_corpus_policy_id",
                  "success_criteria", "failure_criteria"):
        assert getattr(g18b, field) == getattr(g19b, field), field
    assert g18b.success_policy == g19b.success_policy
    assert g18b.runtime_envelope == g19b.runtime_envelope
    assert g19b.objective_policy_id == OBJ
    assert g19b.prompt_template_id == V2_PROMPT


def test_gate19b_corpus_composition_is_20_20_20_4() -> None:
    p = family_balanced_selection_policy()
    quotas = {q.fault_family: q.count for q in p.per_family_allocation}
    assert quotas == {"bgp_neighbor_removal": 20, "bgp_prefix_withdrawal": 20,
                      "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 20}
    assert sum(quotas.values()) == 64
    assert p.allowed_partition == "train"


def test_deployed_v2_prompt_equals_training_input_bytes() -> None:
    f = DatasetFeaturesV2(
        feature_policy_id=V2_FEAT, backend="frr-compose", topology_hash="a" * 64,
        bgp_worst_peer_state="idle", interface_any_admin_down=True,
        interface_any_oper_down=True, reachability_all_success=False,
        bgp_peer_removed=False, bgp_remote_as_changed=False,
        bgp_route_withdrawn=True)
    assert render_diagnosis_prompt_v2(f) == render_training_input_v2(f)
