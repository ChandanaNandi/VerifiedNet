"""Gate 16B contract tests: the experiment is one-run/one-checkpoint locked,
binds the v2 policy and unchanged target, pins the Gate 15 controls, and
touches no measurement contract or warm-start channel."""

from __future__ import annotations

import inspect

import pytest

from verifiednet.evaluation import diagnosis_prompt_template, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.experiment import (
    ControlledTrainingExperimentSpec,
    ExperimentRuntimeEnvelope,
    build_success_policy,
)
from verifiednet.training import (
    build_causal_lm_objective_policy,
    contract_aligned_input_template,
    contract_aligned_training_policy,
    diagnosis_target_template,
)

pytestmark = pytest.mark.contract

_TASK = diagnosis_task()


def test_gate16b_pinned_identities_are_exact() -> None:
    fp = "feat-4f792db1ef08ee5f"
    v2 = contract_aligned_input_template(
        task_id=_TASK.task_id, feature_policy_id=fp)
    target = diagnosis_target_template(task_id=_TASK.task_id)
    policy = contract_aligned_training_policy(
        task_id=_TASK.task_id, input_template=v2, target_template=target)
    assert v2.input_template_id == "traintmpl-c0513ab53036ae9b"
    assert policy.training_data_policy_id == "trainpolicy-336332a846b0f791"
    assert target.target_template_id == "traintgt-286e4ecdff06833e"
    assert build_causal_lm_objective_policy().objective_policy_id \
        == "objpol-e5f36da1a1292f3d"
    assert diagnosis_prompt_template().prompt_template_id \
        == "prompt-93808d932655a347"
    assert build_success_policy().success_policy_id \
        == "esucc-ab21b8d6e2ab7a70"
    assert build_default_interpretation_policy().interpretation_policy_id \
        == "interp-6a0d81d82b2b8d16"
    assert _TASK.scoring_policy_version == 1


def test_one_run_and_one_checkpoint_are_literal_locked() -> None:
    # the spec's run ceiling and the envelope's checkpoint ceiling are Literal 1
    fields = ControlledTrainingExperimentSpec.model_fields
    assert fields["maximum_training_runs"].annotation.__args__ == (1,)
    envelope_fields = ExperimentRuntimeEnvelope.model_fields
    assert envelope_fields["max_training_runs"].annotation.__args__ == (1,)
    assert envelope_fields["max_treatment_checkpoints"].annotation.__args__ \
        == (1,)


def test_real_execution_policy_forbids_retry_resume_and_multi_checkpoint(
) -> None:
    from verifiednet.training.bounds import RealTrainingExecutionPolicy

    fields = RealTrainingExecutionPolicy.model_fields
    assert fields["max_output_checkpoints"].annotation.__args__ == (1,)
    assert fields["retry_support"].annotation.__args__ == ("unsupported",)
    assert fields["resume_support"].annotation.__args__ == ("unsupported",)
    assert fields["checkpoint_timing"].annotation.__args__ == (
        "on_completion_only",)


def test_executor_has_no_warm_start_or_parent_checkpoint_channel() -> None:
    from verifiednet.training import RealTrainingExecutor
    from verifiednet.training.realckptstore import RealCheckpointLineage

    params = set(inspect.signature(
        RealTrainingExecutor.execute).parameters) - {"self"}
    # no checkpoint/parent/warm-start/resume parameter exists
    assert not (params & {"checkpoint_dir", "parent_checkpoint",
                          "warm_start", "resume_from", "init_checkpoint"})
    assert params == {"plan_dir", "corpus_dir", "authorization_dir",
                      "model_dir", "tokenizer_dir", "output_root",
                      "model_policy", "slice_policy", "execution_policy",
                      "objective_policy"}
    # a real checkpoint lineage forbids a parent checkpoint (Literal None)
    assert RealCheckpointLineage.model_fields[
        "parent_checkpoint_id"].annotation is type(None)


def test_v2_policy_requires_v2_input_and_unchanged_target() -> None:
    from verifiednet.training import diagnosis_input_template

    v1 = diagnosis_input_template(
        task_id=_TASK.task_id, feature_policy_id="feat-x")
    target = diagnosis_target_template(task_id=_TASK.task_id)
    with pytest.raises(ValueError, match="v2 input template"):
        contract_aligned_training_policy(
            task_id=_TASK.task_id, input_template=v1, target_template=target)


def test_gate16b_uses_the_frozen_gate15_experiment_format() -> None:
    # Gate 16B introduces NO new experiment/result model — it reuses the
    # Gate 15 store and spec verbatim (ADR-0033).
    from verifiednet import experiment as exp_pkg

    assert hasattr(exp_pkg, "ControlledTrainingExperimentSpec")
    assert hasattr(exp_pkg, "ControlledTrainingExperimentResult")
    assert hasattr(exp_pkg, "write_experiment_result")
    assert hasattr(exp_pkg, "preregister_experiment")
    # no Gate 16B-specific experiment model was added
    assert not any(name.lower().startswith("gate16")
                   for name in exp_pkg.__all__)


def test_measurement_contracts_are_untouched_by_gate16() -> None:
    import ast
    from pathlib import Path

    # the training layer (where Gate 16A/B changes live) imports no evaluation
    training = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
                / "training")
    for path in sorted(training.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert not module.startswith("verifiednet.evaluation"), path
