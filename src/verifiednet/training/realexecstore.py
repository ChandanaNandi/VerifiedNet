"""Immutable real-execution persistence (Gate 10F).

    real-training-executions/<execution_id>/
        manifest.json                (ids, counts, digest, file hashes)
        authorization-binding.json   (auth id/digest + artifact ids)
        events.jsonl                 (ordered real events, canonical JSON)
        result.json                  (RealTrainingExecutionResult)

No raw training data, no model bytes, no timestamps, no host facts. The
checkpoint lives in the separate real-checkpoint store; the execution binds
it by id only (payload bytes are bound through the checkpoint's own verified
digest, never hashed here directly). The verifier recomputes every safely
recomputable value; it never claims to replay losses, gradients, weights, or
kernel behavior (ADR-0027).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes, canonical_json_str
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import DatasetFileHash
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel
from verifiednet.training.bounds import (
    BoundedCorpusSlicePolicy,
    RealTrainingExecutionPolicy,
)
from verifiednet.training.execution import ExecutionState
from verifiednet.training.realexec import (
    RealExecutionEvent,
    RealTrainingExecution,
    RealTrainingExecutionResult,
)

REAL_EXECUTION_GENERATOR = "verifiednet.training.hfexecutor"
REAL_EXEC_MANIFEST_FILE = "manifest.json"
REAL_EXEC_BINDING_FILE = "authorization-binding.json"
REAL_EXEC_EVENTS_FILE = "events.jsonl"
REAL_EXEC_RESULT_FILE = "result.json"
REAL_EXEC_INCOMPLETE_MARKER = ".INCOMPLETE"
REAL_EXEC_CONTENT_FILES = (REAL_EXEC_BINDING_FILE, REAL_EXEC_EVENTS_FILE,
                           REAL_EXEC_RESULT_FILE)


class RealExecutionStoreError(VerifiedNetError):
    """Writing/reading/verifying a real-execution directory failed."""


class RealExecutionManifest(StrictModel):
    schema_version: Literal[1] = 1
    execution_format_version: Literal[1] = 1
    simulated: Literal[False] = False
    execution_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    authorization_digest: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    corpus_slice_id: str = Field(min_length=1)
    backend_spec_id: str = Field(min_length=1)
    model_artifact_id: str = Field(min_length=1)
    tokenizer_artifact_id: str = Field(min_length=1)
    execution_policy: RealTrainingExecutionPolicy
    slice_policy: BoundedCorpusSlicePolicy
    determinism_category: str = Field(min_length=1)
    final_state: ExecutionState
    completed_optimizer_steps: int = Field(ge=0)
    event_count: int = Field(ge=1)
    checkpoint_id: str | None = None
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    execution_digest: str = Field(min_length=1)
    execution_store_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealExecutionManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        if set(paths) != set(REAL_EXEC_CONTENT_FILES):
            raise ValueError("manifest files do not match the layout")
        if (self.final_state is ExecutionState.COMPLETED) != (
                self.checkpoint_id is not None):
            raise ValueError(
                "completed executions carry exactly one checkpoint id; "
                "failed executions carry none")
        if self.execution_store_digest != compute_real_exec_store_digest(self):
            raise ValueError("execution_store_digest does not match")
        return self


def compute_real_exec_store_digest(manifest: RealExecutionManifest) -> str:
    payload = manifest.model_dump(mode="json")
    payload.pop("execution_store_digest", None)
    return "rexecstore-" + sha256_canonical(payload)[:24]


@dataclass(frozen=True)
class WrittenRealExecution:
    root: Path
    execution_id: str
    execution_digest: str
    final_state: ExecutionState
    checkpoint_id: str | None


def write_real_execution(
    execution: RealTrainingExecution,
    executions_root: str | Path,
    *,
    training_corpus_id: str,
    training_corpus_digest: str,
    determinism_category: str,
) -> WrittenRealExecution:
    """Persist a real execution record immutably; never overwrite."""
    events_payload = ("\n".join(
        canonical_json_str(e) for e in execution.events) + "\n").encode()
    binding_payload = canonical_json_bytes({
        "authorization_id": execution.authorization_id,
        "authorization_digest": execution.authorization_digest,
        "model_artifact_id": execution.model_artifact_id,
        "tokenizer_artifact_id": execution.tokenizer_artifact_id,
        "backend_spec_id": execution.backend_spec_id,
    })
    result_payload = canonical_json_bytes(execution.result)
    content = {
        REAL_EXEC_BINDING_FILE: binding_payload,
        REAL_EXEC_EVENTS_FILE: events_payload,
        REAL_EXEC_RESULT_FILE: result_payload,
    }
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path))
    fields: dict[str, object] = {
        "execution_id": execution.execution_id,
        "training_plan_id": execution.training_plan_id,
        "plan_digest": execution.plan_digest,
        "authorization_id": execution.authorization_id,
        "authorization_digest": execution.authorization_digest,
        "training_corpus_id": training_corpus_id,
        "training_corpus_digest": training_corpus_digest,
        "corpus_slice_id": execution.slice_policy.corpus_slice_id,
        "backend_spec_id": execution.backend_spec_id,
        "model_artifact_id": execution.model_artifact_id,
        "tokenizer_artifact_id": execution.tokenizer_artifact_id,
        "execution_policy": execution.execution_policy,
        "slice_policy": execution.slice_policy,
        "determinism_category": determinism_category,
        "final_state": execution.result.final_state,
        "completed_optimizer_steps":
            execution.result.completed_optimizer_steps,
        "event_count": len(execution.events),
        "checkpoint_id": execution.result.produced_checkpoint_id,
        "generated_by": REAL_EXECUTION_GENERATOR,
        "files": files,
        "execution_digest": execution.execution_digest,
    }
    probe = RealExecutionManifest.model_construct(**fields)  # type: ignore[arg-type]
    manifest = RealExecutionManifest(
        **fields,  # type: ignore[arg-type]
        execution_store_digest=compute_real_exec_store_digest(probe))

    root = Path(executions_root) / execution.execution_id
    if root.exists() and any(root.iterdir()):
        raise RealExecutionStoreError(f"execution already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / REAL_EXEC_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    for name, payload in sorted(content.items()):
        atomic_write_bytes(root / name, payload)
    atomic_write_bytes(root / REAL_EXEC_MANIFEST_FILE,
                       canonical_json_bytes(manifest))
    result = verify_real_execution(root, execution=execution)
    hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise RealExecutionStoreError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenRealExecution(
        root=root, execution_id=execution.execution_id,
        execution_digest=execution.execution_digest,
        final_state=execution.result.final_state,
        checkpoint_id=execution.result.produced_checkpoint_id)


class RealExecutionVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    execution_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def verify_real_execution(
    execution_dir: str | Path,
    *,
    execution: RealTrainingExecution | None = None,
) -> RealExecutionVerificationResult:
    """Fail-closed structural verification. Recomputes bindings, ordering,
    monotone counts, final-state consistency, and digests; NEVER replays
    losses, gradients, weights, or kernel behavior."""
    root = Path(execution_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("execution_dir_present", False, str(root)))
        return RealExecutionVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("execution_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / REAL_EXEC_INCOMPLETE_MARKER).exists()))
    manifest_path = root / REAL_EXEC_MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return RealExecutionVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = RealExecutionManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return RealExecutionVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.execution_digest

    on_disk = {str(p.relative_to(root)) for p in root.rglob("*")
               if p.is_file() and p.name != REAL_EXEC_INCOMPLETE_MARKER}
    allowed = set(REAL_EXEC_CONTENT_FILES) | {REAL_EXEC_MANIFEST_FILE}
    checks.append(_c("no_missing_files", not sorted(allowed - on_disk)))
    checks.append(_c("no_unexpected_files", not sorted(on_disk - allowed)))

    hash_ok, detail = True, ""
    for fh in manifest.files:
        fpath = root / fh.relative_path
        if not fpath.is_file():
            hash_ok, detail = False, f"missing {fh.relative_path}"
            break
        raw = fpath.read_bytes()
        if len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok, detail = False, f"mismatch for {fh.relative_path}"
            break
    checks.append(_c("file_hashes_match", hash_ok, detail))

    events_ok, result_ok, consistency_ok = True, True, True
    edetail = ""
    if hash_ok:
        try:
            lines = (root / REAL_EXEC_EVENTS_FILE).read_bytes().decode(
                "utf-8").splitlines()
            events = tuple(RealExecutionEvent.model_validate_json(line)
                           for line in lines if line.strip())
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            events_ok, edetail = False, str(exc).splitlines()[0]
            events = ()
        try:
            result = RealTrainingExecutionResult.model_validate_json(
                (root / REAL_EXEC_RESULT_FILE).read_bytes())
        except (OSError, ValidationError) as exc:
            result_ok, edetail = False, edetail or str(exc).splitlines()[0]
            result = None
        if events_ok and result_ok and result is not None:
            prev = manifest.execution_id
            steps = 0
            for i, event in enumerate(events):
                if (event.execution_id != manifest.execution_id
                        or event.sequence != i
                        or event.prev_event_hash != prev
                        or event.completed_steps < steps):
                    consistency_ok = False
                    edetail = f"event {i} inconsistent"
                    break
                prev = event.event_hash
                steps = event.completed_steps
            if consistency_ok:
                consistency_ok = (
                    len(events) == manifest.event_count
                    and steps == manifest.completed_optimizer_steps
                    and steps == result.completed_optimizer_steps
                    and events[-1].state_after is manifest.final_state
                    and result.final_state is manifest.final_state
                    and result.produced_checkpoint_id == manifest.checkpoint_id
                    and steps
                    <= manifest.execution_policy.max_runtime_optimizer_steps
                    and result.claims_replay_determinism is False)
    else:
        events_ok = result_ok = False
    checks.append(_c("events_parse", events_ok, edetail if not events_ok else ""))
    checks.append(_c("result_parses", result_ok))
    checks.append(_c("execution_consistent", consistency_ok, edetail))
    if execution is not None:
        checks.append(_c(
            "matches_in_memory_execution",
            execution.execution_id == manifest.execution_id
            and execution.execution_digest == manifest.execution_digest))
    checks.append(_c("no_retry_or_resume_evidence",
                     manifest.execution_policy.retry_support == "unsupported"
                     and manifest.execution_policy.resume_support
                     == "unsupported"))

    return RealExecutionVerificationResult(
        verified=all(c.passed for c in checks), execution_digest=digest,
        checks=tuple(checks))


@dataclass(frozen=True)
class LoadedRealExecution:
    manifest: RealExecutionManifest
    events: tuple[RealExecutionEvent, ...]
    result: RealTrainingExecutionResult


def read_real_execution(execution_dir: str | Path) -> LoadedRealExecution:
    root = Path(execution_dir)
    result = verify_real_execution(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise RealExecutionStoreError(
            f"real execution failed verification: {detail}")
    manifest = RealExecutionManifest.model_validate_json(
        (root / REAL_EXEC_MANIFEST_FILE).read_bytes())
    lines = (root / REAL_EXEC_EVENTS_FILE).read_bytes().decode().splitlines()
    events = tuple(RealExecutionEvent.model_validate_json(line)
                   for line in lines if line.strip())
    payload = json.loads((root / REAL_EXEC_RESULT_FILE).read_bytes())
    return LoadedRealExecution(
        manifest=manifest, events=events,
        result=RealTrainingExecutionResult.model_validate_json(
            json.dumps(payload)))
