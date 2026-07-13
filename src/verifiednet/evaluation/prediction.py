"""Deterministic prediction models (Gate 7).

A prediction is an EXPLICIT typed outcome — a discriminated union of a diagnosis
(a predicted fault family) and an abstention (an explicit decline). Abstention is
never an empty string, ``None``, ``"healthy"``, ``"no fault"``, a missing value,
or an exception path.

Every prediction carries a deterministic ``prediction_id`` derived from the
prediction's binding context — ``(baseline_id, task_id, feature_policy_id,
canonical feature payload, canonical prediction payload)`` — and NEVER from
example/group/run id, split, timestamps, order, or path. The models validate the
id FORMAT; the full derived-value check is performed where the binding context
exists (the engine, the verifier, and the integrity audit) — see
``derive_prediction_id`` / ``verify_prediction_id``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel

_PRED_ID_RE = re.compile(r"^pred-[0-9a-f]{16}$")


class PredictionOutcome(StrEnum):
    DIAGNOSIS = "diagnosis"
    ABSTENTION = "abstention"
    INVALID = "invalid"

#: Bound on the raw model output excerpt kept on an invalid prediction (never
#: authoritative, never evaluated — retained only for auditing).
RAW_EXCERPT_LIMIT = 200


class DiagnosisPrediction(StrictModel):
    schema_version: Literal[1] = 1
    outcome_kind: Literal["diagnosis"] = "diagnosis"
    prediction_id: str
    fault_family: str = Field(min_length=1)
    matched_rules: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("prediction_id")
    @classmethod
    def _fmt(cls, value: str) -> str:
        if not _PRED_ID_RE.match(value):
            raise ValueError(f"prediction_id must be 'pred-<16 hex>': {value!r}")
        return value


class AbstentionPrediction(StrictModel):
    schema_version: Literal[1] = 1
    outcome_kind: Literal["abstention"] = "abstention"
    prediction_id: str
    abstain: Literal[True] = True
    reason_code: str = Field(min_length=1)
    matched_rules: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("prediction_id")
    @classmethod
    def _fmt(cls, value: str) -> str:
        if not _PRED_ID_RE.match(value):
            raise ValueError(f"prediction_id must be 'pred-<16 hex>': {value!r}")
        return value


class InvalidPrediction(StrictModel):
    """An explicit, structured "the model produced no usable prediction" outcome.

    Used only by model-backed predictors (Gate 8) when output is malformed,
    incomplete, or fails validation. It is ALWAYS scored incorrect and is never an
    exception path. ``raw_excerpt`` is a bounded, non-authoritative fragment kept
    for auditing; it is never evaluated.
    """

    schema_version: Literal[1] = 1
    outcome_kind: Literal["invalid"] = "invalid"
    prediction_id: str
    reason_code: str = Field(min_length=1)
    raw_excerpt: str = ""

    @field_validator("prediction_id")
    @classmethod
    def _fmt(cls, value: str) -> str:
        if not _PRED_ID_RE.match(value):
            raise ValueError(f"prediction_id must be 'pred-<16 hex>': {value!r}")
        return value


DatasetPrediction = Annotated[
    DiagnosisPrediction | AbstentionPrediction | InvalidPrediction,
    Field(discriminator="outcome_kind"),
]


def _prediction_content(
    prediction: DiagnosisPrediction | AbstentionPrediction | InvalidPrediction,
) -> dict[str, object]:
    """The prediction payload used for id derivation (excludes ``prediction_id``)."""
    data: dict[str, object] = prediction.model_dump(mode="json")
    data.pop("prediction_id", None)
    return data


def derive_prediction_id(
    *,
    baseline_id: str,
    task_id: str,
    feature_policy_id: str,
    feature_payload: dict[str, object],
    prediction_content: dict[str, object],
) -> str:
    """Deterministic prediction id from the full binding context (never identity)."""
    payload = {
        "baseline_id": baseline_id,
        "task_id": task_id,
        "feature_policy_id": feature_policy_id,
        "features": feature_payload,
        "prediction": prediction_content,
    }
    return "pred-" + sha256_canonical(payload)[:16]


def verify_prediction_id(
    prediction: DiagnosisPrediction | AbstentionPrediction | InvalidPrediction,
    *,
    baseline_id: str,
    task_id: str,
    feature_policy_id: str,
    feature_payload: dict[str, object],
) -> bool:
    """True iff the prediction's stored id matches a fresh derivation."""
    expected = derive_prediction_id(
        baseline_id=baseline_id, task_id=task_id, feature_policy_id=feature_policy_id,
        feature_payload=feature_payload, prediction_content=_prediction_content(prediction),
    )
    return prediction.prediction_id == expected


def build_diagnosis_prediction(
    *,
    baseline_id: str,
    task_id: str,
    feature_policy_id: str,
    feature_payload: dict[str, object],
    fault_family: str,
    matched_rules: tuple[str, ...] = (),
) -> DiagnosisPrediction:
    content = {
        "schema_version": 1,
        "outcome_kind": "diagnosis",
        "fault_family": fault_family,
        "matched_rules": list(matched_rules),
    }
    pid = derive_prediction_id(
        baseline_id=baseline_id, task_id=task_id, feature_policy_id=feature_policy_id,
        feature_payload=feature_payload, prediction_content=content,
    )
    return DiagnosisPrediction(
        prediction_id=pid, fault_family=fault_family, matched_rules=matched_rules
    )


def build_abstention_prediction(
    *,
    baseline_id: str,
    task_id: str,
    feature_policy_id: str,
    feature_payload: dict[str, object],
    reason_code: str,
    matched_rules: tuple[str, ...] = (),
) -> AbstentionPrediction:
    content = {
        "schema_version": 1,
        "outcome_kind": "abstention",
        "abstain": True,
        "reason_code": reason_code,
        "matched_rules": list(matched_rules),
    }
    pid = derive_prediction_id(
        baseline_id=baseline_id, task_id=task_id, feature_policy_id=feature_policy_id,
        feature_payload=feature_payload, prediction_content=content,
    )
    return AbstentionPrediction(
        prediction_id=pid, reason_code=reason_code, matched_rules=matched_rules
    )


def build_invalid_prediction(
    *,
    baseline_id: str,
    task_id: str,
    feature_policy_id: str,
    feature_payload: dict[str, object],
    reason_code: str,
    raw_excerpt: str = "",
) -> InvalidPrediction:
    excerpt = raw_excerpt[:RAW_EXCERPT_LIMIT]
    content = {
        "schema_version": 1,
        "outcome_kind": "invalid",
        "reason_code": reason_code,
        "raw_excerpt": excerpt,
    }
    pid = derive_prediction_id(
        baseline_id=baseline_id, task_id=task_id, feature_policy_id=feature_policy_id,
        feature_payload=feature_payload, prediction_content=content,
    )
    return InvalidPrediction(
        prediction_id=pid, reason_code=reason_code, raw_excerpt=excerpt
    )
