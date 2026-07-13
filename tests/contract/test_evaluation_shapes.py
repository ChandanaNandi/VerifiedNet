"""Contract tests: Gate 7 evaluation models frozen, forbid extras, validate ids."""

from __future__ import annotations

import inspect

import pytest
from pydantic import TypeAdapter, ValidationError

from verifiednet.datasets.features import DatasetFeatures
from verifiednet.evaluation import (
    Baseline,
    BaselineSpec,
    EvidenceRuleBaseline,
    NormalizationPolicy,
    diagnosis_task,
)
from verifiednet.evaluation.baseline import derive_baseline_id
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DatasetPrediction,
    DiagnosisPrediction,
)

pytestmark = pytest.mark.contract

_PRED_ADAPTER = TypeAdapter(DatasetPrediction)


def test_task_frozen_and_validates_id() -> None:
    task = diagnosis_task()
    assert diagnosis_task().model_validate_json(task.model_dump_json()) == task
    with pytest.raises(ValidationError):
        task.task_name = "x"  # frozen
    with pytest.raises(ValidationError):  # tampered id rejected
        type(task).model_validate(task.model_dump() | {"task_id": "task-0000000000000000"})
    with pytest.raises(ValidationError):  # unsupported version does not parse
        type(task).model_validate(task.model_dump() | {"task_version": 2})


def test_baseline_spec_validates_id() -> None:
    task = diagnosis_task()
    spec = EvidenceRuleBaseline(task=task, default_fault_family="x").spec
    assert BaselineSpec.model_validate_json(spec.model_dump_json()) == spec
    with pytest.raises(ValidationError):
        BaselineSpec.model_validate(spec.model_dump() | {"baseline_id": "baseline-0" * 1})


def test_prediction_discriminated_union() -> None:
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_x")
    feats = DatasetFeatures(
        feature_policy_id="feat-0000000000000000", topology_hash="a" * 64,
        backend="frr_compose",
        baseline_evidence={"relative_path": "evidence/baseline.json"},  # type: ignore[arg-type]
        onset_evidence={"relative_path": "evidence/onset.json"},  # type: ignore[arg-type]
    )
    pred = baseline.predict(feats)
    assert _PRED_ADAPTER.validate_json(_PRED_ADAPTER.dump_json(pred)) == pred


def test_prediction_cannot_carry_both_kinds() -> None:
    # A diagnosis prediction may not carry abstention fields, and vice versa.
    with pytest.raises(ValidationError):
        DiagnosisPrediction(prediction_id="pred-" + "0" * 16, fault_family="x",
                            reason_code="y")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AbstentionPrediction(prediction_id="pred-" + "0" * 16, reason_code="y",
                            fault_family="x")  # type: ignore[call-arg]
    # bad id format is rejected
    with pytest.raises(ValidationError):
        DiagnosisPrediction(prediction_id="nope", fault_family="x")


def test_abstention_is_explicit_type() -> None:
    p = AbstentionPrediction(prediction_id="pred-" + "0" * 16, reason_code="no_onset")
    assert p.outcome_kind == "abstention"
    assert p.abstain is True
    # abstain is a Literal[True] — it can never be False/None/empty
    with pytest.raises(ValidationError):
        AbstentionPrediction(prediction_id="pred-" + "0" * 16, reason_code="x",
                            abstain=False)  # type: ignore[arg-type]


def test_baseline_interface_accepts_features_only() -> None:
    # The Protocol's predict signature is typed to DatasetFeatures — labels/trace
    # are not part of the model-facing prediction API.
    sig = inspect.signature(Baseline.predict)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 1
    assert params[0].annotation == "DatasetFeatures"
    assert isinstance(EvidenceRuleBaseline(task=diagnosis_task(),
                                           default_fault_family="x"), Baseline)


def test_normalization_policy_id_stable() -> None:
    assert NormalizationPolicy().policy_id == NormalizationPolicy().policy_id
    assert NormalizationPolicy(casefold=False).policy_id != NormalizationPolicy().policy_id


def test_derive_baseline_id_config_sensitive() -> None:
    task = diagnosis_task()
    base = dict(schema_version=1, baseline_name="b", baseline_version=1,
                rule_set_version=1, task_id=task.task_id)
    a = derive_baseline_id(rule_configuration={"k": "1"}, **base)
    b = derive_baseline_id(rule_configuration={"k": "2"}, **base)
    assert a != b
