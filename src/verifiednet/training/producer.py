"""Checkpoint eligibility + the deterministic fake producer (Gate 10D).

``assess_checkpoint_eligibility`` decides whether a persisted execution may
legally become a checkpoint — by VERIFYING the full execution and plan
artifacts, never by trusting a state string. ``FakeCheckpointProducer`` then
turns an eligible execution into an UNTRUSTED ``CheckpointCandidate`` whose
payloads are pure deterministic synthetic bytes: no model, no tokenizer, no
ML framework, no randomness, no timestamps, no host information — and no
training rows, labels, example/group ids, trace metadata, or evaluation data.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field

from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel
from verifiednet.training.checkpoint import (
    CHECKPOINT_VERSION,
    FAKE_PAYLOAD_MAGIC,
    CandidateFile,
    CheckpointCandidate,
    CheckpointCompatibility,
    CheckpointError,
    CheckpointFileRole,
    CheckpointFormatSpec,
    CheckpointLineage,
    CheckpointProductionPolicy,
    derive_checkpoint_id,
    derive_compatibility_id,
    derive_lineage_id,
)
from verifiednet.training.execstore import (
    LoadedTrainingExecution,
    read_training_execution,
)
from verifiednet.training.execution import ExecutionState
from verifiednet.training.planstore import LoadedTrainingPlan, read_training_plan

#: Size of the deterministic fake weight payload (bytes after the magic).
FAKE_PAYLOAD_BLOCKS = 8  # 8 * 32 = 256 synthetic bytes


class CheckpointEligibilityResult(StrictModel):
    schema_version: Literal[1] = 1
    eligible: bool
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def assess_checkpoint_eligibility(
    execution_dir: str | Path,
    plan_dir: str | Path,
    format_spec: CheckpointFormatSpec,
    policy: CheckpointProductionPolicy,
    *,
    checkpoints_root: str | Path | None = None,
) -> CheckpointEligibilityResult:
    """May this persisted execution legally become a checkpoint? Fail closed.

    The execution and plan artifacts are fully VERIFIED (read_* verify first);
    a completed-state string alone never establishes eligibility. Failed,
    cancelled, or corrupt executions are rejected structurally.
    """
    checks: list[DatasetCheck] = []

    try:
        loaded_exec = read_training_execution(execution_dir)
    except Exception as exc:
        checks.append(_c("execution_artifact_verifies", False,
                         str(exc).splitlines()[0]))
        return CheckpointEligibilityResult(eligible=False, checks=tuple(checks))
    checks.append(_c("execution_artifact_verifies", True))
    execution = loaded_exec.execution

    try:
        loaded_plan = read_training_plan(plan_dir)
    except Exception as exc:
        checks.append(_c("plan_artifact_verifies", False,
                         str(exc).splitlines()[0]))
        return CheckpointEligibilityResult(eligible=False, checks=tuple(checks))
    checks.append(_c("plan_artifact_verifies", True))
    plan = loaded_plan.plan

    completed = execution.final_state is ExecutionState.COMPLETED
    checks.append(_c(
        "execution_completed", completed,
        "" if completed else f"final_state={execution.final_state}"))

    plan_match = execution.training_plan_id == plan.training_plan_id
    checks.append(_c("execution_plan_binding", plan_match,
                     "" if plan_match else "execution binds a different plan"))

    counts_ok = (
        execution.planned_optimizer_steps == plan.optimizer_steps
        and execution.planned_batches_per_epoch == plan.batches_per_epoch
        and execution.planned_epochs == plan.expected_epochs
        and (not completed
             or execution.completed_optimizer_steps == plan.optimizer_steps))
    checks.append(_c("execution_counts_match_plan", counts_ok))

    retry_ok = (execution.retry_number
                <= execution.execution_policy.max_retries)
    checks.append(_c("execution_retry_permitted", retry_ok))

    compat_ok = (format_spec.artifact_kind in policy.permitted_artifact_kinds
                 and policy.required_execution_state == "completed")
    checks.append(_c("format_policy_compatible", compat_ok))

    if checkpoints_root is not None:
        lineage = build_checkpoint_lineage(loaded_exec, loaded_plan)
        checkpoint_id = derive_checkpoint_id(
            format_spec_id=format_spec.format_spec_id,
            lineage_id=lineage.lineage_id,
            declared_file_roles=format_spec.expected_file_roles,
            simulated=True,
            model_spec_id=lineage.model_spec_id,
            tokenizer_spec_id=lineage.tokenizer_spec_id,
            checkpoint_version=CHECKPOINT_VERSION)
        existing = Path(checkpoints_root) / checkpoint_id
        vacant = not existing.exists()
        checks.append(_c("no_existing_checkpoint", vacant,
                         "" if vacant else f"already exists: {existing}"))

    return CheckpointEligibilityResult(
        eligible=all(c.passed for c in checks), checks=tuple(checks))


def build_checkpoint_lineage(
    loaded_exec: LoadedTrainingExecution, loaded_plan: LoadedTrainingPlan,
) -> CheckpointLineage:
    """Derive the lineage binding from VERIFIED execution + plan artifacts.

    A resumed execution binds through the execution artifact itself (its
    ``resumed_from_execution_id`` lives there); no checkpoint parent is
    invented — none was consumed, because none exists in this gate.
    """
    execution = loaded_exec.execution
    plan = loaded_plan.plan
    spec = plan.request.spec
    probe = CheckpointLineage.model_construct(
        source_execution_id=execution.execution_id,
        source_execution_digest=execution.execution_digest,
        source_training_plan_id=plan.training_plan_id,
        source_plan_digest=loaded_plan.manifest.plan_digest,
        training_request_id=plan.request.request_id,
        training_spec_id=spec.training_spec_id,
        training_corpus_id=spec.training_corpus_id,
        training_corpus_digest=spec.training_corpus_digest,
        model_spec_id=spec.model.model_spec_id,
        tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
        trainer_implementation_id=spec.trainer_implementation_id,
        trainer_capability_id=plan.request.trainer_capability_id,
        execution_policy_id=execution.execution_policy.execution_policy_id,
        retry_number=execution.retry_number,
        parent_checkpoint_id=None)
    return CheckpointLineage(
        source_execution_id=execution.execution_id,
        source_execution_digest=execution.execution_digest,
        source_training_plan_id=plan.training_plan_id,
        source_plan_digest=loaded_plan.manifest.plan_digest,
        training_request_id=plan.request.request_id,
        training_spec_id=spec.training_spec_id,
        training_corpus_id=spec.training_corpus_id,
        training_corpus_digest=spec.training_corpus_digest,
        model_spec_id=spec.model.model_spec_id,
        tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
        trainer_implementation_id=spec.trainer_implementation_id,
        trainer_capability_id=plan.request.trainer_capability_id,
        execution_policy_id=execution.execution_policy.execution_policy_id,
        retry_number=execution.retry_number,
        parent_checkpoint_id=None,
        lineage_id=derive_lineage_id(probe))


def fake_payload_bytes(
    *,
    execution_id: str,
    training_plan_id: str,
    training_spec_id: str,
    model_spec_id: str,
    tokenizer_spec_id: str,
    completed_steps: int,
    format_spec_id: str,
) -> bytes:
    """Deterministic synthetic 'weights': magic + counter-chained SHA-256.

    Derived ONLY from content-addressed identities and the completed step
    count. No randomness, no timestamps, no host data — and structurally no
    training rows, labels, or example identities, because none are inputs.
    """
    seed = sha256_canonical({
        "execution_id": execution_id,
        "training_plan_id": training_plan_id,
        "training_spec_id": training_spec_id,
        "model_spec_id": model_spec_id,
        "tokenizer_spec_id": tokenizer_spec_id,
        "completed_steps": completed_steps,
        "format_spec_id": format_spec_id,
    })
    blocks = [
        hashlib.sha256(f"{seed}:{i}".encode("ascii")).digest()
        for i in range(FAKE_PAYLOAD_BLOCKS)
    ]
    return FAKE_PAYLOAD_MAGIC + b"".join(blocks)


class FakeCheckpointProducer:
    """Deterministic producer of UNTRUSTED fake checkpoint candidates.

    Consumes only: a verified completed execution artifact, its verified plan
    artifact, the fake format spec, and an explicit production policy. Emits
    a candidate whose payload directory is:

        payload/checkpoint.json            (checkpoint metadata)
        payload/config.json                (model configuration METADATA only)
        payload/model.fakebin              (magic-prefixed synthetic bytes)
        payload/tokenizer-metadata.json    (tokenizer compatibility METADATA)

    ``.fakebin`` is deliberately not a real weight extension; the bytes start
    with the fake-checkpoint magic; nothing here can be loaded as a model.
    """

    producer_id = "fake-checkpoint-producer-v1"

    def produce(
        self,
        execution_dir: str | Path,
        plan_dir: str | Path,
        *,
        format_spec: CheckpointFormatSpec,
        policy: CheckpointProductionPolicy,
    ) -> CheckpointCandidate:
        eligibility = assess_checkpoint_eligibility(
            execution_dir, plan_dir, format_spec, policy)
        if not eligibility.eligible:
            detail = "; ".join(
                f"{c.rule}: {c.detail}" for c in eligibility.failures)
            raise CheckpointError(f"execution is not checkpoint-eligible: {detail}")

        loaded_exec = read_training_execution(execution_dir)
        loaded_plan = read_training_plan(plan_dir)
        execution = loaded_exec.execution
        plan = loaded_plan.plan
        spec = plan.request.spec

        lineage = build_checkpoint_lineage(loaded_exec, loaded_plan)
        compat_probe = CheckpointCompatibility.model_construct(
            format_spec_id=format_spec.format_spec_id,
            model_spec_id=lineage.model_spec_id,
            tokenizer_spec_id=lineage.tokenizer_spec_id,
            architecture_id=spec.model.model_class,
            supported_inference_backends=())
        compatibility = CheckpointCompatibility(
            format_spec_id=format_spec.format_spec_id,
            model_spec_id=lineage.model_spec_id,
            tokenizer_spec_id=lineage.tokenizer_spec_id,
            architecture_id=spec.model.model_class,
            compatibility_id=derive_compatibility_id(compat_probe))

        checkpoint_id = derive_checkpoint_id(
            format_spec_id=format_spec.format_spec_id,
            lineage_id=lineage.lineage_id,
            declared_file_roles=format_spec.expected_file_roles,
            simulated=True,
            model_spec_id=lineage.model_spec_id,
            tokenizer_spec_id=lineage.tokenizer_spec_id,
            checkpoint_version=CHECKPOINT_VERSION)

        checkpoint_meta = canonical_json_bytes({
            "schema_version": 1,
            "artifact_kind": format_spec.artifact_kind,
            "payload_format": format_spec.payload_format,
            "simulated": True,
            "checkpoint_id": checkpoint_id,
            "lineage_id": lineage.lineage_id,
            "source_execution_id": execution.execution_id,
            "source_training_plan_id": plan.training_plan_id,
            "completed_optimizer_steps": execution.completed_optimizer_steps,
        })
        model_config_meta = canonical_json_bytes({
            "schema_version": 1,
            "simulated": True,
            "model_spec_id": spec.model.model_spec_id,
            "provider": spec.model.provider,
            "model_identifier": spec.model.model_identifier,
            "model_revision": spec.model.model_revision,
            "model_class": spec.model.model_class,
            "load_precision": spec.model.load_precision,
        })
        tokenizer_meta = canonical_json_bytes({
            "schema_version": 1,
            "simulated": True,
            "tokenizer_spec_id": spec.tokenizer.tokenizer_spec_id,
            "tokenizer_identifier": spec.tokenizer.tokenizer_identifier,
            "tokenizer_revision": spec.tokenizer.tokenizer_revision,
            "tokenizer_class": spec.tokenizer.tokenizer_class,
            "special_vocab_policy": spec.tokenizer.special_vocab_policy,
            "padding_policy": spec.tokenizer.padding_policy,
            "truncation_policy": spec.tokenizer.truncation_policy,
        })
        fake_weights = fake_payload_bytes(
            execution_id=execution.execution_id,
            training_plan_id=plan.training_plan_id,
            training_spec_id=spec.training_spec_id,
            model_spec_id=spec.model.model_spec_id,
            tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
            completed_steps=execution.completed_optimizer_steps,
            format_spec_id=format_spec.format_spec_id)

        files = tuple(sorted((
            CandidateFile(relative_path="payload/checkpoint.json",
                          role=CheckpointFileRole.CHECKPOINT_METADATA,
                          serialization_id="canonical-json-v1",
                          required=True, content=checkpoint_meta),
            CandidateFile(relative_path="payload/config.json",
                          role=CheckpointFileRole.MODEL_CONFIG_METADATA,
                          serialization_id="canonical-json-v1",
                          required=True, content=model_config_meta),
            CandidateFile(relative_path="payload/model.fakebin",
                          role=CheckpointFileRole.FAKE_MODEL_PAYLOAD,
                          serialization_id="fake-bytes-v1",
                          required=True, content=fake_weights),
            CandidateFile(relative_path="payload/tokenizer-metadata.json",
                          role=CheckpointFileRole.TOKENIZER_COMPAT_METADATA,
                          serialization_id="canonical-json-v1",
                          required=True, content=tokenizer_meta),
        ), key=lambda f: f.relative_path))

        return CheckpointCandidate(
            intended_checkpoint_id=checkpoint_id, lineage=lineage,
            format_spec=format_spec, production_policy=policy,
            compatibility=compatibility, files=files)
