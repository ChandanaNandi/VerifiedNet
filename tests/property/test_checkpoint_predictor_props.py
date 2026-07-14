"""Gate 11 property tests: id sensitivity and tamper-evidence.

Exhaustive loops (not Hypothesis) are used where a function-scoped pipeline
fixture is involved, mirroring the Gate 10D/10F convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.evaluation import (
    assess_checkpoint_prediction_eligibility,
    derive_checkpoint_predictor_id,
)

pytestmark = pytest.mark.property

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

_BASE_KWARGS: dict[str, object] = {
    "schema_version": 1,
    "predictor_version": 1,
    "checkpoint_id": "realckpt-" + "0" * 24,
    "checkpoint_digest": "realdig-" + "0" * 24,
    "checkpoint_format_id": "realfmt-" + "0" * 16,
    "compatibility_id": "infcompat-" + "0" * 16,
    "model_spec_id": "model-" + "0" * 16,
    "tokenizer_spec_id": "tok-" + "0" * 16,
    "prompt_template_id": "prompt-" + "0" * 16,
    "decoding_config": {"schema_version": 1, "temperature": 0.0,
                        "max_tokens": 256, "stop": [], "seed": None},
    "normalization_policy_id": "norm-" + "0" * 16,
    "backend_family": "hf-transformers-local",
    "inference_precision": "float32",
    "device_policy": "infdev-" + "0" * 16,
}

_MUTATIONS: dict[str, object] = {
    "schema_version": 2,
    "predictor_version": 2,
    "checkpoint_id": "realckpt-" + "1" * 24,
    "checkpoint_digest": "realdig-" + "1" * 24,
    "checkpoint_format_id": "realfmt-" + "1" * 16,
    "compatibility_id": "infcompat-" + "1" * 16,
    "model_spec_id": "model-" + "1" * 16,
    "tokenizer_spec_id": "tok-" + "1" * 16,
    "prompt_template_id": "prompt-" + "1" * 16,
    "decoding_config": {"schema_version": 1, "temperature": 0.0,
                        "max_tokens": 64, "stop": [], "seed": None},
    "normalization_policy_id": "norm-" + "1" * 16,
    "backend_family": "fake",
    "inference_precision": "float64",
    "device_policy": "infdev-" + "1" * 16,
}


def test_predictor_id_changes_with_every_input() -> None:
    base = derive_checkpoint_predictor_id(**_BASE_KWARGS)  # type: ignore[arg-type]
    assert base.startswith("ckptpred-")
    assert base == derive_checkpoint_predictor_id(**_BASE_KWARGS)  # type: ignore[arg-type]
    assert set(_MUTATIONS) == set(_BASE_KWARGS)  # every input is covered
    for field, mutated in _MUTATIONS.items():
        kwargs = dict(_BASE_KWARGS)
        kwargs[field] = mutated
        assert derive_checkpoint_predictor_id(**kwargs) != base, field  # type: ignore[arg-type]


def test_any_payload_byte_flip_breaks_eligibility(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    baseline = assess_checkpoint_prediction_eligibility(
        ctx.checkpoint_dir, ctx.compatibility)
    assert baseline.eligible is True
    targets = ["manifest.json"] + [
        entry.relative_path for entry in ctx.bundle.manifest.files]
    for relative in targets:
        path = ctx.checkpoint_dir / relative
        original = path.read_bytes()
        position = len(original) // 2
        flipped = (original[:position]
                   + bytes([original[position] ^ 0xFF])
                   + original[position + 1:])
        path.write_bytes(flipped)
        try:
            result = assess_checkpoint_prediction_eligibility(
                ctx.checkpoint_dir, ctx.compatibility)
            assert result.eligible is False, relative
        finally:
            path.write_bytes(original)
    # restored: eligible again
    assert assess_checkpoint_prediction_eligibility(
        ctx.checkpoint_dir, ctx.compatibility).eligible is True
