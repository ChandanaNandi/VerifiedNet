"""Gate 10F property tests: slice determinism, objective arithmetic, losses."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.training import (
    BoundedTrainingError,
    build_causal_lm_example,
    build_minimal_safetensors,
    count_safetensors_parameters,
    parse_safetensors_header,
    validate_finite_loss,
)

pytestmark = pytest.mark.property

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


@given(n_in=st.integers(0, 40), n_sep=st.integers(0, 4),
       n_tgt=st.integers(0, 40), budget=st.integers(1, 128))
@settings(max_examples=300)
def test_objective_arithmetic(n_in: int, n_sep: int, n_tgt: int,
                              budget: int) -> None:
    total = n_in + n_sep + n_tgt + 1
    args = dict(input_token_ids=tuple(range(100, 100 + n_in)),
                separator_token_ids=tuple(range(5, 5 + n_sep)),
                target_token_ids=tuple(range(200, 200 + n_tgt)),
                eos_token_id=2, max_total_tokens=budget)
    if total > budget:
        with pytest.raises(BoundedTrainingError):
            build_causal_lm_example(**args)
        return
    tokens, labels = build_causal_lm_example(**args)
    assert len(tokens) == len(labels) == total
    assert labels[:n_in + n_sep] == (-100,) * (n_in + n_sep)
    assert labels[n_in + n_sep:] == (*args["target_token_ids"], 2)
    assert tokens[-1] == 2  # single trailing EOS


@given(shapes=st.lists(st.tuples(st.integers(1, 5), st.integers(1, 5)),
                       min_size=1, max_size=4))
@settings(max_examples=100)
def test_safetensors_roundtrip_and_param_arithmetic(shapes: list) -> None:
    tensors = {f"t{i}": (tuple(shape), bytes(4 * shape[0] * shape[1]))
               for i, shape in enumerate(shapes)}
    blob = build_minimal_safetensors(tensors)
    header = parse_safetensors_header(blob)
    assert set(header) == set(tensors)
    assert count_safetensors_parameters(blob) == sum(
        a * b for a, b in shapes)


@given(mantissa=st.integers(0, 10**9), exponent=st.integers(-9, 3))
@settings(max_examples=200)
def test_finite_loss_serialization(mantissa: int, exponent: int) -> None:
    value = f"{mantissa}E{exponent}"
    assert validate_finite_loss(value) == value
    for non_finite in ("nan", "inf", "-inf"):
        with pytest.raises(ValueError):
            validate_finite_loss(non_finite)


def test_build_twice_structural_equality(tmp_path_factory, realtrain_pipeline) -> None:
    # the deterministic STUB backend must produce byte-identical execution
    # artifacts and checkpoint payloads across two runs of the same intent
    import hashlib

    tmp = tmp_path_factory.mktemp("twice")
    ctx = realtrain_pipeline(tmp, accepted=_ACC, rejected=["run-rej"])
    w1 = ctx.execute()
    w2 = ctx.execute(output_root=tmp / "outputs-2")

    def fingerprint(root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    assert w1.execution_id == w2.execution_id
    assert w1.execution_digest == w2.execution_digest
    assert w1.checkpoint_id == w2.checkpoint_id
    assert fingerprint(w1.root) == fingerprint(w2.root)
    ckpt1 = ctx.output_root / "real-checkpoints" / w1.checkpoint_id
    ckpt2 = tmp / "outputs-2" / "real-checkpoints" / w2.checkpoint_id
    assert fingerprint(ckpt1) == fingerprint(ckpt2)


def test_identity_sensitivity_to_slice_and_policy(
    tmp_path_factory, realtrain_pipeline,
) -> None:
    from verifiednet.training import (
        build_real_execution_policy,
        derive_real_execution_id,
        read_training_authorization,
        read_training_plan,
        select_corpus_slice,
    )

    tmp = tmp_path_factory.mktemp("ripple")
    ctx = realtrain_pipeline(tmp, accepted=_ACC, rejected=["run-rej"])
    loaded_plan = read_training_plan(ctx.plan_dir)
    loaded_auth = read_training_authorization(ctx.auth_dir)

    def exec_id(slice_id: str, policy_id: str) -> str:
        assert loaded_auth.authorization.model_artifact is not None
        assert loaded_auth.authorization.tokenizer_artifact is not None
        return derive_real_execution_id(
            training_plan_id=loaded_plan.plan.training_plan_id,
            plan_digest=loaded_plan.manifest.plan_digest,
            authorization_id=loaded_auth.authorization.authorization_id,
            authorization_digest=loaded_auth.manifest.authorization_digest,
            backend_spec_id=loaded_auth.authorization.backend_spec_id,
            model_artifact_id=(loaded_auth.authorization.model_artifact
                               .resolved_model_artifact_id),
            tokenizer_artifact_id=(loaded_auth.authorization.tokenizer_artifact
                                   .resolved_tokenizer_artifact_id),
            bounded_model_policy_id=(
                ctx.model_policy.bounded_model_policy_id),
            corpus_slice_id=slice_id, real_execution_policy_id=policy_id)

    base = exec_id(ctx.slice_policy.corpus_slice_id,
                   ctx.execution_policy.real_execution_policy_id)
    smaller_slice, _ = select_corpus_slice(ctx.corpus_root, max_example_count=2)
    assert exec_id(smaller_slice.corpus_slice_id,
                   ctx.execution_policy.real_execution_policy_id) != base
    other_policy = build_real_execution_policy(
        approved_backend_id=ctx.execution_policy.approved_backend_id,
        authorization_id=ctx.execution_policy.authorization_id,
        bounded_model_policy_id=(
            ctx.execution_policy.bounded_model_policy_id),
        corpus_slice_id=ctx.execution_policy.corpus_slice_id,
        objective_policy_id=ctx.execution_policy.objective_policy_id,
        max_runtime_optimizer_steps=8,  # changed bound → changed identity
        max_epochs=4, max_examples=16, max_sequence_length=1024,
        max_effective_batch_size=4,
        determinism_acceptance=("deterministic_supported",))
    assert exec_id(ctx.slice_policy.corpus_slice_id,
                   other_policy.real_execution_policy_id) != base
