"""Immutable training-plan persistence: manifest, writer, reader, verifier (Gate 10B).

A training plan is persisted separately from training corpora (and every other
artifact class):

    training-plans/<training_plan_id>/
        manifest.json             # TrainingPlanManifest (+ self-validating digest)
        request.json              # TrainingRequest (canonical JSON)
        plan.json                 # TrainingPlan (canonical JSON)
        simulated-result.json     # optional SimulatedTrainingResult (explicit)

The manifest makes simulation EXPLICIT (``simulated`` flag) and carries only
deterministic metadata — no timestamps, hostnames, usernames, durations, device
discovery, or absolute paths. The verifier reconstructs the request and plan
(their model validators re-derive every id and every batch/step count), checks
manifest/file consistency and the non-recursive plan digest, and fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import DatasetFileHash
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel
from verifiednet.training.trainer import (
    SimulatedTrainingResult,
    TrainingPlan,
    TrainingRequest,
)

TRAINING_PLAN_FORMAT_VERSION = 1
PLAN_GENERATOR = "verifiednet.training.trainer"

MANIFEST_FILE = "manifest.json"
REQUEST_FILE = "request.json"
PLAN_FILE = "plan.json"
SIMULATED_RESULT_FILE = "simulated-result.json"
PLAN_INCOMPLETE_MARKER = ".INCOMPLETE"
SUPPORTED_PLAN_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_PLAN_FORMAT: frozenset[int] = frozenset({1})


class TrainingPlanStoreError(VerifiedNetError):
    """Writing/reading/verifying a training-plan directory failed."""


def compute_plan_digest(
    *,
    schema_version: int,
    plan_format_version: int,
    training_plan_id: str,
    request_id: str,
    training_spec_id: str,
    training_corpus_id: str,
    training_corpus_digest: str,
    model_spec_id: str,
    tokenizer_spec_id: str,
    trainer_implementation_id: str,
    trainer_capability_id: str,
    simulated: bool,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    """Non-recursive digest over the plan configuration and content files."""
    payload = {
        "schema_version": schema_version,
        "plan_format_version": plan_format_version,
        "training_plan_id": training_plan_id,
        "request_id": request_id,
        "training_spec_id": training_spec_id,
        "training_corpus_id": training_corpus_id,
        "training_corpus_digest": training_corpus_digest,
        "model_spec_id": model_spec_id,
        "tokenizer_spec_id": tokenizer_spec_id,
        "trainer_implementation_id": trainer_implementation_id,
        "trainer_capability_id": trainer_capability_id,
        "simulated": simulated,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "plandig-" + sha256_canonical(payload)[:24]


class TrainingPlanManifest(StrictModel):
    """Deterministic metadata for one persisted training plan (self-validating)."""

    schema_version: Literal[1] = 1
    plan_format_version: Literal[1] = 1
    training_plan_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    training_spec_id: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    model_spec_id: str = Field(min_length=1)
    tokenizer_spec_id: str = Field(min_length=1)
    trainer_implementation_id: str = Field(min_length=1)
    trainer_capability_id: str = Field(min_length=1)
    simulated: bool = False
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(default_factory=tuple)
    plan_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> TrainingPlanManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("manifest files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("manifest files must be unique by path")
        expected_files = {REQUEST_FILE, PLAN_FILE}
        if self.simulated:
            expected_files.add(SIMULATED_RESULT_FILE)
        if set(paths) != expected_files:
            raise ValueError("manifest files do not match the declared layout")
        expected = compute_plan_digest(
            schema_version=self.schema_version,
            plan_format_version=self.plan_format_version,
            training_plan_id=self.training_plan_id, request_id=self.request_id,
            training_spec_id=self.training_spec_id,
            training_corpus_id=self.training_corpus_id,
            training_corpus_digest=self.training_corpus_digest,
            model_spec_id=self.model_spec_id,
            tokenizer_spec_id=self.tokenizer_spec_id,
            trainer_implementation_id=self.trainer_implementation_id,
            trainer_capability_id=self.trainer_capability_id,
            simulated=self.simulated, generated_by=self.generated_by,
            files=self.files,
        )
        if self.plan_digest != expected:
            raise ValueError("plan_digest does not match manifest content")
        return self


@dataclass(frozen=True)
class WrittenTrainingPlan:
    root: Path
    training_plan_id: str
    plan_digest: str
    file_count: int


def _build_manifest(
    plan: TrainingPlan,
    simulated_result: SimulatedTrainingResult | None,
    content: dict[str, bytes],
) -> TrainingPlanManifest:
    spec = plan.request.spec
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path,
    ))
    digest = compute_plan_digest(
        schema_version=1, plan_format_version=TRAINING_PLAN_FORMAT_VERSION,
        training_plan_id=plan.training_plan_id, request_id=plan.request.request_id,
        training_spec_id=spec.training_spec_id,
        training_corpus_id=spec.training_corpus_id,
        training_corpus_digest=spec.training_corpus_digest,
        model_spec_id=spec.model.model_spec_id,
        tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
        trainer_implementation_id=spec.trainer_implementation_id,
        trainer_capability_id=plan.request.trainer_capability_id,
        simulated=simulated_result is not None, generated_by=PLAN_GENERATOR,
        files=files,
    )
    return TrainingPlanManifest(
        training_plan_id=plan.training_plan_id, request_id=plan.request.request_id,
        training_spec_id=spec.training_spec_id,
        training_corpus_id=spec.training_corpus_id,
        training_corpus_digest=spec.training_corpus_digest,
        model_spec_id=spec.model.model_spec_id,
        tokenizer_spec_id=spec.tokenizer.tokenizer_spec_id,
        trainer_implementation_id=spec.trainer_implementation_id,
        trainer_capability_id=plan.request.trainer_capability_id,
        simulated=simulated_result is not None, generated_by=PLAN_GENERATOR,
        files=files, plan_digest=digest,
    )


def write_training_plan(
    plan: TrainingPlan,
    plans_root: str | Path,
    *,
    simulated_result: SimulatedTrainingResult | None = None,
) -> WrittenTrainingPlan:
    """Write ``training-plans/<plan_id>/`` deterministically; never overwrite."""
    if simulated_result is not None:
        if simulated_result.training_plan_id != plan.training_plan_id:
            raise TrainingPlanStoreError("simulated result is for a different plan")
        if simulated_result.request_id != plan.request.request_id:
            raise TrainingPlanStoreError("simulated result is for a different request")

    content: dict[str, bytes] = {
        REQUEST_FILE: canonical_json_bytes(plan.request),
        PLAN_FILE: canonical_json_bytes(plan),
    }
    if simulated_result is not None:
        content[SIMULATED_RESULT_FILE] = canonical_json_bytes(simulated_result)
    manifest = _build_manifest(plan, simulated_result, content)

    root = Path(plans_root) / plan.training_plan_id
    if root.exists() and any(root.iterdir()):
        raise TrainingPlanStoreError(f"training plan already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / PLAN_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    try:
        for rel, payload in sorted(content.items()):
            atomic_write_bytes(root / rel, payload)
        atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(manifest))
        result = verify_training_plan(root)
        hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
        if hard:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
            raise TrainingPlanStoreError(f"post-write verification failed: {detail}")
    except Exception:
        raise
    marker.unlink()
    fsync_dir(root)
    file_count = sum(1 for p in root.rglob("*") if p.is_file())
    return WrittenTrainingPlan(
        root=root, training_plan_id=plan.training_plan_id,
        plan_digest=manifest.plan_digest, file_count=file_count,
    )


class TrainingPlanVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    plan_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def verify_training_plan(plan_dir: str | Path) -> TrainingPlanVerificationResult:
    """Verify a training-plan directory; recompute ids/counts/digest; fail closed."""
    root = Path(plan_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("plan_dir_present", False, f"not a directory: {root}"))
        return TrainingPlanVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("plan_dir_present", True))

    marker_absent = not (root / PLAN_INCOMPLETE_MARKER).exists()
    checks.append(_c("incomplete_marker_absent", marker_absent))

    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return TrainingPlanVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = TrainingPlanManifest.model_validate_json(manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return TrainingPlanVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.plan_digest

    checks.append(_c("schema_supported",
                    manifest.schema_version in SUPPORTED_PLAN_SCHEMA))
    checks.append(_c("format_supported",
                    manifest.plan_format_version in SUPPORTED_PLAN_FORMAT))

    expected_content = {REQUEST_FILE, PLAN_FILE}
    if manifest.simulated:
        expected_content.add(SIMULATED_RESULT_FILE)
    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != PLAN_INCOMPLETE_MARKER
    }
    allowed = expected_content | {MANIFEST_FILE}
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
            hash_ok, hash_detail = False, f"hash/size mismatch for {fh.relative_path}"
            break
    checks.append(_c("file_hashes_match", hash_ok, hash_detail))

    # Reconstruct request + plan; their model validators re-derive EVERY id and
    # batch/step count, so stored derived values are never trusted.
    consistency_ok, sim_ok = True, True
    detail = ""
    if hash_ok:
        try:
            request = TrainingRequest.model_validate_json(
                (root / REQUEST_FILE).read_bytes())
            plan = TrainingPlan.model_validate_json((root / PLAN_FILE).read_bytes())
        except (OSError, ValidationError) as exc:
            consistency_ok = False
            detail = str(exc).splitlines()[0]
        else:
            spec = plan.request.spec
            if (plan.training_plan_id != manifest.training_plan_id
                    or plan.request.request_id != manifest.request_id
                    or request.request_id != manifest.request_id
                    or spec.training_spec_id != manifest.training_spec_id
                    or spec.training_corpus_id != manifest.training_corpus_id
                    or spec.training_corpus_digest != manifest.training_corpus_digest
                    or spec.model.model_spec_id != manifest.model_spec_id
                    or spec.tokenizer.tokenizer_spec_id != manifest.tokenizer_spec_id
                    or spec.trainer_implementation_id
                    != manifest.trainer_implementation_id
                    or plan.request.trainer_capability_id
                    != manifest.trainer_capability_id):
                consistency_ok = False
                detail = "manifest ids do not match the stored request/plan"
            if manifest.simulated:
                try:
                    sim = SimulatedTrainingResult.model_validate_json(
                        (root / SIMULATED_RESULT_FILE).read_bytes())
                except (OSError, ValidationError) as exc:
                    sim_ok = False
                    detail = detail or str(exc).splitlines()[0]
                else:
                    if (sim.training_plan_id != plan.training_plan_id
                            or sim.request_id != plan.request.request_id
                            or sim.simulated_completed_steps != plan.optimizer_steps):
                        sim_ok = False
                        detail = detail or "simulated result inconsistent with the plan"
    else:
        consistency_ok = False
    checks.append(_c("plan_reconstructs_and_matches", consistency_ok, detail))
    checks.append(_c("simulated_result_consistent", sim_ok))

    recomputed = compute_plan_digest(
        schema_version=manifest.schema_version,
        plan_format_version=manifest.plan_format_version,
        training_plan_id=manifest.training_plan_id, request_id=manifest.request_id,
        training_spec_id=manifest.training_spec_id,
        training_corpus_id=manifest.training_corpus_id,
        training_corpus_digest=manifest.training_corpus_digest,
        model_spec_id=manifest.model_spec_id,
        tokenizer_spec_id=manifest.tokenizer_spec_id,
        trainer_implementation_id=manifest.trainer_implementation_id,
        trainer_capability_id=manifest.trainer_capability_id,
        simulated=manifest.simulated, generated_by=manifest.generated_by,
        files=manifest.files,
    )
    checks.append(_c("plan_digest_matches", recomputed == manifest.plan_digest))

    return TrainingPlanVerificationResult(
        verified=all(c.passed for c in checks), plan_digest=digest,
        checks=tuple(checks),
    )


@dataclass(frozen=True)
class LoadedTrainingPlan:
    manifest: TrainingPlanManifest
    request: TrainingRequest
    plan: TrainingPlan
    simulated_result: SimulatedTrainingResult | None


def read_training_plan(plan_dir: str | Path) -> LoadedTrainingPlan:
    """Verify then reconstruct a training plan; fail closed on any failure."""
    root = Path(plan_dir)
    result = verify_training_plan(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise TrainingPlanStoreError(f"training plan failed verification: {detail}")
    manifest = TrainingPlanManifest.model_validate_json(
        (root / MANIFEST_FILE).read_bytes())
    request = TrainingRequest.model_validate_json((root / REQUEST_FILE).read_bytes())
    plan = TrainingPlan.model_validate_json((root / PLAN_FILE).read_bytes())
    simulated = None
    if manifest.simulated:
        simulated = SimulatedTrainingResult.model_validate_json(
            (root / SIMULATED_RESULT_FILE).read_bytes())
    return LoadedTrainingPlan(
        manifest=manifest, request=request, plan=plan, simulated_result=simulated)
