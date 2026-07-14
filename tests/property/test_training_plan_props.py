"""Gate 10B property tests: arithmetic consistency, id stability, canonicals."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.training import (
    canonical_decimal,
    compute_batches_per_epoch,
    compute_optimizer_steps_per_epoch,
)
from verifiednet.training.trainer import build_capabilities

pytestmark = pytest.mark.property


@given(n=st.integers(1, 10_000), b=st.integers(1, 128), a=st.integers(1, 64),
       epochs=st.integers(1, 20))
@settings(max_examples=300)
def test_batch_step_arithmetic_consistency(n: int, b: int, a: int, epochs: int) -> None:
    batches = compute_batches_per_epoch(n, b)
    steps = compute_optimizer_steps_per_epoch(batches, a)
    # ceil-division bounds
    assert (batches - 1) * b < n <= batches * b
    assert (steps - 1) * a < batches <= steps * a
    # epochs scale linearly with no drift
    assert epochs * steps == sum(steps for _ in range(epochs))


@given(mantissa=st.integers(1, 10**6), exponent=st.integers(-8, 4))
@settings(max_examples=300)
def test_equivalent_decimals_canonicalize_identically(
    mantissa: int, exponent: int,
) -> None:
    plain = f"{mantissa}E{exponent}"
    padded = f"{mantissa}0E{exponent - 1}"  # same value, different representation
    assert canonical_decimal(plain) == canonical_decimal(padded)
    # idempotent
    once = canonical_decimal(plain)
    assert canonical_decimal(once) == once


@given(rot=st.integers(0, 10))
@settings(max_examples=11, deadline=None)
def test_capability_id_is_order_independent(rot: int) -> None:
    optimizers = ["adamw", "sgd", "adafactor"]
    k = rot % len(optimizers)
    rotated = tuple(sorted(optimizers[k:] + optimizers[:k]))
    base = tuple(sorted(optimizers))
    kw = dict(trainer_implementation_id="t", supported_model_families=("fake",),
              supported_precisions=("float32",),
              supported_schedulers=("constant",),
              supported_checkpoint_policies=("none",),
              supports_deterministic="yes", supports_cpu=True, supports_gpu=False,
              supports_adapter_training=False, supports_full_finetuning=False,
              supports_distributed=False)
    a = build_capabilities(supported_optimizers=base, **kw)
    b = build_capabilities(supported_optimizers=rotated, **kw)
    assert a.capability_id == b.capability_id  # sorted before hashing


def test_build_twice_full_equality(tmp_path_factory, plan_pipeline) -> None:
    from verifiednet.common.canonical import canonical_json_bytes

    tmp = tmp_path_factory.mktemp("plans")
    ctx = plan_pipeline(tmp, accepted=[("ras-ref", "run-a"), ("nr-rev", "run-b")],
                        rejected=["run-rej"])
    p1 = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    p2 = ctx.trainer.plan(spec=ctx.make_spec(), corpus=ctx.descriptor)
    assert canonical_json_bytes(p1) == canonical_json_bytes(p2)
    s1 = ctx.trainer.simulate(p1)
    s2 = ctx.trainer.simulate(p2)
    assert canonical_json_bytes(s1) == canonical_json_bytes(s2)
