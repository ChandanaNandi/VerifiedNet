"""Contract tests: Gate 8 SLM predictor models frozen, forbid extras, validate ids."""

from __future__ import annotations

import inspect

import pytest
from pydantic import TypeAdapter, ValidationError

from verifiednet.evaluation import (
    Baseline,
    DecodingConfig,
    FakeInferenceBackend,
    PredictorSpec,
    PromptTemplate,
    SlmPredictor,
    diagnosis_prompt_template,
    diagnosis_task,
)
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DatasetPrediction,
    DiagnosisPrediction,
    InvalidPrediction,
    build_invalid_prediction,
)

pytestmark = pytest.mark.contract

_PRED_ADAPTER = TypeAdapter(DatasetPrediction)


def _slm() -> SlmPredictor:
    return SlmPredictor(
        task=diagnosis_task(), backend=FakeInferenceBackend(fixed_text="{}"),
        prompt_template=diagnosis_prompt_template(), model_identifier="m",
        backend_name="fake")


def test_decoding_config_frozen_and_validated() -> None:
    d = DecodingConfig()
    assert DecodingConfig.model_validate_json(d.model_dump_json()) == d
    with pytest.raises(ValidationError):
        d.temperature = 1.0  # frozen
    with pytest.raises(ValidationError):
        DecodingConfig.model_validate(d.model_dump() | {"surprise": 1})  # extra forbid
    with pytest.raises(ValidationError):
        DecodingConfig(max_tokens=0)  # ge=1


def test_predictor_spec_validates_id() -> None:
    spec = _slm().predictor_spec
    assert PredictorSpec.model_validate_json(spec.model_dump_json()) == spec
    with pytest.raises(ValidationError):
        PredictorSpec.model_validate(spec.model_dump() | {"predictor_id": "predictor-0" + "0" * 15})
    with pytest.raises(ValidationError):  # unsupported version does not parse
        PredictorSpec.model_validate(spec.model_dump() | {"predictor_version": 2})


def test_prompt_template_validates_id() -> None:
    t = diagnosis_prompt_template()
    assert PromptTemplate.model_validate_json(t.model_dump_json()) == t
    with pytest.raises(ValidationError):
        PromptTemplate.model_validate(t.model_dump() | {"prompt_template_id": "prompt-" + "0" * 16})


def test_invalid_prediction_in_union_round_trips() -> None:
    inv = build_invalid_prediction(
        baseline_id="baseline-0000000000000000", task_id="task-0000000000000000",
        feature_policy_id="feat-0000000000000000", feature_payload={"a": 1},
        reason_code="malformed_json", raw_excerpt="garbage")
    again = _PRED_ADAPTER.validate_json(_PRED_ADAPTER.dump_json(inv))
    assert again == inv
    assert inv.outcome_kind == "invalid"


def test_predictor_is_a_baseline_and_features_only() -> None:
    slm = _slm()
    assert isinstance(slm, Baseline)
    sig = inspect.signature(slm.predict)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 1
    assert params[0].name == "features"


def test_predictor_ids_deterministic() -> None:
    assert _slm().predictor_spec.predictor_id == _slm().predictor_spec.predictor_id


def test_raw_excerpt_is_bounded() -> None:
    inv = build_invalid_prediction(
        baseline_id="baseline-0000000000000000", task_id="task-0000000000000000",
        feature_policy_id="feat-0000000000000000", feature_payload={},
        reason_code="malformed_json", raw_excerpt="x" * 5000)
    assert len(inv.raw_excerpt) <= 200


def test_prediction_union_has_three_members() -> None:
    # sanity: the union discriminator accepts all three outcome kinds
    kinds = set()
    for cls in (DiagnosisPrediction, AbstentionPrediction, InvalidPrediction):
        kinds.add(cls.model_fields["outcome_kind"].default)
    assert kinds == {"diagnosis", "abstention", "invalid"}
