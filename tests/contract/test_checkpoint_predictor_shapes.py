"""Gate 11 contract tests: frozen shapes, Literal locks, self-validating ids.

StrictModel note: python-mode validation is strict (tuples for tuple fields);
JSON/dict payloads via ``model_validate`` are used to probe Literal locks so a
rejection is a REAL contract refusal, not a vacuous type error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.evaluation import (
    CheckpointInferenceCompatibility,
    CheckpointInferenceDevicePolicy,
    CheckpointPredictorSpec,
    FakeInferenceBackend,
    VerifiedCheckpointPredictor,
    build_checkpoint_inference_compatibility,
    build_cpu_inference_device_policy,
)

pytestmark = pytest.mark.contract

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def test_device_policy_is_cpu_only_and_literal_locked() -> None:
    policy = build_cpu_inference_device_policy()
    assert policy.device_kind == "cpu"
    assert policy.inference_precision == "float32"
    assert policy.allow_silent_fallback is False
    assert policy.device_policy_id.startswith("infdev-")
    assert build_cpu_inference_device_policy() == policy  # deterministic
    dump = policy.model_dump(mode="json")
    for field, bad in (("device_kind", "cuda"), ("device_kind", "mps"),
                       ("inference_precision", "bfloat16"),
                       ("allow_silent_fallback", True),
                       ("single_process", False)):
        with pytest.raises(ValidationError):
            CheckpointInferenceDevicePolicy.model_validate(dump | {field: bad})
    with pytest.raises(ValidationError):  # tampered id
        CheckpointInferenceDevicePolicy.model_validate(
            dump | {"device_policy_id": "infdev-" + "0" * 16})


def test_inference_compatibility_is_narrow_and_literal_locked() -> None:
    compat = build_checkpoint_inference_compatibility()
    assert compat.supported_architectures == ("Qwen2ForCausalLM",)
    assert compat.compatibility_id.startswith("infcompat-")
    assert build_checkpoint_inference_compatibility() == compat
    dump = compat.model_dump(mode="json")
    for field, bad in (("trust_remote_code", True), ("network_access", True),
                       ("quantization", "int4"), ("adapters", "lora"),
                       ("local_files_only", False),
                       ("tokenizer_source", "hub"),
                       ("backend_family", "remote-api"),
                       ("single_device", False)):
        with pytest.raises(ValidationError):
            CheckpointInferenceCompatibility.model_validate(dump | {field: bad})
    with pytest.raises(ValidationError):  # unsorted architecture list
        CheckpointInferenceCompatibility.model_validate(
            dump | {"supported_architectures": ["b", "a"]})
    with pytest.raises(ValidationError):  # tampered id
        CheckpointInferenceCompatibility.model_validate(
            dump | {"compatibility_id": "infcompat-" + "0" * 16})


def test_predictor_spec_is_self_validating(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    predictor = VerifiedCheckpointPredictor(
        task=ctx.task, bundle=ctx.bundle,
        backend=FakeInferenceBackend(fixed_text="{}"),
        prompt_template=ctx.template, device_policy=ctx.device_policy,
        backend_family="fake")
    spec = predictor.predictor_spec
    dump = spec.model_dump(mode="json")

    def validate(payload: dict[str, object]) -> CheckpointPredictorSpec:
        # JSON-mode validation: strict python-mode would reject the dumped
        # list for the tuple-typed decoding.stop and make rejections vacuous.
        return CheckpointPredictorSpec.model_validate_json(json.dumps(payload))

    assert validate(dump) == spec  # round-trip
    with pytest.raises(ValidationError):  # tampered predictor id
        validate(dump | {"predictor_id": "ckptpred-" + "0" * 24})
    with pytest.raises(ValidationError):  # any content change breaks the id
        validate(dump | {"backend_family": "other"})
    with pytest.raises(ValidationError):  # a non-real checkpoint id is refused
        validate(dump | {"checkpoint_id": "ckpt-" + "0" * 24})
    with pytest.raises(ValidationError):  # a non-real digest is refused
        validate(dump | {"checkpoint_digest": "dig-" + "0" * 24})
    with pytest.raises(ValidationError):  # precision is Literal-locked
        validate(dump | {"inference_precision": "float16"})


def test_predictor_spec_carries_no_host_or_path_facts(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    from verifiednet.common.canonical import canonical_json_str

    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    predictor = VerifiedCheckpointPredictor(
        task=ctx.task, bundle=ctx.bundle,
        backend=FakeInferenceBackend(fixed_text="{}"),
        prompt_template=ctx.template, device_policy=ctx.device_policy,
        backend_family="fake")
    fields = set(CheckpointPredictorSpec.model_fields)
    assert not fields & {"path", "checkpoint_path", "hostname", "username",
                         "timestamp", "created_at", "cache_path",
                         "home_directory", "labels"}
    rendered = canonical_json_str(predictor.predictor_spec)
    assert str(tmp_path) not in rendered
    assert str(ctx.checkpoint_dir) not in rendered
