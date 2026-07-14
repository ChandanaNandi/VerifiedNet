"""The FIRST genuine checkpoint format + immutable store (Gate 10F).

    real-checkpoints/<checkpoint_id>/
        manifest.json
        payload/
            checkpoint.json        (checkpoint metadata)
            config.json            (model configuration)
            model.safetensors      (real model weights)
            tokenizer.json         (tokenizer snapshot)

This is a NEW explicit format (``verifiednet.real-checkpoint-v1``,
``artifact_kind="full_model_checkpoint"``, ``payload_format="safetensors"``).
It does not touch or weaken the Gate 10D fake format — the two are distinct
model types with distinct Literal locks, and the fake format still cannot
claim real loadability.

Gate 10D's candidate-versus-verified rule is preserved exactly: the trainer
backend emits an UNTRUSTED ``RealCheckpointCandidate`` (raw bytes, no hashes);
the writer recomputes every hash, validates the safetensors payload
STRUCTURALLY (dependency-free header parsing — no ML library is imported,
and the checkpoint is never loaded into a model to verify it), binds the
lineage, and verifies the persisted artifact before removing ``.INCOMPLETE``.
Optimizer/scheduler/RNG/resume state are structurally excluded; exactly one
checkpoint may exist per execution; parent checkpoints remain forbidden.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel
from verifiednet.training.checkpoint import validate_checkpoint_relative_path

REAL_CHECKPOINT_PAYLOAD_FORMAT = "verifiednet.real-checkpoint-v1"
REAL_CHECKPOINT_VERSION = 1
REAL_CHECKPOINT_GENERATOR = "verifiednet.training.realckptstore"
REAL_CHECKPOINT_MANIFEST_FILE = "manifest.json"
REAL_CHECKPOINT_INCOMPLETE_MARKER = ".INCOMPLETE"
#: Field names that would indicate resumable/optimizer state — forbidden.
FORBIDDEN_STATE_MARKERS = (b'"optimizer_state"', b'"scheduler_state"',
                           b'"rng_state"', b'"resume_state"')


class RealCheckpointError(VerifiedNetError):
    """A real-checkpoint contract or store operation failed."""


# ---------------------------------------------------------------------------
# Dependency-free safetensors structural parsing
# ---------------------------------------------------------------------------


def parse_safetensors_header(blob: bytes) -> dict[str, object]:
    """Structurally parse a safetensors byte payload; fail closed.

    Layout: 8-byte little-endian header length N, then N bytes of JSON whose
    keys are tensor names mapping to {dtype, shape, data_offsets}, followed by
    the tensor data. This validates structure WITHOUT any ML library and
    without interpreting weights.
    """
    if len(blob) < 8:
        raise RealCheckpointError("safetensors payload shorter than its header")
    (header_len,) = struct.unpack("<Q", blob[:8])
    if header_len <= 0 or 8 + header_len > len(blob):
        raise RealCheckpointError("safetensors header length is invalid")
    try:
        header = json.loads(blob[8:8 + header_len].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RealCheckpointError(f"safetensors header is not JSON: {exc}") from exc
    if not isinstance(header, dict) or not header:
        raise RealCheckpointError("safetensors header must be a non-empty object")
    data_len = len(blob) - 8 - header_len
    for name, entry in header.items():
        if name == "__metadata__":
            continue
        if not (isinstance(entry, dict)
                and isinstance(entry.get("dtype"), str)
                and isinstance(entry.get("shape"), list)
                and isinstance(entry.get("data_offsets"), list)
                and len(entry["data_offsets"]) == 2):
            raise RealCheckpointError(f"malformed tensor entry: {name!r}")
        start, end = entry["data_offsets"]
        if not (isinstance(start, int) and isinstance(end, int)
                and 0 <= start <= end <= data_len):
            raise RealCheckpointError(f"tensor offsets out of range: {name!r}")
    return header


def count_safetensors_parameters(blob: bytes) -> int:
    """Exact parameter count from tensor shapes (structural, never loaded)."""
    header = parse_safetensors_header(blob)
    total = 0
    for name, entry in header.items():
        if name == "__metadata__":
            continue
        assert isinstance(entry, dict)
        n = 1
        for dim in entry["shape"]:
            if not isinstance(dim, int) or dim < 0:
                raise RealCheckpointError(f"invalid shape in {name!r}")
            n *= dim
        total += n
    return total


def build_minimal_safetensors(
    tensors: dict[str, tuple[tuple[int, ...], bytes]],
) -> bytes:
    """Build a structurally valid safetensors blob from raw float32 bytes.

    Used by the offline STUB backend and test fixtures only — deterministic
    synthetic bytes, not trained weights. ``tensors`` maps name ->
    (shape, raw little-endian float32 data).
    """
    header: dict[str, object] = {}
    offset = 0
    data = b""
    for name in sorted(tensors):
        shape, raw = tensors[name]
        expected = 4
        for dim in shape:
            expected *= dim
        if len(raw) != expected:
            raise RealCheckpointError(
                f"tensor {name!r}: {len(raw)} bytes != shape {shape}")
        header[name] = {"dtype": "F32", "shape": list(shape),
                        "data_offsets": [offset, offset + len(raw)]}
        offset += len(raw)
        data += raw
    header_bytes = json.dumps(header, sort_keys=True,
                              separators=(",", ":")).encode()
    return struct.pack("<Q", len(header_bytes)) + header_bytes + data


# ---------------------------------------------------------------------------
# Real format spec / compatibility / lineage
# ---------------------------------------------------------------------------


class RealCheckpointFileRole(StrEnum):
    MODEL_WEIGHTS = "model_weights"
    MODEL_CONFIG = "model_config"
    TOKENIZER_SNAPSHOT = "tokenizer_snapshot"
    CHECKPOINT_METADATA = "checkpoint_metadata"


REAL_CHECKPOINT_ROLES: tuple[RealCheckpointFileRole, ...] = tuple(
    sorted(RealCheckpointFileRole))


class RealCheckpointFormatSpec(StrictModel):
    """The first GENUINE checkpoint format: full-model safetensors weights,
    config, tokenizer snapshot, metadata. Optimizer/scheduler/RNG/resume
    state are Literal-excluded; checkpointing is on-completion only."""

    schema_version: Literal[1] = 1
    format_version: Literal[1] = 1
    artifact_kind: Literal["full_model_checkpoint"] = "full_model_checkpoint"
    payload_format: Literal["verifiednet.real-checkpoint-v1"] = (
        "verifiednet.real-checkpoint-v1")
    weights_serialization: Literal["safetensors"] = "safetensors"
    expected_file_roles: tuple[RealCheckpointFileRole, ...] = Field(
        min_length=1)
    optimizer_state_inclusion: Literal["excluded"] = "excluded"
    scheduler_state_inclusion: Literal["excluded"] = "excluded"
    rng_state_inclusion: Literal["excluded"] = "excluded"
    resume_state_inclusion: Literal["excluded"] = "excluded"
    checkpoint_timing: Literal["on_completion_only"] = "on_completion_only"
    simulated: Literal[False] = False
    format_spec_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealCheckpointFormatSpec:
        roles = list(self.expected_file_roles)
        if roles != sorted(roles) or len(roles) != len(set(roles)):
            raise ValueError("expected_file_roles must be sorted and unique")
        if self.format_spec_id != derive_real_format_spec_id(self):
            raise ValueError("format_spec_id does not match the format spec")
        return self


def derive_real_format_spec_id(spec: RealCheckpointFormatSpec) -> str:
    payload = spec.model_dump(mode="json")
    payload.pop("format_spec_id", None)
    return "realfmt-" + sha256_canonical(payload)[:16]


def build_real_checkpoint_format_spec() -> RealCheckpointFormatSpec:
    probe = RealCheckpointFormatSpec.model_construct(
        expected_file_roles=REAL_CHECKPOINT_ROLES)
    return RealCheckpointFormatSpec(
        expected_file_roles=REAL_CHECKPOINT_ROLES,
        format_spec_id=derive_real_format_spec_id(probe))


class RealCheckpointLineage(StrictModel):
    """Complete provenance of the first real checkpoint. Parent checkpoints
    remain forbidden (warm starts and resume are deferred gates)."""

    schema_version: Literal[1] = 1
    lineage_version: Literal[1] = 1
    real_execution_id: str = Field(min_length=1)
    real_execution_digest: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    authorization_digest: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    training_spec_id: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    corpus_slice_id: str = Field(min_length=1)
    model_artifact_id: str = Field(min_length=1)
    tokenizer_artifact_id: str = Field(min_length=1)
    backend_spec_id: str = Field(min_length=1)
    real_execution_policy_id: str = Field(min_length=1)
    completed_optimizer_steps: int = Field(ge=1)
    parent_checkpoint_id: None = None
    lineage_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealCheckpointLineage:
        if self.lineage_id != derive_real_lineage_id(self):
            raise ValueError("lineage_id does not match the lineage content")
        return self


def derive_real_lineage_id(lineage: RealCheckpointLineage) -> str:
    payload = lineage.model_dump(mode="json")
    payload.pop("lineage_id", None)
    return "reallin-" + sha256_canonical(payload)[:16]


class RealCheckpointCompatibility(StrictModel):
    """What may consume this checkpoint: metadata for the FUTURE predictor
    gate. Honest: real weights, but no prediction integration exists yet."""

    schema_version: Literal[1] = 1
    format_spec_id: str = Field(min_length=1)
    model_spec_id: str = Field(min_length=1)
    tokenizer_spec_id: str = Field(min_length=1)
    architecture_id: str = Field(min_length=1)
    predictor_adapter_version: Literal["deferred-next-gate-v0"] = (
        "deferred-next-gate-v0")
    simulated_only: Literal[False] = False
    loadable_as_real_model: Literal[True] = True
    evaluated: Literal[False] = False
    benchmarked: Literal[False] = False
    compatibility_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealCheckpointCompatibility:
        if self.compatibility_id != derive_real_compatibility_id(self):
            raise ValueError("compatibility_id does not match the content")
        return self


def derive_real_compatibility_id(compat: RealCheckpointCompatibility) -> str:
    payload = compat.model_dump(mode="json")
    payload.pop("compatibility_id", None)
    return "realcompat-" + sha256_canonical(payload)[:16]


def derive_real_checkpoint_id(
    *, format_spec_id: str, lineage_id: str,
    declared_file_roles: tuple[RealCheckpointFileRole, ...],
    model_spec_id: str, tokenizer_spec_id: str, checkpoint_version: int,
) -> str:
    payload = {
        "format_spec_id": format_spec_id, "lineage_id": lineage_id,
        "declared_file_roles": sorted(r.value for r in declared_file_roles),
        "simulated": False, "model_spec_id": model_spec_id,
        "tokenizer_spec_id": tokenizer_spec_id,
        "checkpoint_version": checkpoint_version,
    }
    return "realckpt-" + sha256_canonical(payload)[:24]


# ---------------------------------------------------------------------------
# Untrusted candidate
# ---------------------------------------------------------------------------


class RealCandidateFile(StrictModel):
    relative_path: str = Field(min_length=1)
    role: RealCheckpointFileRole
    serialization_id: str = Field(min_length=1)
    content: bytes

    @model_validator(mode="after")
    def _valid(self) -> RealCandidateFile:
        validate_checkpoint_relative_path(self.relative_path)
        return self


class RealCheckpointCandidate(StrictModel):
    """Untrusted backend output. Carries raw bytes and NO hashes — trust is
    created only by the writer + verifier, exactly as in Gate 10D."""

    schema_version: Literal[1] = 1
    checkpoint_version: Literal[1] = 1
    simulated: Literal[False] = False
    producer_id: str = Field(min_length=1)
    intended_checkpoint_id: str = Field(min_length=1)
    lineage: RealCheckpointLineage
    format_spec: RealCheckpointFormatSpec
    compatibility: RealCheckpointCompatibility
    files: tuple[RealCandidateFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealCheckpointCandidate:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("candidate files must be path-sorted and unique")
        roles = [f.role for f in self.files]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate candidate file roles")
        if set(roles) != set(self.format_spec.expected_file_roles):
            raise ValueError("candidate roles do not match the format spec")
        if self.compatibility.format_spec_id != self.format_spec.format_spec_id:
            raise ValueError("compatibility binds a different format spec")
        weights = next(f for f in self.files
                       if f.role is RealCheckpointFileRole.MODEL_WEIGHTS)
        parse_safetensors_header(weights.content)  # structural fail-closed
        for f in self.files:
            if f.role is not RealCheckpointFileRole.MODEL_WEIGHTS:
                for marker in FORBIDDEN_STATE_MARKERS:
                    if marker in f.content:
                        raise ValueError(
                            "optimizer/scheduler/rng/resume state is forbidden")
        expected = derive_real_checkpoint_id(
            format_spec_id=self.format_spec.format_spec_id,
            lineage_id=self.lineage.lineage_id,
            declared_file_roles=tuple(roles),
            model_spec_id=self.compatibility.model_spec_id,
            tokenizer_spec_id=self.compatibility.tokenizer_spec_id,
            checkpoint_version=self.checkpoint_version)
        if self.intended_checkpoint_id != expected:
            raise ValueError(
                "intended_checkpoint_id does not match the candidate content")
        return self


# ---------------------------------------------------------------------------
# Manifest / writer / verifier / reader
# ---------------------------------------------------------------------------


class RealCheckpointFileEntry(StrictModel):
    relative_path: str = Field(min_length=1)
    role: RealCheckpointFileRole
    size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    serialization_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealCheckpointFileEntry:
        validate_checkpoint_relative_path(self.relative_path)
        return self


def compute_real_checkpoint_digest(
    *, schema_version: int, checkpoint_format_version: int, checkpoint_id: str,
    format_spec: RealCheckpointFormatSpec, lineage: RealCheckpointLineage,
    compatibility: RealCheckpointCompatibility, generated_by: str,
    files: tuple[RealCheckpointFileEntry, ...],
) -> str:
    payload = {
        "schema_version": schema_version,
        "checkpoint_format_version": checkpoint_format_version,
        "checkpoint_id": checkpoint_id,
        "format_spec": format_spec.model_dump(mode="json"),
        "lineage": lineage.model_dump(mode="json"),
        "compatibility": compatibility.model_dump(mode="json"),
        "generated_by": generated_by,
        "files": [f.model_dump(mode="json")
                  for f in sorted(files, key=lambda f: f.relative_path)],
    }
    return "realdig-" + sha256_canonical(payload)[:24]


class RealCheckpointManifest(StrictModel):
    schema_version: Literal[1] = 1
    checkpoint_format_version: Literal[1] = 1
    simulated: Literal[False] = False
    checkpoint_id: str = Field(min_length=1)
    checkpoint_digest: str = Field(min_length=1)
    format_spec: RealCheckpointFormatSpec
    lineage: RealCheckpointLineage
    compatibility: RealCheckpointCompatibility
    files: tuple[RealCheckpointFileEntry, ...] = Field(min_length=1)
    file_count: int = Field(ge=1)
    total_bytes: int = Field(ge=0)
    generated_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealCheckpointManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        roles = [f.role for f in self.files]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate file roles in manifest")
        if set(roles) != set(self.format_spec.expected_file_roles):
            raise ValueError("manifest roles do not match the format spec")
        if self.file_count != len(self.files):
            raise ValueError("file_count mismatch")
        if self.total_bytes != sum(f.size for f in self.files):
            raise ValueError("total_bytes mismatch")
        if self.compatibility.format_spec_id != self.format_spec.format_spec_id:
            raise ValueError("compatibility binds a different format spec")
        expected_id = derive_real_checkpoint_id(
            format_spec_id=self.format_spec.format_spec_id,
            lineage_id=self.lineage.lineage_id,
            declared_file_roles=tuple(roles),
            model_spec_id=self.compatibility.model_spec_id,
            tokenizer_spec_id=self.compatibility.tokenizer_spec_id,
            checkpoint_version=self.checkpoint_format_version)
        if self.checkpoint_id != expected_id:
            raise ValueError("checkpoint_id does not match the manifest")
        expected_digest = compute_real_checkpoint_digest(
            schema_version=self.schema_version,
            checkpoint_format_version=self.checkpoint_format_version,
            checkpoint_id=self.checkpoint_id, format_spec=self.format_spec,
            lineage=self.lineage, compatibility=self.compatibility,
            generated_by=self.generated_by, files=self.files)
        if self.checkpoint_digest != expected_digest:
            raise ValueError("checkpoint_digest does not match the content")
        return self


@dataclass(frozen=True)
class WrittenRealCheckpoint:
    root: Path
    checkpoint_id: str
    checkpoint_digest: str
    file_count: int
    total_bytes: int


def write_real_checkpoint(
    candidate: RealCheckpointCandidate, checkpoints_root: str | Path,
) -> WrittenRealCheckpoint:
    """Persist an untrusted real candidate as a verified immutable checkpoint."""
    candidate = RealCheckpointCandidate.model_validate(candidate.model_dump())
    entries = tuple(sorted((
        RealCheckpointFileEntry(
            relative_path=f.relative_path, role=f.role, size=len(f.content),
            sha256=sha256_bytes(f.content),
            serialization_id=f.serialization_id)
        for f in candidate.files), key=lambda e: e.relative_path))
    digest = compute_real_checkpoint_digest(
        schema_version=1, checkpoint_format_version=REAL_CHECKPOINT_VERSION,
        checkpoint_id=candidate.intended_checkpoint_id,
        format_spec=candidate.format_spec, lineage=candidate.lineage,
        compatibility=candidate.compatibility,
        generated_by=REAL_CHECKPOINT_GENERATOR, files=entries)
    manifest = RealCheckpointManifest(
        checkpoint_id=candidate.intended_checkpoint_id,
        checkpoint_digest=digest, format_spec=candidate.format_spec,
        lineage=candidate.lineage, compatibility=candidate.compatibility,
        files=entries, file_count=len(entries),
        total_bytes=sum(e.size for e in entries),
        generated_by=REAL_CHECKPOINT_GENERATOR)

    root = Path(checkpoints_root) / manifest.checkpoint_id
    if root.exists() and any(root.iterdir()):
        raise RealCheckpointError(f"checkpoint already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / REAL_CHECKPOINT_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    (root / "payload").mkdir(exist_ok=True)
    for f in candidate.files:
        atomic_write_bytes(root / f.relative_path, f.content)
    atomic_write_bytes(root / REAL_CHECKPOINT_MANIFEST_FILE,
                       canonical_json_bytes(manifest))
    result = verify_real_checkpoint(root)
    hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise RealCheckpointError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenRealCheckpoint(
        root=root, checkpoint_id=manifest.checkpoint_id,
        checkpoint_digest=manifest.checkpoint_digest,
        file_count=manifest.file_count, total_bytes=manifest.total_bytes)


class RealCheckpointVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    checkpoint_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def verify_real_checkpoint(
    checkpoint_dir: str | Path,
) -> RealCheckpointVerificationResult:
    """Structural fail-closed verification. The checkpoint is NEVER loaded
    into a model here; the safetensors payload is validated structurally."""
    root = Path(checkpoint_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("checkpoint_dir_present", False, str(root)))
        return RealCheckpointVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("checkpoint_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / REAL_CHECKPOINT_INCOMPLETE_MARKER).exists()))
    manifest_path = root / REAL_CHECKPOINT_MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return RealCheckpointVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = RealCheckpointManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return RealCheckpointVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.checkpoint_digest

    checks.append(_c("checkpoint_dir_matches_id",
                     root.name == manifest.checkpoint_id))
    declared = {f.relative_path for f in manifest.files}
    on_disk = {str(p.relative_to(root)) for p in root.rglob("*")
               if p.is_file() and p.name != REAL_CHECKPOINT_INCOMPLETE_MARKER}
    allowed = declared | {REAL_CHECKPOINT_MANIFEST_FILE}
    checks.append(_c("no_missing_files", not sorted(allowed - on_disk)))
    checks.append(_c("no_unexpected_files", not sorted(on_disk - allowed)))
    checks.append(_c("no_symlinks",
                     not any(p.is_symlink() for p in root.rglob("*"))))
    executables = [p for p in root.rglob("*")
                   if p.is_file() and os.access(p, os.X_OK)]
    checks.append(_c("no_executable_payloads", not executables))
    checks.append(_c("exactly_one_checkpoint_layout",
                     manifest.file_count == len(REAL_CHECKPOINT_ROLES)))

    hash_ok, weights_ok, state_ok = True, False, True
    detail = ""
    total = 0
    for entry in manifest.files:
        fpath = root / entry.relative_path
        if not fpath.is_file():
            hash_ok, detail = False, f"missing {entry.relative_path}"
            break
        raw = fpath.read_bytes()
        total += len(raw)
        if len(raw) != entry.size or sha256_bytes(raw) != entry.sha256:
            hash_ok, detail = False, f"mismatch for {entry.relative_path}"
            break
        if entry.role is RealCheckpointFileRole.MODEL_WEIGHTS:
            try:
                parse_safetensors_header(raw)
                weights_ok = True
            except RealCheckpointError as exc:
                detail = str(exc)
        else:
            for marker_bytes in FORBIDDEN_STATE_MARKERS:
                if marker_bytes in raw:
                    state_ok = False
    checks.append(_c("file_hashes_match", hash_ok, detail if not hash_ok else ""))
    checks.append(_c("total_size_matches",
                     hash_ok and total == manifest.total_bytes))
    checks.append(_c("safetensors_structurally_valid", weights_ok,
                     "" if weights_ok else detail))
    checks.append(_c("no_forbidden_state", state_ok))
    checks.append(_c("lineage_parent_absent",
                     manifest.lineage.parent_checkpoint_id is None))
    checks.append(_c("completed_steps_positive",
                     manifest.lineage.completed_optimizer_steps >= 1))
    checks.append(_c(
        "real_format_honest",
        manifest.simulated is False
        and manifest.format_spec.artifact_kind == "full_model_checkpoint"
        and manifest.compatibility.loadable_as_real_model is True
        and manifest.compatibility.evaluated is False
        and manifest.compatibility.benchmarked is False))

    return RealCheckpointVerificationResult(
        verified=all(c.passed for c in checks), checkpoint_digest=digest,
        checks=tuple(checks))


@dataclass(frozen=True)
class LoadedRealCheckpoint:
    root: Path
    manifest: RealCheckpointManifest


def read_real_checkpoint(checkpoint_dir: str | Path) -> LoadedRealCheckpoint:
    """Verify then return the manifest; NEVER loads weights into a model."""
    root = Path(checkpoint_dir)
    result = verify_real_checkpoint(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise RealCheckpointError(f"real checkpoint failed verification: {detail}")
    return LoadedRealCheckpoint(
        root=root, manifest=RealCheckpointManifest.model_validate_json(
            (root / REAL_CHECKPOINT_MANIFEST_FILE).read_bytes()))
