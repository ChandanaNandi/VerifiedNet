"""SLM predictor: a model-backed predictor on the Gate 7 baseline boundary (Gate 8).

``SlmPredictor`` implements the SAME ``predict(features: DatasetFeatures) ->
DatasetPrediction`` interface as the deterministic rule baselines. It receives
ONLY model-visible features, renders a versioned prompt, calls a pluggable
``InferenceBackend``, and STRICTLY parses/validates/normalizes the structured
response into a prediction. Malformed or unusable output becomes an explicit
``InvalidPrediction`` (never an exception escaping the evaluation engine).

To keep the Gate 7 evaluation engine, records, metrics, and digests unchanged, the
predictor exposes a Gate-7 ``BaselineSpec`` whose ``baseline_id`` and
``rule_configuration`` embed the full ``PredictorSpec`` (including ``predictor_id``)
— so the evaluation manifest persists the predictor specification with no
structural change, and any prediction-affecting change alters the id.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.canonical import canonical_json_str
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.features import DatasetFeatures
from verifiednet.evaluation.baseline import BaselineSpec, derive_baseline_id
from verifiednet.evaluation.contract import EvaluationTask, NormalizationPolicy
from verifiednet.evaluation.inference import (
    BackendUnavailableError,
    DecodingConfig,
    InferenceBackend,
    InferenceTimeoutError,
)
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    InvalidPrediction,
    build_abstention_prediction,
    build_diagnosis_prediction,
    build_invalid_prediction,
)
from verifiednet.evaluation.prompt import PromptTemplate
from verifiednet.schemas.base import StrictModel

PREDICTOR_VERSION = 1


def derive_predictor_id(
    *,
    schema_version: int,
    predictor_name: str,
    predictor_version: int,
    backend: str,
    model_identifier: str,
    prompt_template_id: str,
    decoding_config_id: str,
    normalization_policy_id: str,
) -> str:
    payload = {
        "schema_version": schema_version,
        "predictor_name": predictor_name,
        "predictor_version": predictor_version,
        "backend": backend,
        "model_identifier": model_identifier,
        "prompt_template_id": prompt_template_id,
        "decoding_config_id": decoding_config_id,
        "normalization_policy_id": normalization_policy_id,
    }
    return "predictor-" + sha256_canonical(payload)[:16]


class PredictorSpec(StrictModel):
    """A frozen, content-addressed model-predictor specification."""

    schema_version: Literal[1] = 1
    predictor_version: Literal[1] = 1
    predictor_name: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    model_identifier: str = Field(min_length=1)
    prompt_template_id: str = Field(min_length=1)
    decoding: DecodingConfig
    normalization_policy_id: str = Field(min_length=1)
    predictor_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> PredictorSpec:
        expected = derive_predictor_id(
            schema_version=self.schema_version, predictor_name=self.predictor_name,
            predictor_version=self.predictor_version, backend=self.backend,
            model_identifier=self.model_identifier,
            prompt_template_id=self.prompt_template_id,
            decoding_config_id=self.decoding.config_id,
            normalization_policy_id=self.normalization_policy_id,
        )
        if self.predictor_id != expected:
            raise ValueError("predictor_id does not match the predictor configuration")
        return self


class SlmPredictor:
    """A model-backed predictor plugged into the Gate 7 evaluation boundary."""

    def __init__(
        self,
        *,
        task: EvaluationTask,
        backend: InferenceBackend,
        prompt_template: PromptTemplate,
        model_identifier: str,
        backend_name: str,
        decoding: DecodingConfig | None = None,
        predictor_name: str = "slm_predictor",
        normalization: NormalizationPolicy | None = None,
    ) -> None:
        self._task_id = task.task_id
        self._backend = backend
        self._template = prompt_template
        self._decoding = decoding or DecodingConfig()
        self._norm = normalization or NormalizationPolicy()
        self._candidates = frozenset(
            self._norm.normalize(f) for f in prompt_template.candidate_families
        )
        predictor_id = derive_predictor_id(
            schema_version=1, predictor_name=predictor_name,
            predictor_version=PREDICTOR_VERSION, backend=backend_name,
            model_identifier=model_identifier,
            prompt_template_id=prompt_template.prompt_template_id,
            decoding_config_id=self._decoding.config_id,
            normalization_policy_id=self._norm.policy_id,
        )
        self._predictor_spec = PredictorSpec(
            predictor_name=predictor_name, backend=backend_name,
            model_identifier=model_identifier,
            prompt_template_id=prompt_template.prompt_template_id,
            decoding=self._decoding, normalization_policy_id=self._norm.policy_id,
            predictor_id=predictor_id,
        )
        # Gate-7 BaselineSpec: the predictor spec is embedded (and hashed) so the
        # evaluation manifest persists it with no structural change.
        cfg = {
            "predictor_id": predictor_id,
            "predictor_spec": canonical_json_str(self._predictor_spec),
        }
        self._spec = BaselineSpec(
            baseline_name=predictor_name, rule_set_version=PREDICTOR_VERSION,
            task_id=task.task_id, rule_configuration=cfg,
            baseline_id=derive_baseline_id(
                schema_version=1, baseline_name=predictor_name, baseline_version=1,
                rule_set_version=PREDICTOR_VERSION, task_id=task.task_id,
                rule_configuration=cfg),
        )

    @property
    def spec(self) -> BaselineSpec:
        return self._spec

    @property
    def predictor_spec(self) -> PredictorSpec:
        return self._predictor_spec

    def predict(
        self, features: DatasetFeatures
    ) -> DiagnosisPrediction | AbstentionPrediction | InvalidPrediction:
        prompt = self._template.render(features)
        payload = features.model_dump(mode="json")
        try:
            response = self._backend.generate(prompt, decoding=self._decoding)
        except BackendUnavailableError as exc:
            return self._invalid(payload, "backend_unavailable", str(exc))
        except InferenceTimeoutError as exc:
            return self._invalid(payload, "inference_timeout", str(exc))
        return self._parse(response.text, features_payload=payload)

    # -- parsing / validation / normalization -----------------------------

    def _invalid(
        self, payload: dict[str, object], reason: str, raw: str
    ) -> InvalidPrediction:
        return build_invalid_prediction(
            baseline_id=self._spec.baseline_id, task_id=self._task_id,
            feature_policy_id=str(payload.get("feature_policy_id", "")),
            feature_payload=payload, reason_code=reason, raw_excerpt=raw,
        )

    def _parse(
        self, text: str, *, features_payload: dict[str, object]
    ) -> DiagnosisPrediction | AbstentionPrediction | InvalidPrediction:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return self._invalid(features_payload, "malformed_json", text)
        if not isinstance(data, dict):
            return self._invalid(features_payload, "not_an_object", text)
        ptype = data.get("prediction_type")
        fpid = str(features_payload.get("feature_policy_id", ""))
        if ptype == "abstention":
            return build_abstention_prediction(
                baseline_id=self._spec.baseline_id, task_id=self._task_id,
                feature_policy_id=fpid, feature_payload=features_payload,
                reason_code="model_abstained",
            )
        if ptype == "diagnosis":
            family = data.get("fault_family")
            if not isinstance(family, str) or not family.strip():
                return self._invalid(features_payload, "missing_fault_family", text)
            normalized = self._norm.normalize(family)
            if normalized not in self._candidates:
                return self._invalid(features_payload, "unknown_fault_family", text)
            return build_diagnosis_prediction(
                baseline_id=self._spec.baseline_id, task_id=self._task_id,
                feature_policy_id=fpid, feature_payload=features_payload,
                fault_family=normalized, matched_rules=(),
            )
        return self._invalid(features_payload, "unsupported_prediction_type", text)
