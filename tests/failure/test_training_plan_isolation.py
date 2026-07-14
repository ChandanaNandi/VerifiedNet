"""Gate 10B proofs: no real training, no execution/network, source immutability,
evaluation isolation, build-twice reproducibility, corpus-content isolation.

Planning a training run is pure bookkeeping: it must never load a model or
tokenizer, construct an optimizer, execute a gradient, write a checkpoint,
spawn a process, or open a network client — and it must leave every upstream
artifact byte-identical. These tests sabotage each of those avenues and prove
the entire Gate 10B pipeline (spec → plan → simulate → write → verify → read)
still succeeds.
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
    read_training_plan,
    verify_training_plan,
    write_training_plan,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

#: Libraries whose import would mean real training machinery is being loaded:
#: model/tokenizer loading (transformers, tokenizers, safetensors), gradient
#: execution and optimizer construction (torch), adapters (peft), quantized or
#: distributed execution (bitsandbytes, accelerate, deepspeed).
_ML_FRAMEWORKS = ("torch", "transformers", "tokenizers", "safetensors", "peft",
                  "accelerate", "bitsandbytes", "deepspeed")


class _TrapFinder(importlib.abc.MetaPathFinder):
    """Meta-path trap: any attempt to import training machinery fails loudly."""

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".", 1)[0]
        if root in _ML_FRAMEWORKS:
            raise AssertionError(
                f"Gate 10B attempted to import training machinery: {fullname}")
        return None


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _full_gate10b_pipeline(ctx, plans_root: Path):
    """Run every Gate 10B stage end-to-end and return the loaded artifact."""
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    sim = ctx.trainer.simulate(plan)
    written = write_training_plan(plan, plans_root, simulated_result=sim)
    result = verify_training_plan(written.root)
    assert result.verified is True, result.failures
    return read_training_plan(written.root)


def test_no_real_training_under_import_traps(tmp_path: Path, plan_pipeline) -> None:
    # Trap model loading, tokenizer loading, gradient execution, optimizer
    # construction, and checkpoint machinery at the import boundary: none of
    # it may even be imported while the whole Gate 10B pipeline runs.
    for name in _ML_FRAMEWORKS:
        assert name not in sys.modules, f"{name} already imported by the suite"
    trap = _TrapFinder()
    sys.meta_path.insert(0, trap)
    try:
        ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
        loaded = _full_gate10b_pipeline(ctx, tmp_path / "training-plans")
    finally:
        sys.meta_path.remove(trap)
    assert loaded.simulated_result is not None
    # No checkpoint was written: the plan directory holds ONLY the declared
    # JSON artifacts — no weights, no optimizer state, no checkpoint dirs.
    files = sorted(p.name for p in (tmp_path / "training-plans"
                                    / loaded.plan.training_plan_id).iterdir())
    assert files == ["manifest.json", "plan.json", "request.json",
                     "simulated-result.json"]
    assert loaded.simulated_result.produced_checkpoint is False


def test_planning_no_execution_no_network(
    tmp_path: Path, plan_pipeline, monkeypatch,
) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 10B must not execute or open a network client")

    import verifiednet.evaluation.inference as inference

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(inference, "OllamaBackend", _boom)

    loaded = _full_gate10b_pipeline(ctx, tmp_path / "training-plans")
    assert loaded.plan.training_plan_id.startswith("trainplan-")


def test_planning_does_not_mutate_sources(tmp_path: Path, plan_pipeline) -> None:
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

    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    # Materialize EVERY upstream artifact class, including evaluation and
    # benchmark outputs, so the fingerprint covers the full chain.
    prepared = load_prepared(ctx.prepared_dir)
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task,
                                    default_fault_family="bgp_remote_as_mismatch")
    write_evaluation(evaluate_prepared_corpus(prepared, baseline, task),
                     tmp_path / "evaluations")
    bench = run_benchmark(prepared, task=task, predictors=[
        baseline,
        FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch"),
    ])
    write_benchmark(bench, tmp_path / "benchmarks")

    roots = {
        "runs": Path(ctx.run_root),
        "dataset": Path(ctx.dataset_dir),
        "prepared": Path(ctx.prepared_dir),
        "evaluations": tmp_path / "evaluations",
        "benchmarks": tmp_path / "benchmarks",
        "training-corpus": Path(ctx.corpus_root),
    }
    before = {name: _fingerprint(root) for name, root in roots.items()}

    _full_gate10b_pipeline(ctx, tmp_path / "training-plans")

    after = {name: _fingerprint(root) for name, root in roots.items()}
    assert after == before  # every upstream stage byte-identical


def test_evaluation_isolation(tmp_path: Path, plan_pipeline) -> None:
    from verifiednet.common.canonical import canonical_json_bytes
    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        diagnosis_task,
        run_benchmark,
        write_benchmark,
    )

    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan_before = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)

    # Produce two DIFFERENT benchmark results (different predictor sets, hence
    # different rankings) against the same prepared corpus.
    prepared = load_prepared(ctx.prepared_dir)
    task = diagnosis_task()
    evidence = EvidenceRuleBaseline(task=task,
                                    default_fault_family="bgp_remote_as_mismatch")
    prior = FixedPriorBaseline(task=task,
                               fixed_fault_family="bgp_remote_as_mismatch")
    bench_a = run_benchmark(prepared, task=task, predictors=[evidence])
    bench_b = run_benchmark(prepared, task=task, predictors=[evidence, prior])
    assert bench_a.spec.benchmark_id != bench_b.spec.benchmark_id
    assert len(bench_a.ranking) != len(bench_b.ranking)  # rankings genuinely differ
    write_benchmark(bench_a, tmp_path / "bench-a")
    write_benchmark(bench_b, tmp_path / "bench-b")

    # The training spec and plan are structurally blind to all of it.
    spec_after = ctx.make_spec()
    plan_after = ctx.trainer.plan(spec=spec_after, corpus=ctx.descriptor)
    assert canonical_json_bytes(spec_after) == canonical_json_bytes(ctx.spec)
    assert canonical_json_bytes(plan_after) == canonical_json_bytes(plan_before)


def test_evaluation_side_data_never_reaches_the_plan(
    tmp_path: Path, plan_pipeline,
) -> None:
    # Same train examples, DIFFERENT abstention (evaluation-only) example:
    # the prepared corpus differs but the training-corpus CONTENT does not
    # (Gate 10A partition isolation). The corpus manifest's provenance pins
    # necessarily track the changed prepared source — so the corpus digest
    # (and every id derived from it) legitimately differs — but nothing about
    # the plan's substance may drift: same corpus identity, same example
    # count, same batch/step arithmetic, same hyperparameters.
    ctx_a = plan_pipeline(tmp_path / "a", accepted=_ACC, rejected=["run-rej"])
    ctx_b = plan_pipeline(tmp_path / "b", accepted=_ACC,
                          rejected=["run-rej-other"])
    assert ctx_a.manifest.training_corpus_id == ctx_b.manifest.training_corpus_id
    assert (ctx_a.manifest.training_corpus_digest
            != ctx_b.manifest.training_corpus_digest)  # provenance pin only
    plan_a = ctx_a.trainer.plan(spec=ctx_a.spec, corpus=ctx_a.descriptor)
    plan_b = ctx_b.trainer.plan(spec=ctx_b.spec, corpus=ctx_b.descriptor)
    # substance identical: the abstention change influenced no planned quantity
    assert plan_a.expected_example_count == plan_b.expected_example_count
    assert plan_a.batches_per_epoch == plan_b.batches_per_epoch
    assert plan_a.optimizer_steps == plan_b.optimizer_steps
    assert plan_a.effective_batch_size == plan_b.effective_batch_size
    dump_a = ctx_a.spec.model_dump(exclude={"training_corpus_digest",
                                            "training_spec_id"})
    dump_b = ctx_b.spec.model_dump(exclude={"training_corpus_digest",
                                            "training_spec_id"})
    assert dump_a == dump_b  # only the provenance pin and derived id differ


def test_corpus_content_isolation(tmp_path: Path, plan_pipeline) -> None:
    # A REAL corpus-content change (one fewer accepted run) must ripple through
    # every Gate 10B identity: spec, request, plan, and on-disk digest.
    ctx_full = plan_pipeline(tmp_path / "full", accepted=_ACC,
                             rejected=["run-rej"])
    ctx_small = plan_pipeline(tmp_path / "small", accepted=_ACC[:2],
                              rejected=["run-rej"])
    assert (ctx_full.manifest.training_corpus_digest
            != ctx_small.manifest.training_corpus_digest)
    assert ctx_full.spec.training_spec_id != ctx_small.spec.training_spec_id
    plan_full = ctx_full.trainer.plan(spec=ctx_full.spec,
                                      corpus=ctx_full.descriptor)
    plan_small = ctx_small.trainer.plan(spec=ctx_small.spec,
                                        corpus=ctx_small.descriptor)
    assert plan_full.request.request_id != plan_small.request.request_id
    assert plan_full.training_plan_id != plan_small.training_plan_id
    w_full = write_training_plan(plan_full, tmp_path / "p-full")
    w_small = write_training_plan(plan_small, tmp_path / "p-small")
    assert w_full.plan_digest != w_small.plan_digest


def test_build_twice_reproducibility(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan_1 = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    plan_2 = ctx.trainer.plan(spec=ctx.make_spec(), corpus=ctx.descriptor)
    sim_1 = ctx.trainer.simulate(plan_1)
    sim_2 = ctx.trainer.simulate(plan_2)
    w1 = write_training_plan(plan_1, tmp_path / "p1", simulated_result=sim_1)
    w2 = write_training_plan(plan_2, tmp_path / "p2", simulated_result=sim_2)
    assert w1.plan_digest == w2.plan_digest
    assert _fingerprint(w1.root) == _fingerprint(w2.root)  # byte-identical dirs
