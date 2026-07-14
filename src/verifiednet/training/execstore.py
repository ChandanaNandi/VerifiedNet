"""Immutable training-execution persistence: manifest, writer, verifier, reader.

Gate 10C artifact layout (separate from every other artifact class):

    training-executions/<execution_id>/
        manifest.json    # TrainingExecutionManifest (+ self-validating digest)
        events.jsonl     # one canonical-JSON ExecutionEvent per line, in order

There are NO timestamps anywhere: ordering is the event sequence numbers plus
the hash chain. The manifest embeds the full execution header (policy, planned
counts, resume binding, final state, progress counts, event count, execution
digest), so the reader reconstructs the complete ``TrainingExecution`` from
manifest + events — and the model validator then replays the expected event
skeleton, re-checks every transition, the chain, the counts, the resume
binding, and the digest. The verifier is fail-closed and structured.
"""

from __future__ import annotations

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
from verifiednet.training.execution import (
    FAKE_EXECUTION_ENGINE_ID,
    FINAL_STATES,
    ExecutionEvent,
    ExecutionPolicy,
    ExecutionState,
    TrainingExecution,
)

TRAINING_EXECUTION_FORMAT_VERSION = 1
EXECUTION_GENERATOR = "verifiednet.training.engine"

EXECUTION_MANIFEST_FILE = "manifest.json"
EXECUTION_EVENTS_FILE = "events.jsonl"
EXECUTION_INCOMPLETE_MARKER = ".INCOMPLETE"
SUPPORTED_EXECUTION_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_EXECUTION_FORMAT: frozenset[int] = frozenset({1})


class TrainingExecutionStoreError(VerifiedNetError):
    """Writing/reading/verifying a training-execution directory failed."""


