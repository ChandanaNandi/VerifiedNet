"""Deterministic predictor registry for benchmarking (Gate 9).

A registry holds predictors to be compared and exposes deterministic, ordered
metadata about each. A predictor's benchmark identifier is its Gate-7
``BaselineSpec.baseline_id`` — the same id the evaluation engine and evaluation
manifests already use — so rule baselines and model-backed predictors are treated
uniformly. Registration ORDER never affects benchmark results: the registry
always yields predictors sorted by identifier.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.evaluation.baseline import Baseline, BaselineSpec
from verifiednet.schemas.base import StrictModel


class PredictorRegistryError(VerifiedNetError):
    """A predictor could not be registered (duplicate identifier, etc.)."""


class PredictorEntry(StrictModel):
    """Frozen, deterministic metadata for one registered predictor."""

    schema_version: Literal[1] = 1
    predictor_identifier: str = Field(min_length=1)
    predictor_spec: BaselineSpec
    supported_task_ids: tuple[str, ...] = Field(min_length=1)
    supported_feature_policy_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> PredictorEntry:
        if self.predictor_identifier != self.predictor_spec.baseline_id:
            raise ValueError("predictor_identifier must equal predictor_spec.baseline_id")
        if self.predictor_spec.task_id not in self.supported_task_ids:
            raise ValueError("supported_task_ids must include the predictor's task_id")
        if sorted(self.supported_task_ids) != list(self.supported_task_ids):
            raise ValueError("supported_task_ids must be sorted")
        if sorted(self.supported_feature_policy_ids) != list(self.supported_feature_policy_ids):
            raise ValueError("supported_feature_policy_ids must be sorted")
        return self


class PredictorRegistry:
    """A deterministic, duplicate-free registry of predictors to benchmark."""

    def __init__(self) -> None:
        self._predictors: dict[str, Baseline] = {}
        self._entries: dict[str, PredictorEntry] = {}

    def register(
        self, predictor: Baseline, *, supported_feature_policy_ids: tuple[str, ...]
    ) -> PredictorEntry:
        """Register a predictor; fail closed on a duplicate identifier."""
        spec: BaselineSpec = predictor.spec
        identifier = spec.baseline_id
        if identifier in self._predictors:
            raise PredictorRegistryError(f"duplicate predictor identifier: {identifier}")
        if not supported_feature_policy_ids:
            raise PredictorRegistryError("supported_feature_policy_ids must be non-empty")
        entry = PredictorEntry(
            predictor_identifier=identifier, predictor_spec=spec,
            supported_task_ids=(spec.task_id,),
            supported_feature_policy_ids=tuple(sorted(set(supported_feature_policy_ids))),
        )
        self._predictors[identifier] = predictor
        self._entries[identifier] = entry
        return entry

    def identifiers(self) -> tuple[str, ...]:
        """All registered identifiers, deterministically sorted."""
        return tuple(sorted(self._predictors))

    def entries(self) -> tuple[PredictorEntry, ...]:
        """All entries, deterministically ordered by identifier."""
        return tuple(self._entries[i] for i in self.identifiers())

    def predictors(self) -> tuple[Baseline, ...]:
        """All predictors, deterministically ordered by identifier."""
        return tuple(self._predictors[i] for i in self.identifiers())

    def get(self, identifier: str) -> Baseline:
        if identifier not in self._predictors:
            raise PredictorRegistryError(f"unknown predictor identifier: {identifier}")
        return self._predictors[identifier]

    def __len__(self) -> int:
        return len(self._predictors)
