"""Gate 10C proofs: no execution/network/ML frameworks, Gates 6-10B unchanged.

Simulated execution is pure bookkeeping. These tests run the ENTIRE Gate 10C
pipeline — execute, fail, resume, cancel, write, verify, read — with subprocess
APIs, the process runner, network access, and the inference backend sabotaged,
and with import traps armed against every ML framework; and they prove every
upstream artifact class (verified runs, dataset export, prepared corpus,
evaluations, benchmarks, training corpus, training plans) stays byte-identical.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from verifiednet.training import (
    read_training_execution,
    verify_training_execution,
    write_training_execution,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

_ML_FRAMEWORKS = ("torch", "transformers", "tokenizers", "safetensors", "peft",
                  "accelerate", "bitsandbytes", "deepspeed")


class _TrapFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in _ML_FRAMEWORKS:
            raise AssertionError(
                f"Gate 10C attempted to import training machinery: {fullname}")
        return None


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _full_gate10c_pipeline(ctx, executions_root: Path) -> None:
    """Exercise every Gate 10C lifecycle path end-to-end.

    Alternate outcomes of the SAME attempt (completed/failed/cancelled at
    retry 0) share one execution identity — one attempt has one authoritative
    outcome — so each history goes under its own root; the realistic
    failed → resumed pair shares a root because the retry has its own id.
    """
    completed = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    failed = ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=1)
    resumed = ctx.engine.resume(failed, ctx.plan)
    cancelled = ctx.engine.execute(ctx.plan, policy=ctx.policy,
                                   cancel_after_step=2)
    histories = (("completed", (completed,)), ("resumed", (failed, resumed)),
                 ("cancelled", (cancelled,)))
    for name, executions in histories:
        for ex in executions:
            w = write_training_execution(ex, executions_root / name)
            assert verify_training_execution(w.root).verified is True
            assert read_training_execution(w.root).execution == ex


def test_execution_under_import_traps_no_checkpoints(
    tmp_path: Path, execution_pipeline,
) -> None:
    for name in _ML_FRAMEWORKS:
        assert name not in sys.modules, f"{name} already imported by the suite"
    trap = _TrapFinder()
    sys.meta_path.insert(0, trap)
    try:
        ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
        _full_gate10c_pipeline(ctx, tmp_path / "training-executions")
    finally:
        sys.meta_path.remove(trap)
    # every execution directory holds ONLY the declared bookkeeping files —
    # no weights, no optimizer state, no checkpoints of any kind.
    exec_dirs = [p for p in (tmp_path / "training-executions").rglob("*")
                 if p.is_dir() and p.name.startswith("trainexec-")]
    assert len(exec_dirs) == 4
    for exec_dir in exec_dirs:
        assert sorted(p.name for p in exec_dir.iterdir()) == \
            ["events.jsonl", "manifest.json"]


def test_execution_no_subprocess_no_network(
    tmp_path: Path, execution_pipeline, monkeypatch,
) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 10C must not execute or open a network client")

    import verifiednet.evaluation.inference as inference

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(inference, "OllamaBackend", _boom)

    _full_gate10c_pipeline(ctx, tmp_path / "training-executions")


def test_execution_does_not_mutate_gates_6_to_10b(
    tmp_path: Path, execution_pipeline,
) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        diagnosis_task,
        evaluate_prepared_corpus,
        run_benchmark,
        write_benchmark,
        write_evaluation,
    )
    from verifiednet.training import write_training_plan

    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    # materialize EVERY upstream artifact class, Gate 6 through Gate 10B
    prepared = load_prepared(ctx.planctx.prepared_dir)
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(
        task=task, default_fault_family="bgp_remote_as_mismatch")
    write_evaluation(evaluate_prepared_corpus(prepared, baseline, task),
                     tmp_path / "evaluations")
    bench = run_benchmark(prepared, task=task, predictors=[
        baseline,
        FixedPriorBaseline(task=task,
                           fixed_fault_family="bgp_remote_as_mismatch"),
    ])
    write_benchmark(bench, tmp_path / "benchmarks")
    write_training_plan(ctx.plan, tmp_path / "training-plans")

    roots = {
        "runs": Path(ctx.planctx.run_root),
        "dataset": Path(ctx.planctx.dataset_dir),
        "prepared": Path(ctx.planctx.prepared_dir),
        "evaluations": tmp_path / "evaluations",
        "benchmarks": tmp_path / "benchmarks",
        "training-corpus": Path(ctx.planctx.corpus_root),
        "training-plans": tmp_path / "training-plans",
    }
    before = {name: _fingerprint(root) for name, root in roots.items()}

    _full_gate10c_pipeline(ctx, tmp_path / "training-executions")

    after = {name: _fingerprint(root) for name, root in roots.items()}
    assert after == before  # every upstream stage byte-identical


def test_execution_identity_rippled_by_plan_and_policy(
    tmp_path: Path, execution_pipeline,
) -> None:
    from verifiednet.training import StepBudget, build_execution_policy

    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    base = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    other_plan = ctx.make_plan(budget=StepBudget(max_optimizer_steps=5))
    other_exec = ctx.engine.execute(other_plan, policy=ctx.policy)
    assert other_exec.execution_id != base.execution_id
    other_policy = build_execution_policy(max_retries=1, allow_resume=True)
    assert ctx.engine.execute(
        ctx.plan, policy=other_policy).execution_id != base.execution_id