def compute_execution_store_digest(
    *,
    schema_version: int,
    execution_format_version: int,
    execution_id: str,
    training_plan_id: str,
    trainer_capability_id: str,
    execution_policy_id: str,
    retry_number: int,
    final_state: str,
    event_count: int,
    execution_digest: str,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    """Non-recursive digest over the manifest header and the content files."""
    payload = {
        "schema_version": schema_version,
        "execution_format_version": execution_format_version,
        "execution_id": execution_id,
        "training_plan_id": training_plan_id,
        "trainer_capability_id": trainer_capability_id,
        "execution_policy_id": execution_policy_id,
        "retry_number": retry_number,
        "final_state": final_state,
        "event_count": event_count,
        "execution_digest": execution_digest,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "execstore-" + sha256_canonical(payload)[:24]


class TrainingExecutionManifest(StrictModel):
    """Deterministic metadata + full header for one persisted execution."""

    schema_version: Literal[1] = 1
    execution_format_version: Literal[1] = 1
    simulated: Literal[True] = True
    execution_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    trainer_capability_id: str = Field(min_length=1)
    engine_implementation_id: str = Field(min_length=1)
    execution_policy: ExecutionPolicy
    retry_number: int = Field(ge=0)
    resumed_from_execution_id: str | None = None
    resumed_from_completed_steps: int | None = Field(default=None, ge=1)
    gradient_accumulation_steps: int = Field(ge=1)
    planned_optimizer_steps: int = Field(ge=1)
    planned_batches_per_epoch: int = Field(ge=1)
    planned_epochs: int | None = Field(default=None, ge=1)
    final_state: ExecutionState
    completed_optimizer_steps: int = Field(ge=0)
    completed_epochs: int = Field(ge=0)
    event_count: int = Field(ge=1)
    execution_digest: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    execution_store_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> TrainingExecutionManifest:
        if self.final_state not in FINAL_STATES:
            raise ValueError(f"final_state {self.final_state} is not final")
        if self.engine_implementation_id != FAKE_EXECUTION_ENGINE_ID:
            raise ValueError(
                "only the fake execution engine exists in this gate")
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        if set(paths) != {EXECUTION_EVENTS_FILE}:
            raise ValueError("manifest files do not match the declared layout")
        expected = compute_execution_store_digest(
            schema_version=self.schema_version,
            execution_format_version=self.execution_format_version,
            execution_id=self.execution_id,
            training_plan_id=self.training_plan_id,
            trainer_capability_id=self.trainer_capability_id,
            execution_policy_id=self.execution_policy.execution_policy_id,
            retry_number=self.retry_number,
            final_state=self.final_state.value,
            event_count=self.event_count,
            execution_digest=self.execution_digest,
            generated_by=self.generated_by, files=self.files,
        )
        if self.execution_store_digest != expected:
            raise ValueError(
                "execution_store_digest does not match manifest content")
        return self


@dataclass(frozen=True)
class WrittenTrainingExecution:
    root: Path
    execution_id: str
    execution_digest: str
    event_count: int


def _build_manifest(
    execution: TrainingExecution, events_payload: bytes,
) -> TrainingExecutionManifest:
    files = (DatasetFileHash(
        relative_path=EXECUTION_EVENTS_FILE,
        sha256=sha256_bytes(events_payload), size=len(events_payload)),)
    digest = compute_execution_store_digest(
        schema_version=1,
        execution_format_version=TRAINING_EXECUTION_FORMAT_VERSION,
        execution_id=execution.execution_id,
        training_plan_id=execution.training_plan_id,
        trainer_capability_id=execution.trainer_capability_id,
        execution_policy_id=execution.execution_policy.execution_policy_id,
        retry_number=execution.retry_number,
        final_state=execution.final_state.value,
        event_count=len(execution.events),
        execution_digest=execution.execution_digest,
        generated_by=EXECUTION_GENERATOR, files=files,
    )
    return TrainingExecutionManifest(
        execution_id=execution.execution_id,
        training_plan_id=execution.training_plan_id,
        trainer_capability_id=execution.trainer_capability_id,
        engine_implementation_id=execution.engine_implementation_id,
        execution_policy=execution.execution_policy,
        retry_number=execution.retry_number,
        resumed_from_execution_id=execution.resumed_from_execution_id,
        resumed_from_completed_steps=execution.resumed_from_completed_steps,
        gradient_accumulation_steps=execution.gradient_accumulation_steps,
        planned_optimizer_steps=execution.planned_optimizer_steps,
        planned_batches_per_epoch=execution.planned_batches_per_epoch,
        planned_epochs=execution.planned_epochs,
        final_state=execution.final_state,
        completed_optimizer_steps=execution.completed_optimizer_steps,
        completed_epochs=execution.completed_epochs,
        event_count=len(execution.events),
        execution_digest=execution.execution_digest,
        generated_by=EXECUTION_GENERATOR, files=files,
        execution_store_digest=digest,
    )


def _events_payload(execution: TrainingExecution) -> bytes:
    lines = [canonical_json_str(event) for event in execution.events]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_training_execution(
    execution: TrainingExecution, executions_root: str | Path,
) -> WrittenTrainingExecution:
    """Write ``training-executions/<execution_id>/``; never overwrite."""
    events_payload = _events_payload(execution)
    manifest = _build_manifest(execution, events_payload)

    root = Path(executions_root) / execution.execution_id
    if root.exists() and any(root.iterdir()):
        raise TrainingExecutionStoreError(
            f"training execution already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / EXECUTION_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    atomic_write_bytes(root / EXECUTION_EVENTS_FILE, events_payload)
    atomic_write_bytes(root / EXECUTION_MANIFEST_FILE,
                       canonical_json_bytes(manifest))
    result = verify_training_execution(root)
    hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise TrainingExecutionStoreError(
            f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenTrainingExecution(
        root=root, execution_id=execution.execution_id,
        execution_digest=execution.execution_digest,
        event_count=len(execution.events),
    )


class TrainingExecutionVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    execution_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def _reconstruct_execution(
    manifest: TrainingExecutionManifest, events: tuple[ExecutionEvent, ...],
) -> TrainingExecution:
    """Rebuild the execution from manifest header + events; full validation."""
    return TrainingExecution(
        execution_id=manifest.execution_id,
        training_plan_id=manifest.training_plan_id,
        trainer_capability_id=manifest.trainer_capability_id,
        execution_policy=manifest.execution_policy,
        retry_number=manifest.retry_number,
        resumed_from_execution_id=manifest.resumed_from_execution_id,
        resumed_from_completed_steps=manifest.resumed_from_completed_steps,
        gradient_accumulation_steps=manifest.gradient_accumulation_steps,
        planned_optimizer_steps=manifest.planned_optimizer_steps,
        planned_batches_per_epoch=manifest.planned_batches_per_epoch,
        planned_epochs=manifest.planned_epochs,
        final_state=manifest.final_state,
        completed_optimizer_steps=manifest.completed_optimizer_steps,
        completed_epochs=manifest.completed_epochs,
        events=events,
        execution_digest=manifest.execution_digest,
    )


def verify_training_execution(
    execution_dir: str | Path,
) -> TrainingExecutionVerificationResult:
    """Verify an execution directory; replay transitions/counts; fail closed."""
    root = Path(execution_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("execution_dir_present", False,
                         f"not a directory: {root}"))
        return TrainingExecutionVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("execution_dir_present", True))

    marker_absent = not (root / EXECUTION_INCOMPLETE_MARKER).exists()
    checks.append(_c("incomplete_marker_absent", marker_absent))

    manifest_path = root / EXECUTION_MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return TrainingExecutionVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = TrainingExecutionManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return TrainingExecutionVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.execution_digest

    checks.append(_c("schema_supported",
                     manifest.schema_version in SUPPORTED_EXECUTION_SCHEMA))
    checks.append(_c(
        "format_supported",
        manifest.execution_format_version in SUPPORTED_EXECUTION_FORMAT))

    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != EXECUTION_INCOMPLETE_MARKER
    }
    allowed = {EXECUTION_MANIFEST_FILE, EXECUTION_EVENTS_FILE}
    missing = sorted(allowed - on_disk)
    unexpected = sorted(on_disk - allowed)
    checks.append(_c("no_missing_files", not missing,
                     "" if not missing else f"missing={missing}"))
    checks.append(_c("no_unexpected_files", not unexpected,
                     "" if not unexpected else f"unexpected={unexpected}"))

    hash_ok = True
    hash_detail = ""
    for fh in manifest.files:
        fpath = root / fh.relative_path
        if not fpath.is_file():
            hash_ok, hash_detail = False, f"missing {fh.relative_path}"
            break
        raw = fpath.read_bytes()
        if len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok, hash_detail = (
                False, f"hash/size mismatch for {fh.relative_path}")
            break
    checks.append(_c("file_hashes_match", hash_ok, hash_detail))

    # Parse the event log and reconstruct the execution: the model validator
    # replays the deterministic skeleton, re-checks every transition, the
    # sequence numbers, the hash chain, all progress counts, the resume
    # binding, and the execution digest. Stored values are never trusted.
    events_ok, replay_ok = True, True
    detail = ""
    event_count = 0
    if hash_ok:
        try:
            raw_lines = (root / EXECUTION_EVENTS_FILE).read_bytes().decode(
                "utf-8").splitlines()
            events = tuple(
                ExecutionEvent.model_validate_json(line)
                for line in raw_lines if line.strip()
            )
            event_count = len(events)
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            events_ok = False
            detail = str(exc).splitlines()[0]
        else:
            try:
                _reconstruct_execution(manifest, events)
            except ValidationError as exc:
                replay_ok = False
                detail = str(exc).splitlines()[-1].strip()
    else:
        events_ok = False
    checks.append(_c("events_parse", events_ok,
                     "" if events_ok else detail))
    checks.append(_c("execution_replays_and_matches",
                     events_ok and replay_ok,
                     "" if events_ok and replay_ok else detail))
    checks.append(_c(
        "event_count_matches",
        events_ok and event_count == manifest.event_count,
        "" if events_ok and event_count == manifest.event_count
        else f"manifest={manifest.event_count} on_disk={event_count}"))

    recomputed = compute_execution_store_digest(
        schema_version=manifest.schema_version,
        execution_format_version=manifest.execution_format_version,
        execution_id=manifest.execution_id,
        training_plan_id=manifest.training_plan_id,
        trainer_capability_id=manifest.trainer_capability_id,
        execution_policy_id=manifest.execution_policy.execution_policy_id,
        retry_number=manifest.retry_number,
        final_state=manifest.final_state.value,
        event_count=manifest.event_count,
        execution_digest=manifest.execution_digest,
        generated_by=manifest.generated_by, files=manifest.files,
    )
    checks.append(_c("execution_store_digest_matches",
                     recomputed == manifest.execution_store_digest))

    return TrainingExecutionVerificationResult(
        verified=all(c.passed for c in checks), execution_digest=digest,
        checks=tuple(checks),
    )


@dataclass(frozen=True)
class LoadedTrainingExecution:
    manifest: TrainingExecutionManifest
    execution: TrainingExecution


def read_training_execution(
    execution_dir: str | Path,
) -> LoadedTrainingExecution:
    """Verify then reconstruct an execution; fail closed on any failure."""
    root = Path(execution_dir)
    result = verify_training_execution(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise TrainingExecutionStoreError(
            f"training execution failed verification: {detail}")
    manifest = TrainingExecutionManifest.model_validate_json(
        (root / EXECUTION_MANIFEST_FILE).read_bytes())
    raw_lines = (root / EXECUTION_EVENTS_FILE).read_bytes().decode(
        "utf-8").splitlines()
    events = tuple(
        ExecutionEvent.model_validate_json(line)
        for line in raw_lines if line.strip()
    )
    return LoadedTrainingExecution(
        manifest=manifest,
        execution=_reconstruct_execution(manifest, events),
    )
