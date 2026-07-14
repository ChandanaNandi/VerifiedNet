"""Gate 10E failure tests: refusals across every stage + store corruption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifiednet.training import (
    AuthorizationStoreError,
    FakeEnvironmentProbe,
    FakeModelArtifactResolver,
    FakeTokenizerArtifactResolver,
    build_device_capability,
    read_training_authorization,
    verify_training_authorization,
    write_training_authorization,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def _run(ctx, **kw):
    defaults = dict(plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
                    model_resolver=ctx.model_resolver,
                    tokenizer_resolver=ctx.tokenizer_resolver)
    defaults.update(kw)
    return ctx.backend.preflight(**defaults)


def _codes(auth):
    return {f.code for f in auth.findings}


def test_unverified_plan_and_corpus_refused(
    tmp_path: Path, preflight_pipeline,
) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    # corrupted plan artifact
    victim = ctx.plan_dir / "plan.json"
    original = victim.read_bytes()
    victim.write_bytes(original + b" ")
    auth, _ = _run(ctx)
    assert auth.authorized is False
    assert "plan_artifact_invalid" in _codes(auth)
    assert "stage_skipped" in _codes(auth)  # skips are visible, never hidden
    victim.write_bytes(original)
    # corrupted corpus artifact
    corpus_victim = Path(ctx.corpus_root) / "inputs.jsonl"
    corpus_original = corpus_victim.read_bytes()
    corpus_victim.write_bytes(corpus_original + b" ")
    auth, _ = _run(ctx)
    assert auth.authorized is False
    assert "corpus_artifact_invalid" in _codes(auth)
    corpus_victim.write_bytes(corpus_original)
    # missing plan directory entirely
    auth, _ = _run(ctx, plan_dir=tmp_path / "nope")
    assert auth.authorized is False


def test_plan_corpus_mismatch_refused(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path / "a", accepted=_ACC, rejected=["run-rej"])
    other = preflight_pipeline(tmp_path / "b", accepted=_ACC[:2],
                               rejected=["run-rej"])
    auth, _ = _run(ctx, corpus_root=other.corpus_root)
    assert auth.authorized is False
    assert "corpus_plan_mismatch" in _codes(auth)


def test_fake_trainer_plan_never_runs_on_real_backend(
    tmp_path: Path, preflight_pipeline,
) -> None:
    # the Gate 10B fake-trainer plan from the same corpus is structurally
    # refused by the real backend's contract stage
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    from verifiednet.training import write_training_plan

    fake_plan = ctx.planctx.trainer.plan(spec=ctx.planctx.spec,
                                         corpus=ctx.planctx.descriptor)
    w = write_training_plan(fake_plan, tmp_path / "fake-plans")
    auth, _ = _run(ctx, plan_dir=w.root)
    assert auth.authorized is False
    assert "fake_plan_on_real_backend" in _codes(auth)


def test_dependency_failures_refused(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    missing = FakeEnvironmentProbe(packages={
        "torch": (None, False), "transformers": ("4.44.0", True)})
    auth, _ = ctx.make_backend(missing).preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    assert auth.authorized is False
    assert "package_missing" in _codes(auth)
    old = FakeEnvironmentProbe(packages={
        "torch": ("1.13.0", True), "transformers": ("4.44.0", True)})
    auth2, _ = ctx.make_backend(old).preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    assert auth2.authorized is False
    assert "package_incompatible" in _codes(auth2)


def test_environment_failures_refused(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cases = {
        "operating_system_unsupported": FakeEnvironmentProbe(os_family="windows"),
        "implicit_distributed_rejected": FakeEnvironmentProbe(
            device=build_device_capability(
                device_type="cpu", declared_device_count=4,
                selected_device_index=0,
                supported_precisions=("bfloat16", "float32"),
                total_memory_bytes=16 * 1024**3,
                deterministic_operations_supported=True)),
        "no_supported_device": FakeEnvironmentProbe(
            device=build_device_capability(
                device_type="cpu", declared_device_count=0,
                selected_device_index=0,
                supported_precisions=("float32",), total_memory_bytes=0,
                deterministic_operations_supported=True)),
        "device_type_unsupported": FakeEnvironmentProbe(
            device=build_device_capability(
                device_type="metal", declared_device_count=1,
                selected_device_index=0,
                supported_precisions=("bfloat16", "float32"),
                total_memory_bytes=16 * 1024**3,
                deterministic_operations_supported=True)),
        "precision_unavailable_on_device": FakeEnvironmentProbe(
            device=build_device_capability(
                device_type="cpu", declared_device_count=1,
                selected_device_index=0,
                supported_precisions=("bfloat16",),  # plan wants float32
                total_memory_bytes=16 * 1024**3,
                deterministic_operations_supported=True)),
        "total_memory_undeclared": FakeEnvironmentProbe(
            device=build_device_capability(
                device_type="cpu", declared_device_count=1,
                selected_device_index=0,
                supported_precisions=("bfloat16", "float32"),
                total_memory_bytes=0,
                deterministic_operations_supported=True)),
        "insufficient_total_memory": FakeEnvironmentProbe(
            device=build_device_capability(
                device_type="cpu", declared_device_count=1,
                selected_device_index=0,
                supported_precisions=("bfloat16", "float32"),
                total_memory_bytes=1024,  # absurdly small
                deterministic_operations_supported=True)),
        "determinism_category_forbidden": FakeEnvironmentProbe(
            deterministic_supported=False),
    }
    for expected_code, probe in cases.items():
        auth, _ = ctx.make_backend(probe).preflight(
            plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
            model_resolver=ctx.model_resolver,
            tokenizer_resolver=ctx.tokenizer_resolver)
        assert auth.authorized is False, expected_code
        assert expected_code in _codes(auth), expected_code


def test_best_effort_requires_explicit_acknowledgement(
    tmp_path: Path, preflight_pipeline,
) -> None:
    from verifiednet.training import DeterminismCategory, FindingSeverity

    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cuda_probe = FakeEnvironmentProbe(device=build_device_capability(
        device_type="cuda", declared_device_count=1, selected_device_index=0,
        supported_precisions=("bfloat16", "float32"),
        total_memory_bytes=16 * 1024**3,
        deterministic_operations_supported=True))
    backend = ctx.make_backend(cuda_probe)
    # default policy: best-effort NOT allowed → refusal, never a downgrade
    refused, _ = backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    assert refused.authorized is False
    assert "determinism_category_forbidden" in _codes(refused)
    # explicit acknowledgement → authorized with a visible WARNING
    allowed, _ = backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver,
        allowed_determinism=(
            DeterminismCategory.DETERMINISTIC_SUPPORTED,
            DeterminismCategory.BEST_EFFORT_DETERMINISTIC))
    assert allowed.authorized is True
    assert "best_effort_acknowledged" in _codes(allowed)
    assert any(f.severity is FindingSeverity.WARNING for f in allowed.findings)


def test_resolution_failures_refused(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth, _ = _run(ctx, model_resolver=FakeModelArtifactResolver(cached=False))
    assert auth.authorized is False
    assert "model_unresolved" in _codes(auth)
    auth, _ = _run(ctx,
                   tokenizer_resolver=FakeTokenizerArtifactResolver(cached=False))
    assert auth.authorized is False
    assert "tokenizer_unresolved" in _codes(auth)
    auth, _ = _run(ctx, tokenizer_resolver=FakeTokenizerArtifactResolver(
        special_vocab_agrees=False))
    assert auth.authorized is False
    assert "tokenizer_unresolved" in _codes(auth)


def test_store_corruption_matrix(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth, snapshot = _run(ctx)
    root = tmp_path / "training-authorizations"
    written = write_training_authorization(auth, snapshot, root)

    with pytest.raises(AuthorizationStoreError):  # unsafe overwrite
        write_training_authorization(auth, snapshot, root)

    # corrupted environment snapshot
    env_path = written.root / "environment.json"
    good_env = env_path.read_bytes()
    env_path.write_bytes(good_env + b" ")
    result = verify_training_authorization(written.root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    env_path.write_bytes(good_env)

    # malformed persisted finding
    findings_path = written.root / "findings.json"
    good_findings = findings_path.read_bytes()
    data = json.loads(good_findings)
    data[0]["severity"] = "catastrophic"  # not a valid severity
    findings_path.write_bytes(json.dumps(data).encode())
    result = verify_training_authorization(written.root)
    assert result.verified is False
    findings_path.write_bytes(good_findings)

    # tampered manifest digest → self-validation fails at parse
    manifest_path = written.root / "manifest.json"
    good_manifest = manifest_path.read_bytes()
    m = json.loads(good_manifest)
    m["authorization_digest"] = "authdig-" + "0" * 24
    manifest_path.write_bytes(json.dumps(m).encode())
    result = verify_training_authorization(written.root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)
    manifest_path.write_bytes(good_manifest)

    # flipping the stored authorized boolean cannot survive: the digest,
    # the manifest binding, and the authorization id all disagree
    m = json.loads(good_manifest)
    m["authorized"] = False
    manifest_path.write_bytes(json.dumps(m).encode())
    assert verify_training_authorization(written.root).verified is False
    manifest_path.write_bytes(good_manifest)

    # missing file / missing dir
    (written.root / "findings.json").unlink()
    result = verify_training_authorization(written.root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)
    findings_path.write_bytes(good_findings)
    assert verify_training_authorization(written.root).verified is True
    assert verify_training_authorization(tmp_path / "nope").verified is False

    with pytest.raises(AuthorizationStoreError):  # mismatched snapshot refused
        other_snapshot = ctx.backend.inspect_environment()
        wrong = ctx.make_backend(FakeEnvironmentProbe(
            python_version="3.12.9")).inspect_environment()
        assert other_snapshot.environment_snapshot_id != \
            wrong.environment_snapshot_id
        write_training_authorization(auth, wrong, tmp_path / "other-root")

    read_training_authorization(written.root)  # still healthy at the end
