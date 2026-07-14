"""Gate 10E proofs: no training/ML/exec/network, immutability, isolation,
environment sensitivity, sensitive-data exclusion.

Preflight is observation and bookkeeping over verified artifacts. These tests
run the ENTIRE offline preflight pipeline — verify plan, verify corpus,
inspect fake environment, resolve fake artifacts, assess capability, write,
verify, read — under import traps and subprocess/network sabotage, and prove
that no upstream artifact changes, evaluation cannot reach authorization,
each capability input ripples into the evidence identity, and host secrets
cannot appear in persisted evidence.
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
    FakeEnvironmentProbe,
    FakeModelArtifactResolver,
    build_device_capability,
    read_training_authorization,
    verify_training_authorization,
    write_training_authorization,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

_ML_FRAMEWORKS = ("torch", "transformers", "tokenizers", "safetensors", "peft",
                  "accelerate", "bitsandbytes", "deepspeed")


class _TrapFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in _ML_FRAMEWORKS:
            raise AssertionError(
                f"Gate 10E attempted to import training machinery: {fullname}")
        return None


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _full_gate10e_pipeline(ctx, authorizations_root: Path):
    snapshot_probe = ctx.backend.inspect_environment()
    auth, snapshot = ctx.backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    assert snapshot == snapshot_probe
    written = write_training_authorization(auth, snapshot, authorizations_root)
    assert verify_training_authorization(written.root).verified is True
    loaded = read_training_authorization(written.root)
    assert loaded.authorization == auth
    return auth, snapshot, written


def test_pipeline_under_import_and_training_traps(
    tmp_path: Path, preflight_pipeline, monkeypatch,
) -> None:
    # Import traps on every ML framework AND no-training traps on every
    # avenue real training would need: the whole pipeline must still pass.
    for name in _ML_FRAMEWORKS:
        assert name not in sys.modules, f"{name} already imported by the suite"

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 10E must not touch training machinery")

    # gradient/optimizer/scheduler/weight/checkpoint/model-loading traps:
    # nothing in verifiednet may even reference these names at preflight time
    import verifiednet.training.backend as backend_mod
    import verifiednet.training.preflight as preflight_mod

    for mod in (backend_mod, preflight_mod):
        for attr in ("load_model", "load_tokenizer", "backward", "step",
                     "save_checkpoint", "train"):
            assert not hasattr(mod, attr), (mod.__name__, attr)
    monkeypatch.setattr("importlib.import_module", _boom)

    trap = _TrapFinder()
    sys.meta_path.insert(0, trap)
    try:
        ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
        auth, _, written = _full_gate10e_pipeline(
            ctx, tmp_path / "training-authorizations")
    finally:
        sys.meta_path.remove(trap)
    assert auth.authorized is True
    files = sorted(p.name for p in written.root.iterdir())
    assert files == ["authorization.json", "environment.json",
                     "findings.json", "manifest.json"]


def test_pipeline_no_subprocess_no_network(
    tmp_path: Path, preflight_pipeline, monkeypatch,
) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 10E must not execute or open a network client")

    import verifiednet.evaluation.inference as inference

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(inference, "OllamaBackend", _boom)
    monkeypatch.setattr(inference, "FakeInferenceBackend", _boom)

    auth, _, _ = _full_gate10e_pipeline(
        ctx, tmp_path / "training-authorizations")
    assert auth.authorized is True


def test_preflight_does_not_mutate_any_source(
    tmp_path: Path, preflight_pipeline,
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
    from verifiednet.training import (
        FakeCheckpointProducer,
        FakeExecutionEngine,
        build_default_checkpoint_production_policy,
        build_execution_policy,
        build_fake_checkpoint_format_spec,
        write_checkpoint,
        write_training_execution,
        write_training_plan,
    )

    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    planctx = ctx.planctx
    # materialize EVERY upstream artifact class, Gate 6 through Gate 10D
    prepared = load_prepared(planctx.prepared_dir)
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(
        task=task, default_fault_family="bgp_remote_as_mismatch")
    write_evaluation(evaluate_prepared_corpus(prepared, baseline, task),
                     tmp_path / "evaluations")
    write_benchmark(run_benchmark(prepared, task=task, predictors=[
        baseline,
        FixedPriorBaseline(task=task,
                           fixed_fault_family="bgp_remote_as_mismatch"),
    ]), tmp_path / "benchmarks")
    fake_plan = planctx.trainer.plan(spec=planctx.spec,
                                     corpus=planctx.descriptor)
    fake_plan_dir = write_training_plan(fake_plan, tmp_path / "fake-plans")
    engine = FakeExecutionEngine()
    execution = engine.execute(
        fake_plan, policy=build_execution_policy(max_retries=1,
                                                 allow_resume=True))
    exec_dir = write_training_execution(execution,
                                        tmp_path / "training-executions")
    producer = FakeCheckpointProducer()
    candidate = producer.produce(
        exec_dir.root, fake_plan_dir.root,
        format_spec=build_fake_checkpoint_format_spec(),
        policy=build_default_checkpoint_production_policy())
    write_checkpoint(candidate, tmp_path / "checkpoints")

    roots = {
        "runs": Path(planctx.run_root),
        "dataset": Path(planctx.dataset_dir),
        "prepared": Path(planctx.prepared_dir),
        "evaluations": tmp_path / "evaluations",
        "benchmarks": tmp_path / "benchmarks",
        "training-corpus": Path(planctx.corpus_root),
        "hf-plan": Path(ctx.plan_dir),
        "fake-plan": Path(fake_plan_dir.root),
        "simulated-execution": Path(exec_dir.root),
        "simulated-checkpoint": tmp_path / "checkpoints",
    }
    before = {name: _fingerprint(root) for name, root in roots.items()}
    _full_gate10e_pipeline(ctx, tmp_path / "training-authorizations")
    after = {name: _fingerprint(root) for name, root in roots.items()}
    assert after == before  # every upstream stage byte-identical


def test_evaluation_isolation(tmp_path: Path, preflight_pipeline) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        diagnosis_task,
        run_benchmark,
        write_benchmark,
    )

    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth_before, snap_before = ctx.backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    w1 = write_training_authorization(auth_before, snap_before,
                                      tmp_path / "before")

    prepared = load_prepared(ctx.planctx.prepared_dir)
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

    auth_after, snap_after = ctx.backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    assert auth_after == auth_before
    assert auth_after.authorization_id == auth_before.authorization_id
    w2 = write_training_authorization(auth_after, snap_after,
                                      tmp_path / "after")
    assert _fingerprint(w1.root) == _fingerprint(w2.root)


def test_environment_sensitivity(tmp_path: Path, preflight_pipeline) -> None:
    # change exactly ONE capability at a time; snapshot/authorization must
    # change while the plan identity never does
    from verifiednet.training import DeterminismCategory

    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    base_auth, base_snap = ctx.backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)

    allowed = (DeterminismCategory.DETERMINISTIC_SUPPORTED,
               DeterminismCategory.BEST_EFFORT_DETERMINISTIC,
               DeterminismCategory.NONDETERMINISTIC)
    variants = {
        "package_version": FakeEnvironmentProbe(packages={
            "torch": ("2.5.0", True), "transformers": ("4.44.0", True)}),
        "device_type": FakeEnvironmentProbe(device=build_device_capability(
            device_type="cuda", declared_device_count=1,
            selected_device_index=0,
            supported_precisions=("bfloat16", "float32"),
            total_memory_bytes=16 * 1024**3,
            deterministic_operations_supported=True)),
        "total_memory": FakeEnvironmentProbe(device=build_device_capability(
            device_type="cpu", declared_device_count=1,
            selected_device_index=0,
            supported_precisions=("bfloat16", "float32"),
            total_memory_bytes=32 * 1024**3,
            deterministic_operations_supported=True)),
        "supported_precision": FakeEnvironmentProbe(
            device=build_device_capability(
                device_type="cpu", declared_device_count=1,
                selected_device_index=0, supported_precisions=("float32",),
                total_memory_bytes=16 * 1024**3,
                deterministic_operations_supported=True)),
        "deterministic_mode": FakeEnvironmentProbe(
            deterministic_supported=False),
    }
    for name, probe in variants.items():
        auth, snap = ctx.make_backend(probe).preflight(
            plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
            model_resolver=ctx.model_resolver,
            tokenizer_resolver=ctx.tokenizer_resolver,
            allowed_determinism=allowed)
        assert snap.environment_snapshot_id != base_snap.environment_snapshot_id, name
        assert auth.authorization_id != base_auth.authorization_id, name
        assert auth.training_plan_id == base_auth.training_plan_id, name

    # model/tokenizer artifact identity sensitivity
    other_model = FakeModelArtifactResolver(parameter_count=20_000_000)
    auth, _ = ctx.backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=other_model,
        tokenizer_resolver=ctx.tokenizer_resolver)
    assert auth.model_artifact is not None
    assert base_auth.model_artifact is not None
    assert (auth.model_artifact.resolved_model_artifact_id
            != base_auth.model_artifact.resolved_model_artifact_id)
    assert auth.authorization_id != base_auth.authorization_id


def test_sensitive_data_cannot_enter_persisted_evidence(
    tmp_path: Path, preflight_pipeline, monkeypatch,
) -> None:
    # populate the process environment with unique fake secrets; none of them
    # may appear anywhere in the persisted authorization artifact
    secrets = {
        "USER": "vn-secret-user-9f3a", "USERNAME": "vn-secret-user-9f3a",
        "HOSTNAME": "vn-secret-host-7b2c", "HOME": "/home/vn-secret-home-5d1e",
        "HF_TOKEN": "vn-secret-hf-1a2b3c4d",
        "AWS_SECRET_ACCESS_KEY": "vn-secret-aws-9z8y7x",
        "SSH_AUTH_SOCK": "/tmp/vn-secret-ssh-4e5f",  # noqa: S108 - fake marker value, never used as a path
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)

    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    _, _, written = _full_gate10e_pipeline(
        ctx, tmp_path / "training-authorizations")
    blobs = b"\x00".join(p.read_bytes()
                         for p in sorted(written.root.rglob("*"))
                         if p.is_file())
    for key, value in secrets.items():
        assert value.encode() not in blobs, key
