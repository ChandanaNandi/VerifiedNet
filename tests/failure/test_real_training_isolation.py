"""Gate 10F proofs: no ML/exec/network on the stub path, source immutability,
evaluation isolation, sensitive-data exclusion, no-bypass."""

from __future__ import annotations

import hashlib
import importlib.abc
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from verifiednet.training import (
    read_real_execution,
    verify_real_checkpoint,
    verify_real_execution,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

_ML_FRAMEWORKS = ("torch", "transformers", "tokenizers", "safetensors", "peft",
                  "accelerate", "bitsandbytes", "deepspeed")


class _TrapFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in _ML_FRAMEWORKS:
            raise AssertionError(
                f"stub path attempted to import ML machinery: {fullname}")
        return None


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_stub_path_under_import_traps_and_sabotage(
    tmp_path: Path, realtrain_pipeline, monkeypatch,
) -> None:
    for name in _ML_FRAMEWORKS:
        assert name not in sys.modules, f"{name} already imported by the suite"

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("stub path must not execute or open the network")

    import verifiednet.evaluation.inference as inference

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(inference, "OllamaBackend", _boom)

    trap = _TrapFinder()
    sys.meta_path.insert(0, trap)
    try:
        ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
        written = ctx.execute()
    finally:
        sys.meta_path.remove(trap)
    assert verify_real_execution(written.root).verified is True
    ckpt = Path(ctx.output_root) / "real-checkpoints" / written.checkpoint_id
    assert verify_real_checkpoint(ckpt).verified is True


def test_real_execution_does_not_mutate_any_source(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        diagnosis_task,
        evaluate_prepared_corpus,
        run_benchmark,
        write_benchmark,
        write_evaluation,
    )

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    planctx = ctx.prectx.planctx
    prepared = load_prepared(planctx.prepared_dir)
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(
        task=task, default_fault_family="bgp_remote_as_mismatch")
    write_evaluation(evaluate_prepared_corpus(prepared, baseline, task),
                     tmp_path / "evaluations")
    write_benchmark(run_benchmark(prepared, task=task, predictors=[baseline]),
                    tmp_path / "benchmarks")

    roots = {
        "runs": Path(planctx.run_root),
        "dataset": Path(planctx.dataset_dir),
        "prepared": Path(planctx.prepared_dir),
        "evaluations": tmp_path / "evaluations",
        "benchmarks": tmp_path / "benchmarks",
        "training-corpus": Path(planctx.corpus_root),
        "plan": Path(ctx.plan_dir),
        "authorization": Path(ctx.auth_dir),
        "local-model": Path(ctx.model_dir),
    }
    before = {name: _fingerprint(root) for name, root in roots.items()}
    ctx.execute()
    after = {name: _fingerprint(root) for name, root in roots.items()}
    assert after == before  # weights may change in the CHECKPOINT, never here


def test_evaluation_isolation(tmp_path: Path, realtrain_pipeline) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        diagnosis_task,
        run_benchmark,
        write_benchmark,
    )

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    w1 = ctx.execute()

    prepared = load_prepared(ctx.prectx.planctx.prepared_dir)
    task = diagnosis_task()
    evidence = EvidenceRuleBaseline(
        task=task, default_fault_family="bgp_remote_as_mismatch")
    prior = FixedPriorBaseline(task=task,
                               fixed_fault_family="bgp_remote_as_mismatch")
    bench_a = run_benchmark(prepared, task=task, predictors=[evidence])
    bench_b = run_benchmark(prepared, task=task, predictors=[evidence, prior])
    assert len(bench_a.ranking) != len(bench_b.ranking)
    write_benchmark(bench_a, tmp_path / "bench-a")
    write_benchmark(bench_b, tmp_path / "bench-b")

    w2 = ctx.execute(output_root=tmp_path / "outputs-2")
    assert w2.execution_id == w1.execution_id
    assert w2.execution_digest == w1.execution_digest
    assert w2.checkpoint_id == w1.checkpoint_id
    assert _fingerprint(w2.root) == _fingerprint(w1.root)


