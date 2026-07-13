"""Evaluation-task contract + deterministic normalization (Gate 7).

The ``EvaluationTask`` is the frozen, versioned definition of exactly what a
baseline must predict and how a prediction is scored against authoritative
truth. Its ``task_id`` is a pure content hash (no time/host/user/env/path), and
the model validates its own derived id.

The first supported task is single-fault-family diagnosis with abstention:
accepted examples must predict the fault family; rejected (abstention) examples
must predict abstention.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.models import DatasetPartition
from verifiednet.schemas.base import StrictModel

EVALUATION_TASK_VERSION = 1
SCORING_POLICY_VERSION = 1
NORMALIZATION_POLICY_VERSION = 1


class AcceptedTargetType(StrEnum):
    FAULT_FAMILY = "fault_family"


class NormalizationPolicy(StrictModel):
    """A versioned, deterministic string normalizer for label/prediction compare.

    Only explicit, unambiguous rules — surrounding-whitespace strip and case
    folding. No fuzzy matching, edit distance, embeddings, or synonyms. Changing
    the rules must change ``policy_id`` (and therefore the task id).
    """

    schema_version: Literal[1] = 1
    version: Literal[1] = 1
    strip: bool = True
    casefold: bool = True

    def normalize(self, value: str) -> str:
        out = value
        if self.strip:
            out = out.strip()
        if self.casefold:
            out = out.casefold()
        return out

    @property
    def policy_id(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "version": self.version,
            "strip": self.strip,
            "casefold": self.casefold,
        }
        return "norm-" + sha256_canonical(payload)[:16]


def _task_payload(
    *,
    schema_version: int,
    task_version: int,
    task_name: str,
    accepted_target_type: AcceptedTargetType,
    abstention_target: str,
    permitted_partitions: tuple[DatasetPartition, ...],
    scoring_policy_version: int,
    normalization_policy_id: str,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "task_version": task_version,
        "task_name": task_name,
        "accepted_target_type": accepted_target_type.value,
        "abstention_target": abstention_target,
        "permitted_partitions": sorted(p.value for p in permitted_partitions),
        "scoring_policy_version": scoring_policy_version,
        "normalization_policy_id": normalization_policy_id,
    }


def derive_task_id(
    *,
    schema_version: int,
    task_version: int,
    task_name: str,
    accepted_target_type: AcceptedTargetType,
    abstention_target: str,
    permitted_partitions: tuple[DatasetPartition, ...],
    scoring_policy_version: int,
    normalization_policy_id: str,
) -> str:
    return "task-" + sha256_canonical(_task_payload(
        schema_version=schema_version, task_version=task_version, task_name=task_name,
        accepted_target_type=accepted_target_type, abstention_target=abstention_target,
        permitted_partitions=permitted_partitions,
        scoring_policy_version=scoring_policy_version,
        normalization_policy_id=normalization_policy_id,
    ))[:16]


class EvaluationTask(StrictModel):
    """The frozen, content-addressed definition of one evaluation task."""

    schema_version: Literal[1] = 1
    task_version: Literal[1] = 1
    task_name: str = Field(min_length=1)
    accepted_target_type: AcceptedTargetType = AcceptedTargetType.FAULT_FAMILY
    abstention_target: Literal["abstain"] = "abstain"
    permitted_partitions: tuple[DatasetPartition, ...] = Field(min_length=1)
    scoring_policy_version: Literal[1] = 1
    normalization: NormalizationPolicy = NormalizationPolicy()
    task_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationTask:
        if len(set(self.permitted_partitions)) != len(self.permitted_partitions):
            raise ValueError("permitted_partitions must be unique")
        expected = derive_task_id(
            schema_version=self.schema_version, task_version=self.task_version,
            task_name=self.task_name, accepted_target_type=self.accepted_target_type,
            abstention_target=self.abstention_target,
            permitted_partitions=self.permitted_partitions,
            scoring_policy_version=self.scoring_policy_version,
            normalization_policy_id=self.normalization.policy_id,
        )
        if self.task_id != expected:
            raise ValueError("task_id does not match the task contract")
        return self


def diagnosis_task(
    *,
    task_name: str = "single_fault_family_diagnosis",
    permitted_partitions: tuple[DatasetPartition, ...] = (
        DatasetPartition.TRAIN, DatasetPartition.VALIDATION,
        DatasetPartition.TEST, DatasetPartition.ABSTENTION,
    ),
    normalization: NormalizationPolicy | None = None,
) -> EvaluationTask:
    """Build the canonical Gate 7 diagnosis+abstention task with its derived id."""
    norm = normalization or NormalizationPolicy()
    task_id = derive_task_id(
        schema_version=1, task_version=EVALUATION_TASK_VERSION, task_name=task_name,
        accepted_target_type=AcceptedTargetType.FAULT_FAMILY, abstention_target="abstain",
        permitted_partitions=permitted_partitions,
        scoring_policy_version=SCORING_POLICY_VERSION,
        normalization_policy_id=norm.policy_id,
    )
    return EvaluationTask(
        task_name=task_name, permitted_partitions=permitted_partitions,
        normalization=norm, task_id=task_id,
    )
