"""Deterministic baselines + narrow feature-only interface (Gate 7).

A ``Baseline`` receives ONLY ``DatasetFeatures`` — never labels, trace metadata,
a ``SeparatedDatasetExample``, split membership, identity, or a source artifact.
The evaluator explicitly extracts features before calling ``predict``; there is
no convenience path that hands a full example to a baseline.

Each baseline has a frozen, versioned ``BaselineSpec`` whose ``baseline_id`` is a
content hash of its prediction-affecting configuration (name, versions, task id,
rule configuration) — so any rule change changes the id. Baselines are
deliberately transparent lower bounds; they are not semantically intelligent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.features import DatasetFeatures
from verifiednet.evaluation.contract import EvaluationTask
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    build_abstention_prediction,
    build_diagnosis_prediction,
)
from verifiednet.schemas.base import StrictModel

EVIDENCE_RULE_SET_VERSION = 1
FIXED_PRIOR_RULE_SET_VERSION = 1


def derive_baseline_id(
    *,
    schema_version: int,
    baseline_name: str,
    baseline_version: int,
    rule_set_version: int,
    task_id: str,
    rule_configuration: dict[str, str],
) -> str:
    payload = {
        "schema_version": schema_version,
        "baseline_name": baseline_name,
        "baseline_version": baseline_version,
        "rule_set_version": rule_set_version,
        "task_id": task_id,
        "rule_configuration": {k: rule_configuration[k] for k in sorted(rule_configuration)},
    }
    return "baseline-" + sha256_canonical(payload)[:16]


class BaselineSpec(StrictModel):
    """A frozen, content-addressed baseline specification."""

    schema_version: Literal[1] = 1
    baseline_name: str = Field(min_length=1)
    baseline_version: Literal[1] = 1
    rule_set_version: int = Field(ge=1)
    task_id: str = Field(min_length=1)
    rule_configuration: dict[str, str] = Field(default_factory=dict)
    baseline_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> BaselineSpec:
        expected = derive_baseline_id(
            schema_version=self.schema_version, baseline_name=self.baseline_name,
            baseline_version=self.baseline_version, rule_set_version=self.rule_set_version,
            task_id=self.task_id, rule_configuration=self.rule_configuration,
        )
        if self.baseline_id != expected:
            raise ValueError("baseline_id does not match the baseline configuration")
        return self


@runtime_checkable
class Baseline(Protocol):
    """A deterministic baseline: features in, one explicit prediction out."""

    @property
    def spec(self) -> BaselineSpec: ...

    def predict(self, features: DatasetFeatures) -> DiagnosisPrediction | AbstentionPrediction:
        ...


class FixedPriorBaseline:
    """Always predicts one explicit, configured fault family (a trivial prior).

    It never inspects labels and never abstains; its fixed class is explicit in
    the spec's ``rule_configuration``. It is the transparent reference lower bound.
    """

    def __init__(self, *, task: EvaluationTask, fixed_fault_family: str) -> None:
        if not fixed_fault_family:
            raise ValueError("fixed_fault_family must be non-empty")
        self._task_id = task.task_id
        self._family = fixed_fault_family
        cfg = {"fixed_fault_family": fixed_fault_family}
        self._spec = BaselineSpec(
            baseline_name="fixed_prior", rule_set_version=FIXED_PRIOR_RULE_SET_VERSION,
            task_id=task.task_id, rule_configuration=cfg,
            baseline_id=derive_baseline_id(
                schema_version=1, baseline_name="fixed_prior", baseline_version=1,
                rule_set_version=FIXED_PRIOR_RULE_SET_VERSION, task_id=task.task_id,
                rule_configuration=cfg),
        )

    @property
    def spec(self) -> BaselineSpec:
        return self._spec

    def predict(self, features: DatasetFeatures) -> DiagnosisPrediction:
        return build_diagnosis_prediction(
            baseline_id=self._spec.baseline_id, task_id=self._task_id,
            feature_policy_id=features.feature_policy_id,
            feature_payload=features.model_dump(mode="json"),
            fault_family=self._family, matched_rules=("fixed-prior",),
        )


#: Ordered, explicit rules for the evidence-rule baseline. Each is (rule_id,
#: predicate over model-visible features). First match wins; the set is total.
_EVIDENCE_RULES: tuple[tuple[str, Callable[[DatasetFeatures], bool]], ...] = (
    ("R1-no-onset-abstain", lambda f: f.onset_evidence is None),
    ("R2-onset-default-family", lambda f: f.onset_evidence is not None),
)


class EvidenceRuleBaseline:
    """A deterministic rule classifier over the Gate 6 feature ALLOWLIST only.

    Rules may inspect only ``topology_hash``, ``backend``, and the presence of the
    baseline/onset evidence references — never labels, trace, paths, ids,
    rejection codes, failed phase, or split membership. Because the allowlisted
    features intentionally do not reveal the fault family, the rules can only:
    abstain when there is no onset evidence (R1), else predict the configured
    default family (R2). If no rule matched (unreachable — the set is total), it
    falls back to an explicit abstention.
    """

    def __init__(self, *, task: EvaluationTask, default_fault_family: str) -> None:
        if not default_fault_family:
            raise ValueError("default_fault_family must be non-empty")
        self._task_id = task.task_id
        self._default = default_fault_family
        cfg = {
            "default_fault_family": default_fault_family,
            "rules": "|".join(rid for rid, _ in _EVIDENCE_RULES),
        }
        self._spec = BaselineSpec(
            baseline_name="evidence_rule", rule_set_version=EVIDENCE_RULE_SET_VERSION,
            task_id=task.task_id, rule_configuration=cfg,
            baseline_id=derive_baseline_id(
                schema_version=1, baseline_name="evidence_rule", baseline_version=1,
                rule_set_version=EVIDENCE_RULE_SET_VERSION, task_id=task.task_id,
                rule_configuration=cfg),
        )

    @property
    def spec(self) -> BaselineSpec:
        return self._spec

    def predict(self, features: DatasetFeatures) -> DiagnosisPrediction | AbstentionPrediction:
        payload = features.model_dump(mode="json")
        for rule_id, predicate in _EVIDENCE_RULES:
            if predicate(features):
                if rule_id == "R1-no-onset-abstain":
                    return build_abstention_prediction(
                        baseline_id=self._spec.baseline_id, task_id=self._task_id,
                        feature_policy_id=features.feature_policy_id,
                        feature_payload=payload, reason_code="no_onset_evidence",
                        matched_rules=(rule_id,),
                    )
                return build_diagnosis_prediction(
                    baseline_id=self._spec.baseline_id, task_id=self._task_id,
                    feature_policy_id=features.feature_policy_id,
                    feature_payload=payload, fault_family=self._default,
                    matched_rules=(rule_id,),
                )
        # Explicit deterministic fallback (unreachable: the rule set is total).
        return build_abstention_prediction(
            baseline_id=self._spec.baseline_id, task_id=self._task_id,
            feature_policy_id=features.feature_policy_id, feature_payload=payload,
            reason_code="no_rule_matched", matched_rules=(),
        )
