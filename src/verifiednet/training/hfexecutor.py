"""The authorized real-training executor boundary (Gate 10F).

``AuthorizedTrainingExecutor`` is the ONLY way to reach real weight mutation,
and its signature makes skipping preflight impossible: it requires the
verified plan, corpus, AUTHORIZATION artifact, local model/tokenizer
directories, and the bounded slice + execution policies. There is no
lower-level public API that accepts a plan and skips authorization.

Two engines implement the hot section behind one orchestration:

* ``StubTrainingEngine``  — deterministic, dependency-free: synthetic finite
  losses and a structurally valid safetensors payload derived from the
  execution identity. It exercises EVERY structural path (revalidation,
  bounds, events, persistence, checkpoint candidate boundary) offline.
* ``HFTrainingEngine``    — the real PyTorch + Transformers full fine-tune.
  ALL ML imports are lazy (function-level) inside this class; this module is
  the single sanctioned lazy-ML-import site in ``verifiednet.training`` (the
  AST boundary guard allowlists exactly this file and separately asserts the
  imports are lazy). It runs strictly local-files-only; a cache miss refuses,
  it never downloads.

Sequence (orchestrated identically for both engines):

    revalidate authorization  →  enforce every bound (BEFORE model loading)
    →  deterministic slice re-selection (must match the declared policy)
    →  hot section (engine)  →  events + result  →  write real checkpoint
    (candidate → verified)  →  write real execution artifact

Failures during the hot section become FAILED execution artifacts with a
structured failure class and no checkpoint; refusals before any loading raise
``RealExecutionError`` — nothing executed, so no execution artifact exists.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol, runtime_checkable

from verifiednet.common.hashing import sha256_canonical
from verifiednet.training.authstore import (
    LoadedAuthorization,
    read_training_authorization,
)
from verifiednet.training.backend import (
    TrainingDeviceCapability,
    build_device_capability,
)
from verifiednet.training.bounds import (
    BoundedCorpusSlicePolicy,
    BoundedTrainingModelPolicy,
    RealTrainingExecutionPolicy,
    TrainingObjectivePolicy,
    select_corpus_slice,
)
from verifiednet.training.execution import ExecutionState
from verifiednet.training.localresolve import (
    LocalModelArtifactResolver,
    LocalTokenizerArtifactResolver,
)
from verifiednet.training.planstore import LoadedTrainingPlan, read_training_plan
from verifiednet.training.realckptstore import (
    RealCandidateFile,
    RealCheckpointCandidate,
    RealCheckpointCompatibility,
    RealCheckpointFileRole,
    RealCheckpointLineage,
    build_minimal_safetensors,
    build_real_checkpoint_format_spec,
    count_safetensors_parameters,
    derive_real_checkpoint_id,
    derive_real_compatibility_id,
    derive_real_lineage_id,
    parse_safetensors_header,
    write_real_checkpoint,
)
from verifiednet.training.realexec import (
    ConsistencyClass,
    RealExecutionError,
    RealExecutionEvent,
    RealExecutionEventType,
    RealFailureClass,
    RealTrainingExecution,
    RealTrainingExecutionResult,
    build_real_event,
    derive_real_execution_digest,
    derive_real_execution_id,
    revalidate_authorization,
)
from verifiednet.training.realexecstore import (
    WrittenRealExecution,
    write_real_execution,
)
from verifiednet.training.resolve import (
    ArtifactResolutionError,
    ResolvedModelArtifact,
    ResolvedTokenizerArtifact,
)
from verifiednet.training.spec import TrainingSpec
from verifiednet.training.store import TrainingPair
from verifiednet.training.trainer import (
    compute_batches_per_epoch,
    compute_optimizer_steps_per_epoch,
)

STUB_ENGINE_ID = "stub-real-training-engine-v1"
HF_ENGINE_ID = "hf-real-training-engine-v1"


@dataclass(frozen=True)
class EngineOutcome:
    """What the hot section reports back. Backend-reported evidence only."""

    losses_per_epoch: tuple[tuple[str, ...], ...]
    applied_deterministic_settings: tuple[str, ...]
    #: role -> (relative_path, serialization_id, content bytes)
    checkpoint_payload: dict[RealCheckpointFileRole, tuple[str, str, bytes]]


class TrainingEngineError(RealExecutionError):
    """The hot section failed; carries a structured failure class."""

    def __init__(self, failure_class: RealFailureClass, detail: str) -> None:
        super().__init__(f"{failure_class.value}: {detail}")
        self.failure_class = failure_class
        self.detail = detail


@runtime_checkable
class AuthorizedTrainingExecutor(Protocol):
    """The real execution boundary. Authorization is a REQUIRED argument."""

    def execute(
        self,
        *,
        plan_dir: str | Path,
        corpus_dir: str | Path,
        authorization_dir: str | Path,
        model_dir: str | Path,
        tokenizer_dir: str | Path,
        output_root: str | Path,
        model_policy: BoundedTrainingModelPolicy,
        slice_policy: BoundedCorpusSlicePolicy,
        execution_policy: RealTrainingExecutionPolicy,
        objective_policy: TrainingObjectivePolicy,
    ) -> WrittenRealExecution: ...


def derive_execution_evidence_digest(
    execution_id: str, event_hashes: tuple[str, ...],
) -> str:
    """Digest over the evidence available BEFORE the checkpoint exists.

    The checkpoint lineage binds this (execution id + pre-checkpoint event
    hashes); the execution result then binds the checkpoint id — no circular
    hash. The full execution digest additionally covers the result.
    """
    return "rexecevd-" + sha256_canonical({
        "execution_id": execution_id, "event_hashes": list(event_hashes)})[:24]


class _EventLog:
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.items: list[RealExecutionEvent] = []
        self._prev = execution_id
        self.steps = 0

    def emit(self, event_type: RealExecutionEventType,
             state_before: ExecutionState, state_after: ExecutionState,
             *, completed_steps: int | None = None,
             epoch_index: int | None = None, batch_index: int | None = None,
             step_index: int | None = None, loss: str | None = None,
             detail_code: str = "",
             consistency: ConsistencyClass = (
                 ConsistencyClass.STRUCTURALLY_VERIFIED)) -> None:
        event = build_real_event(
            execution_id=self.execution_id, sequence=len(self.items),
            event_type=event_type, state_before=state_before,
            state_after=state_after,
            completed_steps=(completed_steps if completed_steps is not None
                             else self.steps),
            epoch_index=epoch_index, batch_index=batch_index,
            step_index=step_index, loss=loss, detail_code=detail_code,
            consistency=consistency, prev_event_hash=self._prev)
        self.items.append(event)
        self._prev = event.event_hash
        self.steps = event.completed_steps


class RealTrainingExecutor:
    """Shared orchestration around one injected engine (stub or HF)."""

    def __init__(self, engine: StubTrainingEngine | HFTrainingEngine) -> None:
        self._engine = engine

    def execute(
        self,
        *,
        plan_dir: str | Path,
        corpus_dir: str | Path,
        authorization_dir: str | Path,
        model_dir: str | Path,
        tokenizer_dir: str | Path,
        output_root: str | Path,
        model_policy: BoundedTrainingModelPolicy,
        slice_policy: BoundedCorpusSlicePolicy,
        execution_policy: RealTrainingExecutionPolicy,
        objective_policy: TrainingObjectivePolicy,
    ) -> WrittenRealExecution:
        loaded_plan = read_training_plan(plan_dir)
        plan = loaded_plan.plan
        spec = plan.request.spec
        if plan.expected_epochs is None:
            raise RealExecutionError(
                "the bounded first run supports epoch budgets only")
        if execution_policy.objective_policy_id != (
                objective_policy.objective_policy_id):
            raise RealExecutionError(
                "execution policy binds a different training objective")

        if slice_policy.corpus_slice_id != execution_policy.corpus_slice_id:
            raise RealExecutionError(
                "the execution policy binds a different corpus slice")

        # local, content-hashed resolution — an absolute path locates, content
        # identifies; a hub name alone is never identity
        try:
            model_artifact = LocalModelArtifactResolver(model_dir).resolve(
                spec.model)
            tokenizer_artifact = LocalTokenizerArtifactResolver(
                tokenizer_dir).resolve(spec.tokenizer)
        except ArtifactResolutionError as exc:
            raise RealExecutionError(
                f"local artifact resolution refused execution: {exc}") from exc

        # authorization revalidation + EVERY bound, before any model loading
        ok, checks = revalidate_authorization(
            authorization_dir, plan_dir=plan_dir,
            model_artifact=model_artifact,
            tokenizer_artifact=tokenizer_artifact,
            model_policy=model_policy, execution_policy=execution_policy)
        if not ok:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in checks
                               if not c.passed)
            raise RealExecutionError(
                f"authorization revalidation refused execution: {detail}")
        loaded_auth = read_training_authorization(authorization_dir)
        auth_digest = loaded_auth.manifest.authorization_digest

        # deterministic slice re-selection must reproduce the declared policy
        reselected, pairs = select_corpus_slice(
            corpus_dir, max_example_count=slice_policy.max_example_count)
        if reselected.corpus_slice_id != slice_policy.corpus_slice_id:
            raise RealExecutionError(
                "corpus slice re-selection does not match the declared slice")

        batches = compute_batches_per_epoch(
            len(pairs), spec.batch.per_device_batch_size)
        steps_per_epoch = compute_optimizer_steps_per_epoch(
            batches, spec.batch.gradient_accumulation_steps)
        slice_expected_steps = plan.expected_epochs * steps_per_epoch
        if slice_expected_steps > execution_policy.max_runtime_optimizer_steps:
            raise RealExecutionError(
                "slice-derived steps exceed the execution policy bound")

        execution_id = derive_real_execution_id(
            training_plan_id=plan.training_plan_id,
            plan_digest=loaded_plan.manifest.plan_digest,
            authorization_id=loaded_auth.authorization.authorization_id,
            authorization_digest=auth_digest,
            backend_spec_id=loaded_auth.authorization.backend_spec_id,
            model_artifact_id=model_artifact.resolved_model_artifact_id,
            tokenizer_artifact_id=(
                tokenizer_artifact.resolved_tokenizer_artifact_id),
            bounded_model_policy_id=model_policy.bounded_model_policy_id,
            corpus_slice_id=slice_policy.corpus_slice_id,
            real_execution_policy_id=(
                execution_policy.real_execution_policy_id))

        log = _EventLog(execution_id)
        S, E = ExecutionState, RealExecutionEventType
        log.emit(E.AUTHORIZATION_ACCEPTED, S.PLANNED, S.VALIDATED,
                 detail_code=loaded_auth.authorization.authorization_id)
        log.emit(E.MODEL_ARTIFACT_VERIFIED, S.VALIDATED, S.VALIDATED,
                 detail_code=model_artifact.resolved_model_artifact_id)
        log.emit(E.TOKENIZER_ARTIFACT_VERIFIED, S.VALIDATED, S.VALIDATED,
                 detail_code=tokenizer_artifact.resolved_tokenizer_artifact_id)
        log.emit(E.CORPUS_SLICE_LOADED, S.VALIDATED, S.VALIDATED,
                 detail_code=slice_policy.corpus_slice_id)

        failure: TrainingEngineError | None = None
        outcome: EngineOutcome | None = None
        try:
            outcome = self._engine.run(
                spec=spec, pairs=pairs, objective=objective_policy,
                model_dir=Path(model_dir), tokenizer_dir=Path(tokenizer_dir),
                planned_epochs=plan.expected_epochs,
                steps_per_epoch=steps_per_epoch,
                max_steps=min(plan.optimizer_steps,
                              execution_policy.max_runtime_optimizer_steps),
                execution_seed=execution_id, log=log)
        except TrainingEngineError as exc:
            failure = exc

        if failure is not None or outcome is None:
            assert failure is not None
            log.emit(E.EXECUTION_FAILED, S.RUNNING, S.FAILED,
                     detail_code=failure.failure_class.value,
                     consistency=ConsistencyClass.STRUCTURALLY_VERIFIED)
            result = RealTrainingExecutionResult(
                final_state=S.FAILED,
                completed_optimizer_steps=log.steps,
                completed_epochs=0, observed_losses=(),
                applied_deterministic_settings=(),
                failure_class=failure.failure_class,
                failure_detail=failure.detail)
            execution = self._assemble(
                execution_id, loaded_plan, loaded_auth, model_artifact,
                tokenizer_artifact, model_policy, slice_policy,
                execution_policy, plan.optimizer_steps, slice_expected_steps,
                tuple(log.items), result)
            return write_real_execution(
                execution, Path(output_root) / "real-training-executions",
                training_corpus_id=spec.training_corpus_id,
                training_corpus_digest=spec.training_corpus_digest,
                determinism_category=(
                    loaded_auth.authorization.determinism_category.value))

        # completed: build the checkpoint through the candidate boundary
        losses = tuple(loss for epoch in outcome.losses_per_epoch
                       for loss in epoch)
        log.emit(E.TRAINING_COMPLETED, S.RUNNING, S.COMPLETED,
                 consistency=ConsistencyClass.STRUCTURALLY_VERIFIED)
        evidence_digest = derive_execution_evidence_digest(
            execution_id, tuple(e.event_hash for e in log.items))
        lineage_fields: dict[str, object] = {
            "real_execution_id": execution_id,
            "real_execution_digest": evidence_digest,
            "authorization_id": loaded_auth.authorization.authorization_id,
            "authorization_digest": auth_digest,
            "training_plan_id": plan.training_plan_id,
            "plan_digest": loaded_plan.manifest.plan_digest,
            "training_spec_id": spec.training_spec_id,
            "training_corpus_id": spec.training_corpus_id,
            "training_corpus_digest": spec.training_corpus_digest,
            "corpus_slice_id": slice_policy.corpus_slice_id,
            "model_artifact_id": model_artifact.resolved_model_artifact_id,
            "tokenizer_artifact_id":
                tokenizer_artifact.resolved_tokenizer_artifact_id,
            "backend_spec_id": loaded_auth.authorization.backend_spec_id,
            "real_execution_policy_id":
                execution_policy.real_execution_policy_id,
            "completed_optimizer_steps": log.steps,
            "parent_checkpoint_id": None,
        }
        lineage_probe = RealCheckpointLineage.model_construct(**lineage_fields)  # type: ignore[arg-type]
        lineage = RealCheckpointLineage(
            **lineage_fields,  # type: ignore[arg-type]
            lineage_id=derive_real_lineage_id(lineage_probe))
        format_spec = build_real_checkpoint_format_spec()
        compat_probe = RealCheckpointCompatibility.model_construct(
            format_spec_id=format_spec.format_spec_id,
            model_spec_id=spec.model.model_spec_id,
            tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
            architecture_id=spec.model.model_class)
        compatibility = RealCheckpointCompatibility(
            format_spec_id=format_spec.format_spec_id,
            model_spec_id=spec.model.model_spec_id,
            tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
            architecture_id=spec.model.model_class,
            compatibility_id=derive_real_compatibility_id(compat_probe))
        files = tuple(sorted(
            (RealCandidateFile(relative_path=path, role=role,
                               serialization_id=serialization, content=blob)
             for role, (path, serialization, blob)
             in outcome.checkpoint_payload.items()),
            key=lambda f: f.relative_path))
        checkpoint_id = derive_real_checkpoint_id(
            format_spec_id=format_spec.format_spec_id,
            lineage_id=lineage.lineage_id,
            declared_file_roles=tuple(f.role for f in files),
            model_spec_id=spec.model.model_spec_id,
            tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
            checkpoint_version=1)
        candidate = RealCheckpointCandidate(
            producer_id=self._engine.engine_id,
            intended_checkpoint_id=checkpoint_id, lineage=lineage,
            format_spec=format_spec, compatibility=compatibility, files=files)
        written_ckpt = write_real_checkpoint(
            candidate, Path(output_root) / "real-checkpoints")
        log.emit(E.CHECKPOINT_PRODUCED, S.COMPLETED, S.COMPLETED,
                 detail_code=written_ckpt.checkpoint_id,
                 consistency=ConsistencyClass.STRUCTURALLY_VERIFIED)

        result = RealTrainingExecutionResult(
            final_state=S.COMPLETED, completed_optimizer_steps=log.steps,
            completed_epochs=len(outcome.losses_per_epoch),
            observed_losses=losses,
            applied_deterministic_settings=(
                outcome.applied_deterministic_settings),
            produced_checkpoint_id=written_ckpt.checkpoint_id)
        execution = self._assemble(
            execution_id, loaded_plan, loaded_auth, model_artifact,
            tokenizer_artifact, model_policy, slice_policy, execution_policy,
            plan.optimizer_steps, slice_expected_steps, tuple(log.items),
            result)
        return write_real_execution(
            execution, Path(output_root) / "real-training-executions",
            training_corpus_id=spec.training_corpus_id,
            training_corpus_digest=spec.training_corpus_digest,
            determinism_category=(
                loaded_auth.authorization.determinism_category.value))

    @staticmethod
    def _assemble(execution_id: str, loaded_plan: LoadedTrainingPlan,
                  loaded_auth: LoadedAuthorization,
                  model_artifact: ResolvedModelArtifact,
                  tokenizer_artifact: ResolvedTokenizerArtifact,
                  model_policy: BoundedTrainingModelPolicy,
                  slice_policy: BoundedCorpusSlicePolicy,
                  execution_policy: RealTrainingExecutionPolicy,
                  planned_steps: int, slice_steps: int,
                  events: tuple[RealExecutionEvent, ...],
                  result: RealTrainingExecutionResult) -> RealTrainingExecution:
        fields: dict[str, object] = {
            "execution_id": execution_id,
            "training_plan_id": loaded_plan.plan.training_plan_id,
            "plan_digest": loaded_plan.manifest.plan_digest,
            "authorization_id": loaded_auth.authorization.authorization_id,
            "authorization_digest":
                loaded_auth.manifest.authorization_digest,
            "backend_spec_id": loaded_auth.authorization.backend_spec_id,
            "model_artifact_id": model_artifact.resolved_model_artifact_id,
            "tokenizer_artifact_id":
                tokenizer_artifact.resolved_tokenizer_artifact_id,
            "execution_policy": execution_policy,
            "slice_policy": slice_policy,
            "planned_optimizer_steps": planned_steps,
            "slice_expected_steps": slice_steps,
            "events": events,
            "result": result,
        }
        probe = RealTrainingExecution.model_construct(**fields)  # type: ignore[arg-type]
        return RealTrainingExecution(
            **fields,  # type: ignore[arg-type]
            execution_digest=derive_real_execution_digest(probe))


class StubTrainingEngine:
    """Deterministic offline hot section: no ML import, no weight training.

    Synthetic finite losses derive from the execution id; the checkpoint
    payload is a structurally valid safetensors blob whose bytes derive from
    the same seed — deliberately DIFFERENT from the source model bytes, so the
    structural pipeline (candidate → writer → verifier → lineage) is fully
    exercised offline. Build-twice is byte-identical.
    """

    engine_id = STUB_ENGINE_ID

    def run(self, *, spec: TrainingSpec, pairs: tuple[TrainingPair, ...],
            objective: TrainingObjectivePolicy, model_dir: Path,
            tokenizer_dir: Path, planned_epochs: int, steps_per_epoch: int,
            max_steps: int, execution_seed: str, log: _EventLog) -> EngineOutcome:
        S, E = ExecutionState, RealExecutionEventType
        log.emit(E.TOKENIZATION_COMPLETED, S.VALIDATED, S.VALIDATED,
                 detail_code=objective.objective_policy_id,
                 consistency=ConsistencyClass.RECOMPUTABLE)
        log.emit(E.MODEL_LOADED, S.VALIDATED, S.STARTING,
                 detail_code="stub", consistency=ConsistencyClass.BACKEND_REPORTED)
        log.emit(E.TOKENIZER_LOADED, S.STARTING, S.STARTING, detail_code="stub",
                 consistency=ConsistencyClass.BACKEND_REPORTED)
        log.emit(E.OPTIMIZER_INITIALIZED, S.STARTING, S.STARTING,
                 detail_code=spec.optimization.optimizer_name,
                 consistency=ConsistencyClass.BACKEND_REPORTED)
        log.emit(E.SCHEDULER_INITIALIZED, S.STARTING, S.STARTING,
                 detail_code=spec.scheduler.scheduler_name,
                 consistency=ConsistencyClass.BACKEND_REPORTED)
        log.emit(E.TRAINING_STARTED, S.STARTING, S.RUNNING,
                 consistency=ConsistencyClass.STRUCTURALLY_VERIFIED)
        losses: list[tuple[str, ...]] = []
        step = 0
        for epoch in range(planned_epochs):
            epoch_losses: list[str] = []
            for _ in range(steps_per_epoch):
                if step >= max_steps:
                    raise TrainingEngineError(
                        RealFailureClass.BOUNDS_EXCEEDED,
                        "step budget exhausted mid-epoch")
                step += 1
                digest = sha256_canonical({"seed": execution_seed, "step": step})
                loss = str((Decimal(int(digest[:6], 16) % 900_000)
                            / Decimal(1_000_000)) + Decimal("0.1"))
                epoch_losses.append(loss)
                log.emit(E.OPTIMIZER_STEP_COMPLETED, S.RUNNING, S.RUNNING,
                         completed_steps=step, epoch_index=epoch,
                         step_index=step, loss=loss,
                         consistency=ConsistencyClass.BACKEND_REPORTED)
            losses.append(tuple(epoch_losses))
            log.emit(E.EPOCH_COMPLETED, S.RUNNING, S.RUNNING,
                     completed_steps=step, epoch_index=epoch,
                     consistency=ConsistencyClass.STRUCTURALLY_VERIFIED)

        source = (model_dir / "model.safetensors").read_bytes()
        header = parse_safetensors_header(source)
        tensors: dict[str, tuple[tuple[int, ...], bytes]] = {}
        for name, entry in header.items():
            if name == "__metadata__":
                continue
            assert isinstance(entry, dict)
            shape = tuple(int(d) for d in entry["shape"])
            n_bytes = 4
            for dim in shape:
                n_bytes *= dim
            seed_hash = hashlib.sha256(
                f"{execution_seed}:{name}".encode()).digest()
            raw = b""
            counter = 0
            while len(raw) < n_bytes:
                raw += hashlib.sha256(seed_hash + struct.pack("<I", counter)
                                      ).digest()
                counter += 1
            tensors[name] = (shape, raw[:n_bytes])
        weights = build_minimal_safetensors(tensors)
        assert count_safetensors_parameters(weights) == (
            count_safetensors_parameters(source))
        from verifiednet.common.canonical import canonical_json_bytes

        meta = canonical_json_bytes({
            "schema_version": 1, "artifact_kind": "full_model_checkpoint",
            "payload_format": "verifiednet.real-checkpoint-v1",
            "produced_by": self.engine_id, "completed_steps": step})
        payload = {
            RealCheckpointFileRole.CHECKPOINT_METADATA:
                ("payload/checkpoint.json", "canonical-json-v1", meta),
            RealCheckpointFileRole.MODEL_CONFIG:
                ("payload/config.json", "json-v1",
                 (model_dir / "config.json").read_bytes()),
            RealCheckpointFileRole.MODEL_WEIGHTS:
                ("payload/model.safetensors", "safetensors-v1", weights),
            RealCheckpointFileRole.TOKENIZER_SNAPSHOT:
                ("payload/tokenizer.json", "json-v1",
                 (tokenizer_dir / "tokenizer.json").read_bytes()),
        }
        return EngineOutcome(
            losses_per_epoch=tuple(losses),
            applied_deterministic_settings=(
                "data_order_canonical", "stub_deterministic_engine"),
            checkpoint_payload=payload)


class HFTrainingEngine:
    """The REAL hot section: PyTorch + Transformers full fine-tuning.

    Every ML import is lazy (function-level). Strictly local-files-only:
    offline mode is forced before any Transformers call, so a cache miss is a
    structured refusal, never a download. Exercised only by the explicitly
    enabled integration test — never by offline CI.
    """

    engine_id = HF_ENGINE_ID

    def run(self, *, spec: TrainingSpec, pairs: tuple[TrainingPair, ...],
            objective: TrainingObjectivePolicy, model_dir: Path,
            tokenizer_dir: Path, planned_epochs: int, steps_per_epoch: int,
            max_steps: int, execution_seed: str, log: _EventLog) -> EngineOutcome:
        import os as _os

        _os.environ["HF_HUB_OFFLINE"] = "1"
        _os.environ["TRANSFORMERS_OFFLINE"] = "1"
        S, E = ExecutionState, RealExecutionEventType
        try:
            import torch  # type: ignore[import-not-found, unused-ignore]
        except ImportError as exc:
            raise TrainingEngineError(
                RealFailureClass.MODEL_LOAD_FAILED,
                f"torch unavailable: {exc}") from exc
        try:
            from transformers import (  # type: ignore[import-not-found, unused-ignore]
                AutoModelForCausalLM,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise TrainingEngineError(
                RealFailureClass.MODEL_LOAD_FAILED,
                f"transformers unavailable: {exc}") from exc

        applied: list[str] = []
        import random as _random

        _random.seed(spec.seed_policy.data_order_seed)
        torch.manual_seed(spec.seed_policy.model_init_seed)
        torch.use_deterministic_algorithms(True, warn_only=True)
        applied += ["python_seed", "torch_manual_seed",
                    "torch_deterministic_algorithms_warn_only",
                    "data_order_canonical_no_shuffle"]

        try:
            tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call, unused-ignore]
                str(tokenizer_dir), local_files_only=True)
        except Exception as exc:
            raise TrainingEngineError(
                RealFailureClass.TOKENIZER_LOAD_FAILED, str(exc)) from exc
        log.emit(E.TOKENIZER_LOADED, S.VALIDATED, S.VALIDATED,
                 consistency=ConsistencyClass.BACKEND_REPORTED)
        try:
            model = AutoModelForCausalLM.from_pretrained(
                str(model_dir), local_files_only=True)
        except Exception as exc:
            raise TrainingEngineError(
                RealFailureClass.MODEL_LOAD_FAILED, str(exc)) from exc
        log.emit(E.MODEL_LOADED, S.VALIDATED, S.STARTING,
                 consistency=ConsistencyClass.BACKEND_REPORTED)

        eos = tokenizer.eos_token_id
        if eos is None:
            raise TrainingEngineError(
                RealFailureClass.TOKENIZATION_FAILED, "tokenizer has no EOS")
        from verifiednet.training.bounds import build_causal_lm_example

        sep_ids = tuple(tokenizer.encode(objective.separator,
                                         add_special_tokens=False))
        encoded = []
        for pair in pairs:
            tokens, labels = build_causal_lm_example(
                input_token_ids=tuple(tokenizer.encode(
                    pair.input_text, add_special_tokens=False)),
                separator_token_ids=sep_ids,
                target_token_ids=tuple(tokenizer.encode(
                    pair.target_text, add_special_tokens=False)),
                eos_token_id=eos,
                max_total_tokens=spec.sequence_policy.max_total_tokens)
            encoded.append((tokens, labels))
        log.emit(E.TOKENIZATION_COMPLETED, S.STARTING, S.STARTING,
                 detail_code=objective.objective_policy_id,
                 consistency=ConsistencyClass.RECOMPUTABLE)

        from decimal import Decimal as _D

        lr = float(_D(spec.optimization.learning_rate))
        optimizer = torch.optim.AdamW(
            model.parameters(), lr=lr,
            weight_decay=float(_D(spec.optimization.weight_decay)),
            betas=(float(_D(spec.optimization.beta1)),
                   float(_D(spec.optimization.beta2))),
            eps=float(_D(spec.optimization.epsilon)))
        log.emit(E.OPTIMIZER_INITIALIZED, S.STARTING, S.STARTING,
                 detail_code="adamw",
                 consistency=ConsistencyClass.BACKEND_REPORTED)
        log.emit(E.SCHEDULER_INITIALIZED, S.STARTING, S.STARTING,
                 detail_code=spec.scheduler.scheduler_name,
                 consistency=ConsistencyClass.BACKEND_REPORTED)
        log.emit(E.TRAINING_STARTED, S.STARTING, S.RUNNING,
                 consistency=ConsistencyClass.STRUCTURALLY_VERIFIED)

        accum = spec.batch.gradient_accumulation_steps
        batch_size = spec.batch.per_device_batch_size
        if spec.optimization.max_grad_norm is None:
            raise TrainingEngineError(
                RealFailureClass.OPTIMIZER_INIT_FAILED,
                "gradient clipping is required but the plan declares no "
                "max_grad_norm")
        clip = float(_D(spec.optimization.max_grad_norm))
        losses: list[tuple[str, ...]] = []
        step = 0
        for epoch in range(planned_epochs):
            epoch_losses: list[str] = []
            batch_in_window = 0
            window_loss = 0.0
            for start in range(0, len(encoded), batch_size):
                batch = encoded[start:start + batch_size]
                max_len = max(len(t) for t, _ in batch)
                pad = tokenizer.pad_token_id or eos
                input_ids = torch.tensor([
                    list(t) + [pad] * (max_len - len(t)) for t, _ in batch])
                label_ids = torch.tensor([
                    list(lb) + [-100] * (max_len - len(lb)) for _, lb in batch])
                out = model(input_ids=input_ids, labels=label_ids)
                loss = out.loss / accum
                if not torch.isfinite(loss):
                    raise TrainingEngineError(
                        RealFailureClass.NON_FINITE_LOSS, "loss not finite")
                loss.backward()
                window_loss += float(loss.detach())
                batch_in_window += 1
                if batch_in_window == accum or start + batch_size >= len(encoded):
                    if step >= max_steps:
                        raise TrainingEngineError(
                            RealFailureClass.BOUNDS_EXCEEDED,
                            "step budget exhausted")
                    torch.nn.utils.clip_grad_norm_(model.parameters(), clip)
                    optimizer.step()
                    optimizer.zero_grad()
                    step += 1
                    loss_str = f"{window_loss:.6f}"
                    epoch_losses.append(loss_str)
                    log.emit(E.OPTIMIZER_STEP_COMPLETED, S.RUNNING, S.RUNNING,
                             completed_steps=step, epoch_index=epoch,
                             step_index=step, loss=loss_str,
                             consistency=ConsistencyClass.BACKEND_REPORTED)
                    batch_in_window = 0
                    window_loss = 0.0
            losses.append(tuple(epoch_losses))
            log.emit(E.EPOCH_COMPLETED, S.RUNNING, S.RUNNING,
                     completed_steps=step, epoch_index=epoch,
                     consistency=ConsistencyClass.STRUCTURALLY_VERIFIED)

        try:
            from safetensors.torch import (  # type: ignore[import-not-found, unused-ignore]
                save as st_save,
            )

            # .clone() breaks storage sharing (e.g. tied embeddings):
            # safetensors refuses shared tensors, and each saved entry must
            # own its bytes.
            weights = st_save(
                {k: v.detach().cpu().clone().contiguous()
                 for k, v in model.state_dict().items()})
        except Exception as exc:
            raise TrainingEngineError(
                RealFailureClass.CHECKPOINT_SERIALIZATION_FAILED,
                str(exc)) from exc
        from verifiednet.common.canonical import canonical_json_bytes

        meta = canonical_json_bytes({
            "schema_version": 1, "artifact_kind": "full_model_checkpoint",
            "payload_format": "verifiednet.real-checkpoint-v1",
            "produced_by": self.engine_id, "completed_steps": step})
        payload = {
            RealCheckpointFileRole.CHECKPOINT_METADATA:
                ("payload/checkpoint.json", "canonical-json-v1", meta),
            RealCheckpointFileRole.MODEL_CONFIG:
                ("payload/config.json", "json-v1",
                 (model_dir / "config.json").read_bytes()),
            RealCheckpointFileRole.MODEL_WEIGHTS:
                ("payload/model.safetensors", "safetensors-v1", weights),
            RealCheckpointFileRole.TOKENIZER_SNAPSHOT:
                ("payload/tokenizer.json", "json-v1",
                 (tokenizer_dir / "tokenizer.json").read_bytes()),
        }
        return EngineOutcome(
            losses_per_epoch=tuple(losses),
            applied_deterministic_settings=tuple(applied),
            checkpoint_payload=payload)


class TorchTrainingEnvironmentProbe:
    """The torch-backed probe deferred by Gate 10E. Lazy torch import; only
    execution-relevant facts; no usernames/hostnames/env-vars/free-memory."""

    def python_implementation(self) -> str:
        import platform

        return platform.python_implementation()

    def python_version(self) -> str:
        import platform

        return platform.python_version()

    def os_family(self) -> str:
        import sys as _sys

        if _sys.platform.startswith("linux"):
            return "linux"
        if _sys.platform == "darwin":
            return "macos"
        return "other"

    def machine_architecture(self) -> str:
        import platform

        return platform.machine() or "unknown"

    def detect_package(self, package_name: str) -> tuple[str | None, bool]:
        import importlib.metadata
        import importlib.util

        try:
            version = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            return None, False
        return version, importlib.util.find_spec(package_name) is not None

    def device_capability(self) -> TrainingDeviceCapability:
        try:
            import torch
        except ImportError:
            return build_device_capability(
                device_type="cpu", declared_device_count=0,
                selected_device_index=0, supported_precisions=("float32",),
                total_memory_bytes=0,
                deterministic_operations_supported=False)
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return build_device_capability(
                device_type="cuda",
                declared_device_count=torch.cuda.device_count(),
                selected_device_index=0,
                supported_precisions=("bfloat16", "float32"),
                total_memory_bytes=int(props.total_memory),
                deterministic_operations_supported=True)
        import os as _os

        try:  # POSIX total physical memory (stable machine fact, stdlib only)
            total = int(_os.sysconf("SC_PAGE_SIZE") * _os.sysconf("SC_PHYS_PAGES"))
        except (ValueError, OSError):  # pragma: no cover
            total = 0
        return build_device_capability(
            device_type="cpu", declared_device_count=1,
            selected_device_index=0,
            supported_precisions=("bfloat16", "float32"),
            total_memory_bytes=total,
            deterministic_operations_supported=True)

    def deterministic_algorithms_supported(self) -> bool:
        try:
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    def model_cache_available(self) -> bool:
        return False  # local resolution proves it; the probe never asserts it

    def tokenizer_cache_available(self) -> bool:
        return False


def build_stub_executor() -> RealTrainingExecutor:
    return RealTrainingExecutor(StubTrainingEngine())


def build_hf_executor() -> RealTrainingExecutor:
    return RealTrainingExecutor(HFTrainingEngine())
