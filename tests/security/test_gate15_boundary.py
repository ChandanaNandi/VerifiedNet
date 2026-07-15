"""Gate 15 security proofs: no held-out truth or evaluation result can reach
the trainer, no network, no ML import in the experiment layer, no host
facts or credentials persisted, sources immutable, retraining impossible."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security


def test_experiment_chain_is_model_free_and_network_free(
    tmp_path: Path, experiment_pipeline, monkeypatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 15 offline chain must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    ctx = experiment_pipeline(tmp_path)
    assert ctx.result.outcome  # the whole chain ran
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_trainer_has_no_channel_for_evaluation_or_benchmark_facts() -> None:
    from verifiednet.training import RealTrainingExecutor, select_corpus_slice

    execute_parameters = set(inspect.signature(
        RealTrainingExecutor.execute).parameters) - {"self"}
    assert execute_parameters == {
        "plan_dir", "corpus_dir", "authorization_dir", "model_dir",
        "tokenizer_dir", "output_root", "model_policy", "slice_policy",
        "execution_policy", "objective_policy"}
    slice_parameters = set(inspect.signature(
        select_corpus_slice).parameters)
    assert slice_parameters == {"corpus_root", "max_example_count"}
    # and the package boundary makes evaluation facts unimportable there
    # (enforced by the AST guard in test_import_boundaries.py)


def test_no_retraining_channel_exists(tmp_path: Path,
                                      experiment_pipeline) -> None:
    ctx = experiment_pipeline(tmp_path)
    policy = ctx.trainctx.execution_policy
    assert policy.retry_support == "unsupported"
    assert policy.resume_support == "unsupported"
    assert policy.max_output_checkpoints == 1
    assert ctx.spec.maximum_training_runs == 1
    assert ctx.spec.runtime_envelope.max_training_runs == 1
    assert ctx.spec.runtime_envelope.max_treatment_checkpoints == 1


def test_experiment_artifacts_carry_no_host_or_environment_facts(
    tmp_path: Path, experiment_pipeline,
) -> None:
    import os

    ctx = experiment_pipeline(tmp_path)
    root = Path(str(ctx.written.root))
    for path in sorted(root.iterdir()):
        payload = path.read_text()
        assert str(tmp_path) not in payload, path.name
        assert os.getcwd() not in payload, path.name
        assert str(Path.home()) not in payload, path.name
        for variable in ("HOME", "USER", "HOSTNAME", "TOKEN", "PATH"):
            value = os.environ.get(variable, "")
            if len(value) > 3:
                assert value not in payload, (path.name, variable)


def test_sources_remain_verified_and_bound_after_the_experiment(
    tmp_path: Path, experiment_pipeline,
) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.training import (
        verify_real_checkpoint,
        verify_real_execution,
        verify_training_corpus,
        verify_training_plan,
    )

    ctx = experiment_pipeline(tmp_path)
    trainctx = ctx.trainctx
    # every upstream artifact still verifies after the experiment finalized
    assert verify_training_corpus(trainctx.corpus_root).verified is True
    assert verify_training_plan(trainctx.plan_dir).verified is True
    executions = sorted(
        (Path(str(trainctx.output_root)) / "real-training-executions")
        .iterdir())
    checkpoints = sorted(
        (Path(str(trainctx.output_root)) / "real-checkpoints").iterdir())
    assert len(executions) == 1 and len(checkpoints) == 1  # one run, one ckpt
    assert verify_real_execution(executions[0]).verified is True
    assert verify_real_checkpoint(checkpoints[0]).verified is True
    reloaded = load_prepared(trainctx.prectx.planctx.prepared_dir)
    assert reloaded.manifest.prepared_digest == \
        ctx.prepared.manifest.prepared_digest


def test_experiment_package_is_statically_ml_free() -> None:
    import ast

    package = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
               / "experiment")
    banned = {"torch", "transformers", "peft", "bitsandbytes", "accelerate",
              "safetensors"}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split(".")[0]}
            else:
                continue
            assert not names & banned, (path.name, names)
