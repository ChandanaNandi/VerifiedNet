"""Gate 10E unit tests: ids, checks, resolution, estimation, preflight, store."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.training import (
    DeterminismCategory,
    FakeEnvironmentProbe,
    FakeModelArtifactResolver,
    FakeTokenizerArtifactResolver,
    FindingSeverity,
    PreflightStage,
    assess_determinism,
    build_device_capability,
    build_hf_full_finetune_backend_spec,
    check_package,
    estimate_training_memory_bytes,
    read_training_authorization,
    snapshot_from_probe,
    verify_training_authorization,
    write_training_authorization,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def _preflight(ctx, **kw):
    defaults = dict(plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
                    model_resolver=ctx.model_resolver,
                    tokenizer_resolver=ctx.tokenizer_resolver)
    defaults.update(kw)
    return ctx.backend.preflight(**defaults)


def test_backend_spec_id_deterministic() -> None:
    a, b = build_hf_full_finetune_backend_spec(), build_hf_full_finetune_backend_spec()
    assert a == b
    assert a.backend_spec_id.startswith("trainbk-")
    assert a.training_mode == "full_finetune_single_device"


def test_environment_snapshot_construction() -> None:
    spec = build_hf_full_finetune_backend_spec()
    snap1 = snapshot_from_probe(FakeEnvironmentProbe(), spec)
    snap2 = snapshot_from_probe(FakeEnvironmentProbe(), spec)
    assert snap1 == snap2  # identical probes → identical snapshot + id
    assert snap1.environment_snapshot_id.startswith("envsnap-")
    assert snap1.backend_available is True
    assert [r.package_name for r in snap1.package_records] == \
        ["torch", "transformers"]


def test_package_version_compatibility_is_pep440() -> None:
    ok = check_package(package_name="torch", required_constraint=">=2.2,<3",
                       detected_version="2.4.0", importable=True)
    assert ok.status == "compatible"
    # NOT lexicographic: "2.10" > "2.9" numerically even though "2.10" < "2.9"
    # as a string.
    new = check_package(package_name="torch", required_constraint=">=2.9,<3",
                        detected_version="2.10.0", importable=True)
    assert new.status == "compatible"
    old = check_package(package_name="torch", required_constraint=">=2.2,<3",
                        detected_version="1.13.0", importable=True)
    assert old.status == "incompatible"
    missing = check_package(package_name="torch", required_constraint=">=2.2",
                            detected_version=None, importable=False)
    assert missing.status == "missing"
    weird = check_package(package_name="torch", required_constraint=">=2.2",
                          detected_version="not-a-version", importable=True)
    assert weird.status == "unparseable"


def test_device_capability_ids() -> None:
    cpu = build_device_capability(
        device_type="cpu", declared_device_count=1, selected_device_index=0,
        supported_precisions=("float32",), total_memory_bytes=8 * 1024**3,
        deterministic_operations_supported=True)
    assert cpu.device_capability_id.startswith("devcap-")
    cuda = build_device_capability(
        device_type="cuda", declared_device_count=1, selected_device_index=0,
        supported_precisions=("bfloat16", "float32"),
        total_memory_bytes=8 * 1024**3,
        deterministic_operations_supported=True)
    assert cuda.device_capability_id != cpu.device_capability_id


def test_model_and_vocab_artifact_resolution(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    model = FakeModelArtifactResolver().resolve(ctx.hf_spec.model)
    assert model.verification_status == "verified"
    assert model.resolved_model_artifact_id.startswith("modelart-")
    assert model.content_hash is not None
    assert model.declared_parameter_count == 10_000_000
    tok = FakeTokenizerArtifactResolver().resolve(ctx.hf_spec.tokenizer)
    assert tok.verification_status == "verified"
    assert tok.resolved_tokenizer_artifact_id.startswith("tokart-")
    # uncached artifacts resolve honestly as unverified
    uncached = FakeModelArtifactResolver(cached=False).resolve(ctx.hf_spec.model)
    assert uncached.verification_status == "unverified"
    assert uncached.locally_cached is False


def test_memory_estimation_arithmetic() -> None:
    est = estimate_training_memory_bytes(
        parameter_count=1_000_000, precision="float32",
        per_device_batch_size=2, max_total_tokens=576,
        optimizer_name="adamw")
    # (1M*4*2 + 1M*8 + 2*576*8192) * 5 // 4
    assert est == (8_000_000 + 8_000_000 + 9_437_184) * 5 // 4
    smaller = estimate_training_memory_bytes(
        parameter_count=1_000_000, precision="bfloat16",
        per_device_batch_size=2, max_total_tokens=576,
        optimizer_name="adamw")
    assert smaller < est
    with pytest.raises(ValueError):
        estimate_training_memory_bytes(
            parameter_count=0, precision="float32", per_device_batch_size=1,
            max_total_tokens=8, optimizer_name="adamw")
    with pytest.raises(ValueError):  # no estimator → refuse, never guess
        estimate_training_memory_bytes(
            parameter_count=10, precision="float32", per_device_batch_size=1,
            max_total_tokens=8, optimizer_name="sgd")


def test_determinism_assessment_categories() -> None:
    spec = build_hf_full_finetune_backend_spec()
    cpu_snap = snapshot_from_probe(FakeEnvironmentProbe(), spec)
    category, explanation = assess_determinism(snapshot=cpu_snap)
    assert category is DeterminismCategory.DETERMINISTIC_SUPPORTED
    assert "bit-identical" in explanation  # honesty is spelled out
    cuda = build_device_capability(
        device_type="cuda", declared_device_count=1, selected_device_index=0,
        supported_precisions=("bfloat16", "float32"),
        total_memory_bytes=16 * 1024**3,
        deterministic_operations_supported=True)
    cuda_snap = snapshot_from_probe(FakeEnvironmentProbe(device=cuda), spec)
    category, _ = assess_determinism(snapshot=cuda_snap)
    assert category is DeterminismCategory.BEST_EFFORT_DETERMINISTIC
    nodet = snapshot_from_probe(
        FakeEnvironmentProbe(deterministic_supported=False), spec)
    assert assess_determinism(snapshot=nodet)[0] is \
        DeterminismCategory.NONDETERMINISTIC
    nopkg = snapshot_from_probe(
        FakeEnvironmentProbe(packages={"torch": (None, False),
                                       "transformers": (None, False)}), spec)
    assert assess_determinism(snapshot=nopkg)[0] is \
        DeterminismCategory.UNSUPPORTED


def test_successful_fake_preflight(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth, snapshot = _preflight(ctx)
    assert auth.authorized is True
    assert auth.authorization_id.startswith("trainauth-")
    assert auth.environment_snapshot_id == snapshot.environment_snapshot_id
    assert auth.determinism_category is DeterminismCategory.DETERMINISTIC_SUPPORTED
    stages = [f.stage for f in auth.findings]
    assert stages == sorted(stages, key=list(PreflightStage).index)
    assert set(stages) == set(PreflightStage)  # every stage reported
    assert not any(f.severity is FindingSeverity.ERROR for f in auth.findings)
    assert auth.model_artifact is not None
    assert auth.tokenizer_artifact is not None


def test_refused_fake_preflight(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth, _ = _preflight(
        ctx, model_resolver=FakeModelArtifactResolver(cached=False))
    assert auth.authorized is False
    codes = {f.code for f in auth.findings}
    assert "model_unresolved" in codes
    assert "authorization_refused" in codes
    # refusal is still a COMPLETE structured artifact
    assert {f.stage for f in auth.findings} == set(PreflightStage)


def test_write_verify_read_round_trip(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth, snapshot = _preflight(ctx)
    written = write_training_authorization(
        auth, snapshot, tmp_path / "training-authorizations")
    assert written.root.name == auth.authorization_id
    assert written.authorized is True
    result = verify_training_authorization(written.root)
    assert result.verified is True, result.failures
    loaded = read_training_authorization(written.root)
    assert loaded.authorization == auth
    assert loaded.snapshot == snapshot
    assert loaded.manifest.authorization_digest.startswith("authdig-")
    files = sorted(p.name for p in written.root.iterdir())
    assert files == ["authorization.json", "environment.json",
                     "findings.json", "manifest.json"]


def test_refusal_artifacts_also_persist(tmp_path: Path, preflight_pipeline) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth, snapshot = _preflight(
        ctx, tokenizer_resolver=FakeTokenizerArtifactResolver(cached=False))
    assert auth.authorized is False
    written = write_training_authorization(
        auth, snapshot, tmp_path / "training-authorizations")
    assert verify_training_authorization(written.root).verified is True
    assert read_training_authorization(written.root).authorization.authorized \
        is False
