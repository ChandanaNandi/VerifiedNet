"""Gate 10E property tests: version logic, id stability, refusal invariants."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.training import (
    check_package,
    estimate_training_memory_bytes,
)

pytestmark = pytest.mark.property

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


@given(major=st.integers(0, 20), minor=st.integers(0, 30),
       patch=st.integers(0, 30), low=st.integers(0, 20))
@settings(max_examples=300)
def test_version_comparison_is_numeric_not_lexicographic(
    major: int, minor: int, patch: int, low: int,
) -> None:
    version = f"{major}.{minor}.{patch}"
    record = check_package(package_name="p",
                           required_constraint=f">={low}",
                           detected_version=version, importable=True)
    expected = "compatible" if major >= low else "incompatible"
    assert record.status == expected, (version, low)
    # id determinism
    again = check_package(package_name="p", required_constraint=f">={low}",
                          detected_version=version, importable=True)
    assert record.package_record_id == again.package_record_id


@given(params=st.integers(1, 10**10), batch=st.integers(1, 64),
       tokens=st.integers(1, 8192))
@settings(max_examples=300)
def test_memory_estimate_arithmetic_properties(
    params: int, batch: int, tokens: int,
) -> None:
    est32 = estimate_training_memory_bytes(
        parameter_count=params, precision="float32",
        per_device_batch_size=batch, max_total_tokens=tokens,
        optimizer_name="adamw")
    est16 = estimate_training_memory_bytes(
        parameter_count=params, precision="bfloat16",
        per_device_batch_size=batch, max_total_tokens=tokens,
        optimizer_name="adamw")
    assert est16 <= est32                      # precision monotonicity
    assert est32 >= params * 16                # weights+grads+adamw floor
    bigger = estimate_training_memory_bytes(
        parameter_count=params + 1, precision="float32",
        per_device_batch_size=batch, max_total_tokens=tokens,
        optimizer_name="adamw")
    assert bigger >= est32                     # parameter monotonicity
    exact = (params * 4 * 2 + params * 8 + batch * tokens * 8192) * 5 // 4
    assert est32 == exact                      # exact integer arithmetic


@given(rot=st.integers(0, 5))
@settings(max_examples=6, deadline=None)
def test_package_record_ordering_independence(rot: int) -> None:
    from verifiednet.training import (
        FakeEnvironmentProbe,
        build_hf_full_finetune_backend_spec,
        snapshot_from_probe,
    )

    # probes may report packages in any internal order; the snapshot is
    # name-sorted and its id is order-independent
    packages = {"torch": ("2.4.0", True), "transformers": ("4.44.0", True)}
    items = list(packages.items())
    rotated = dict(items[rot % len(items):] + items[:rot % len(items)])
    spec = build_hf_full_finetune_backend_spec()
    a = snapshot_from_probe(FakeEnvironmentProbe(packages=packages), spec)
    b = snapshot_from_probe(FakeEnvironmentProbe(packages=rotated), spec)
    assert a.environment_snapshot_id == b.environment_snapshot_id


def test_any_error_finding_forces_refusal(tmp_path_factory, preflight_pipeline) -> None:
    from verifiednet.training import (
        FakeEnvironmentProbe,
        FakeModelArtifactResolver,
        FakeTokenizerArtifactResolver,
        FindingSeverity,
        build_device_capability,
    )

    tmp = tmp_path_factory.mktemp("refusals")
    ctx = preflight_pipeline(tmp, accepted=_ACC, rejected=["run-rej"])
    sabotages = (
        {"model_resolver": FakeModelArtifactResolver(cached=False)},
        {"tokenizer_resolver": FakeTokenizerArtifactResolver(cached=False)},
        {"tokenizer_resolver":
         FakeTokenizerArtifactResolver(special_vocab_agrees=False)},
    )
    for sabotage in sabotages:
        kwargs = dict(plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
                      model_resolver=ctx.model_resolver,
                      tokenizer_resolver=ctx.tokenizer_resolver)
        kwargs.update(sabotage)
        auth, _ = ctx.backend.preflight(**kwargs)
        has_error = any(f.severity is FindingSeverity.ERROR
                        for f in auth.findings)
        assert has_error and auth.authorized is False
    probe_sabotages = (
        FakeEnvironmentProbe(packages={"torch": (None, False),
                                       "transformers": ("4.44.0", True)}),
        FakeEnvironmentProbe(device=build_device_capability(
            device_type="cpu", declared_device_count=2,
            selected_device_index=0, supported_precisions=("float32",),
            total_memory_bytes=16 * 1024**3,
            deterministic_operations_supported=True)),
        FakeEnvironmentProbe(deterministic_supported=False),
    )
    for probe in probe_sabotages:
        auth, _ = ctx.make_backend(probe).preflight(
            plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
            model_resolver=ctx.model_resolver,
            tokenizer_resolver=ctx.tokenizer_resolver)
        assert auth.authorized is False


def test_build_twice_byte_identical(tmp_path_factory, preflight_pipeline) -> None:
    import hashlib

    from verifiednet.training import write_training_authorization

    tmp = tmp_path_factory.mktemp("twice")
    ctx = preflight_pipeline(tmp, accepted=_ACC, rejected=["run-rej"])

    def run():
        return ctx.backend.preflight(
            plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
            model_resolver=ctx.model_resolver,
            tokenizer_resolver=ctx.tokenizer_resolver)

    (auth1, snap1), (auth2, snap2) = run(), run()
    assert auth1.backend_spec_id == auth2.backend_spec_id
    assert snap1.environment_snapshot_id == snap2.environment_snapshot_id
    assert auth1.model_artifact == auth2.model_artifact
    assert auth1.tokenizer_artifact == auth2.tokenizer_artifact
    assert auth1.device_capability_id == auth2.device_capability_id
    assert auth1.findings == auth2.findings
    assert auth1.authorization_id == auth2.authorization_id
    w1 = write_training_authorization(auth1, snap1, tmp / "r1")
    w2 = write_training_authorization(auth2, snap2, tmp / "r2")
    assert w1.authorization_digest == w2.authorization_digest

    def fingerprint(root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    assert fingerprint(w1.root) == fingerprint(w2.root)