def test_no_training_content_in_execution_artifacts(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    from verifiednet.training import load_training_corpus, load_training_pairs

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    pairs = load_training_pairs(ctx.corpus_root)
    corpus = load_training_corpus(ctx.corpus_root)

    # execution artifacts: no rendered inputs/targets/labels/group ids.
    # (the slice policy legitimately lists training-example ids for audit)
    blobs = b"\x00".join(p.read_bytes() for p in sorted(written.root.rglob("*"))
                         if p.is_file())
    for pair in pairs:
        assert pair.input_text.encode() not in blobs
        assert pair.target_text.encode() not in blobs
    for example in corpus.examples:
        assert example.trace.source_example_id.encode() not in blobs
        assert example.trace.source_group_id.encode() not in blobs
    from verifiednet.training import TRAINING_CANDIDATE_FAMILIES

    for family in TRAINING_CANDIDATE_FAMILIES:
        assert family.encode() not in blobs, family
    # checkpoint textual metadata too (weights may encode learned information
    # — documented as model memorization, out of scope for field scanning)
    ckpt = Path(ctx.output_root) / "real-checkpoints" / written.checkpoint_id
    textual = b"\x00".join(
        p.read_bytes() for p in sorted(ckpt.rglob("*"))
        if p.is_file() and p.suffix == ".json")
    for pair in pairs:
        assert pair.input_text.encode() not in textual
        assert pair.target_text.encode() not in textual


def test_sensitive_data_cannot_enter_artifacts(
    tmp_path: Path, realtrain_pipeline, monkeypatch,
) -> None:
    secrets = {
        "USER": "vn10f-secret-user-3a1b", "HOSTNAME": "vn10f-secret-host-8c2d",
        "HF_TOKEN": "vn10f-secret-hf-5e4f",
        "AWS_SECRET_ACCESS_KEY": "vn10f-secret-aws-7g6h",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    ckpt = Path(ctx.output_root) / "real-checkpoints" / written.checkpoint_id
    scanned = b"\x00".join(
        p.read_bytes()
        for root in (written.root, ckpt)
        for p in sorted(root.rglob("*"))
        if p.is_file() and (p.suffix in (".json", ".jsonl")))
    for key, value in secrets.items():
        assert value.encode() not in scanned, key


def test_no_bypass_even_with_patched_internals(
    tmp_path: Path, realtrain_pipeline, monkeypatch,
) -> None:
    # even a caller who monkeypatches the engine cannot skip authorization:
    # the orchestration revalidates BEFORE the engine ever runs
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    engine_ran = {"flag": False}

    class _SpyEngine:
        engine_id = "spy-engine"

        def run(self, **kwargs: object) -> object:
            engine_ran["flag"] = True
            raise AssertionError("engine must not run without authorization")

    from verifiednet.training import RealExecutionError, RealTrainingExecutor

    spy = RealTrainingExecutor(_SpyEngine())  # type: ignore[arg-type]
    with pytest.raises(RealExecutionError):
        spy.execute(
            plan_dir=ctx.plan_dir, corpus_dir=ctx.corpus_root,
            authorization_dir=tmp_path / "no-auth", model_dir=ctx.model_dir,
            tokenizer_dir=ctx.tokenizer_dir,
            output_root=tmp_path / "bypass-out",
            model_policy=ctx.model_policy, slice_policy=ctx.slice_policy,
            execution_policy=ctx.execution_policy,
            objective_policy=ctx.objective_policy)
    assert engine_ran["flag"] is False  # refused before the hot section
    # and there is no way to hand the executor a plan without the
    # authorization_dir argument at all:
    import inspect

    assert "authorization_dir" in inspect.signature(
        spy.execute).parameters


def test_failed_hot_section_persists_failed_artifact_without_checkpoint(
    tmp_path: Path, realtrain_pipeline, monkeypatch,
) -> None:
    from verifiednet.training import (
        ExecutionState,
        RealFailureClass,
        RealTrainingExecutor,
        StubTrainingEngine,
        TrainingEngineError,
    )

    class _FailingEngine(StubTrainingEngine):
        engine_id = "failing-stub-engine"

        def run(self, **kwargs: object) -> object:  # type: ignore[override]
            raise TrainingEngineError(
                RealFailureClass.NON_FINITE_LOSS, "synthetic failure")

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    failing = RealTrainingExecutor(_FailingEngine())
    written = failing.execute(
        plan_dir=ctx.plan_dir, corpus_dir=ctx.corpus_root,
        authorization_dir=ctx.auth_dir, model_dir=ctx.model_dir,
        tokenizer_dir=ctx.tokenizer_dir,
        output_root=tmp_path / "failed-out", model_policy=ctx.model_policy,
        slice_policy=ctx.slice_policy, execution_policy=ctx.execution_policy,
        objective_policy=ctx.objective_policy)
    assert written.final_state is ExecutionState.FAILED
    assert written.checkpoint_id is None
    assert verify_real_execution(written.root).verified is True
    loaded = read_real_execution(written.root)
    assert loaded.result.failure_class is RealFailureClass.NON_FINITE_LOSS
    assert loaded.result.produced_checkpoint_id is None
    # no checkpoint directory was published at all
    assert not (tmp_path / "failed-out" / "real-checkpoints").exists()
