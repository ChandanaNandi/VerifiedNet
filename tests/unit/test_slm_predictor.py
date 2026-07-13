"""Gate 8 unit tests: SLM predictor prompt/spec/parse/validate/normalize/integrate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifiednet.datasets.features import DatasetFeatures, FeatureEvidenceRef, FeaturePolicy
from verifiednet.evaluation import (
    AbstentionPrediction,
    DecodingConfig,
    DiagnosisPrediction,
    FakeInferenceBackend,
    InvalidPrediction,
    SlmPredictor,
    audit_evaluation_run,
    diagnosis_prompt_template,
    diagnosis_task,
    evaluate_prepared_corpus,
    read_evaluation,
    verify_evaluation,
    write_evaluation,
)

pytestmark = pytest.mark.unit


def _features(onset: bool = True) -> DatasetFeatures:
    return DatasetFeatures(
        feature_policy_id=FeaturePolicy().policy_id, topology_hash="a" * 64,
        backend="frr_compose",
        baseline_evidence=FeatureEvidenceRef(relative_path="evidence/baseline.json"),
        onset_evidence=(FeatureEvidenceRef(relative_path="evidence/onset.json")
                        if onset else None),
    )


def _diag_responder(family: str = "bgp_remote_as_mismatch"):
    def r(prompt: str, decoding: DecodingConfig) -> str:
        if "onset_evidence: absent" in prompt:
            return json.dumps({"prediction_type": "abstention"})
        return json.dumps({"prediction_type": "diagnosis", "fault_family": family})
    return r


def _slm(responder=None, backend_name="fake", model="fake-qwen"):
    return SlmPredictor(
        task=diagnosis_task(), backend=FakeInferenceBackend(responder=responder),
        prompt_template=diagnosis_prompt_template(), model_identifier=model,
        backend_name=backend_name, decoding=DecodingConfig())


def test_prompt_renders_features_only() -> None:
    tmpl = diagnosis_prompt_template()
    rendered = tmpl.render(_features(onset=True))
    assert "onset_evidence: present" in rendered
    assert "frr_compose" in rendered
    assert "bgp_remote_as_mismatch" in rendered  # candidate class list (not the answer)
    # no identity / labels leak into the prompt
    for forbidden in ("example_id", "group_id", "run_id", "run-", "grp-", "ex-",
                      "rejection_code", "ground_truth"):
        assert forbidden not in rendered


def test_prompt_id_deterministic_and_config_sensitive() -> None:
    assert diagnosis_prompt_template().prompt_template_id == \
        diagnosis_prompt_template().prompt_template_id
    other = diagnosis_prompt_template(candidate_families=("bgp_remote_as_mismatch",))
    assert other.prompt_template_id != diagnosis_prompt_template().prompt_template_id


def test_predictor_spec_and_ids() -> None:
    slm = _slm(_diag_responder())
    assert slm.predictor_spec.predictor_id.startswith("predictor-")
    assert slm.spec.baseline_id.startswith("baseline-")
    # the predictor spec is embedded in the Gate-7 baseline spec
    assert slm.spec.rule_configuration["predictor_id"] == slm.predictor_spec.predictor_id
    # changing the model identifier changes the predictor id
    other = _slm(_diag_responder(), model="other-model")
    assert other.predictor_spec.predictor_id != slm.predictor_spec.predictor_id


def test_decoding_config_id_deterministic() -> None:
    assert DecodingConfig().config_id == DecodingConfig().config_id
    assert DecodingConfig(max_tokens=512).config_id != DecodingConfig().config_id


def test_diagnosis_and_abstention_parsing() -> None:
    slm = _slm(_diag_responder())
    diag = slm.predict(_features(onset=True))
    absten = slm.predict(_features(onset=False))
    assert isinstance(diag, DiagnosisPrediction)
    assert diag.fault_family == "bgp_remote_as_mismatch"
    assert isinstance(absten, AbstentionPrediction)
    assert absten.reason_code == "model_abstained"


def test_output_normalization() -> None:
    # A mixed-case / padded family is normalized to the canonical form.
    slm = _slm(_diag_responder(family="  BGP_Remote_AS_Mismatch  "))
    diag = slm.predict(_features(onset=True))
    assert isinstance(diag, DiagnosisPrediction)
    assert diag.fault_family == "bgp_remote_as_mismatch"


def test_unknown_family_is_invalid() -> None:
    slm = _slm(_diag_responder(family="totally_unknown_family"))
    pred = slm.predict(_features(onset=True))
    assert isinstance(pred, InvalidPrediction)
    assert pred.reason_code == "unknown_fault_family"


def test_malformed_json_is_invalid() -> None:
    slm = _slm(lambda p, d: "this is not json")
    pred = slm.predict(_features(onset=True))
    assert isinstance(pred, InvalidPrediction)
    assert pred.reason_code == "malformed_json"
    assert pred.raw_excerpt  # bounded excerpt retained for audit


def test_prediction_is_deterministic() -> None:
    slm = _slm(_diag_responder())
    f = _features(onset=True)
    assert slm.predict(f).prediction_id == slm.predict(f).prediction_id


def test_slm_integrates_with_evaluation(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-rev", "run-b")],
                        rejected=["run-rej"])
    task = diagnosis_task()
    slm = SlmPredictor(
        task=task, backend=FakeInferenceBackend(responder=_diag_responder()),
        prompt_template=diagnosis_prompt_template(), model_identifier="fake-qwen",
        backend_name="fake")
    run = evaluate_prepared_corpus(ctx.loaded, slm, task)
    assert audit_evaluation_run(run).passed
    written = write_evaluation(run, tmp_path / "evaluations")
    assert verify_evaluation(written.root).verified is True
    back = read_evaluation(written.root)
    assert back.evaluation_id == run.evaluation_id
    # the predictor specification is persisted in the evaluation manifest
    manifest = json.loads((written.root / "manifest.json").read_text())
    cfg = manifest["baseline_spec"]["rule_configuration"]
    assert "predictor_id" in cfg and "predictor_spec" in cfg
