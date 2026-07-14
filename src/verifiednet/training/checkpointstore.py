"""Immutable checkpoint persistence: manifest, writer, verifier, reader (10D).

    checkpoints/<checkpoint_id>/
        manifest.json
        payload/
            checkpoint.json
            config.json
            model.fakebin
            tokenizer-metadata.json

The writer accepts an UNTRUSTED ``CheckpointCandidate``, recomputes every file
hash and size from the candidate's actual content (candidate-supplied
integrity claims cannot exist — the candidate carries none), builds the
self-validating manifest, writes atomically under ``.INCOMPLETE``, verifies
the persisted artifact, and only then removes the marker. Verification is
structured and fail-closed: paths, symlinks, executables, hashes, sizes,
counts, ids, lineage, compatibility, simulation honesty, and the checkpoint
digest are all recomputed — stored derived values are never trusted.

Readers are deliberately narrow: they return metadata and payload DESCRIPTORS.
There is no ``load_model``, no tokenizer loading, no ML import — a Gate 10D
fake payload cannot be interpreted as weights through this API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel
from verifiednet.training.checkpoint import (
    FAKE_PAYLOAD_MAGIC,
    CheckpointCandidate,
    CheckpointCompatibility,
    CheckpointFileRole,
    CheckpointFormatSpec,
    CheckpointLineage,
    CheckpointProductionPolicy,
    derive_checkpoint_id,
    derive_compatibility_id,
    derive_lineage_id,
    validate_checkpoint_relative_path,
)

CHECKPOINT_FORMAT_VERSION = 1
CHECKPOINT_GENERATOR = "verifiednet.training.checkpointstore"

CHECKPOINT_MANIFEST_FILE = "manifest.json"
CHECKPOINT_PAYLOAD_DIR = "payload"
CHECKPOINT_INCOMPLETE_MARKER = ".INCOMPLETE"
SUPPORTED_CHECKPOINT_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_CHECKPOINT_FORMAT: frozenset[int] = frozenset({1})


class CheckpointStoreError(VerifiedNetError):
    """Writing/reading/verifying a checkpoint directory failed."""


class CheckpointFileEntry(StrictModel):
    """One VERIFIED checkpoint file record (hash/size recomputed by writer)."""

    relative_path: str = Field(min_length=1)
    role: CheckpointFileRole
    size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    serialization_id: str = Field(min_length=1)
    required: bool

    @model_validator(mode="after")
    def _valid(self) -> CheckpointFileEntry:
        validate_checkpoint_relative_path(self.relative_path)
        return self


def compute_checkpoint_digest(
    *,
    schema_version: int,
    checkpoint_format_version: int,
    checkpoint_id: str,
    format_spec: CheckpointFormatSpec,
    production_policy: CheckpointProductionPolicy,
    lineage: CheckpointLineage,
    compatibility: CheckpointCompatibility,
    simulated: bool,
    generated_by: str,
    files: tuple[CheckpointFileEntry, ...],
) -> str:
    """CONTENT identity: every configuration block + path-sorted file facts."""
    payload = {
        "schema_version": schema_version,
        "checkpoint_format_version": checkpoint_format_version,
        "checkpoint_id": checkpoint_id,
        "format_spec": format_spec.model_dump(mode="json"),
        "production_policy": production_policy.model_dump(mode="json"),
        "lineage": lineage.model_dump(mode="json"),
        "compatibility": compatibility.model_dump(mode="json"),
        "simulated": simulated,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "role": f.role.value,
             "size": f.size, "sha256": f.sha256,
             "serialization_id": f.serialization_id, "required": f.required}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "ckptdig-" + sha256_canonical(payload)[:24]


class CheckpointManifest(StrictModel):
    """Self-validating immutable checkpoint manifest. Deterministic metadata
    only: no timestamps, durations, hostnames, usernames, devices, process
    ids, absolute paths, or git state."""

    schema_version: Literal[1] = 1
    checkpoint_format_version: Literal[1] = 1
    simulated: Literal[True] = True
    checkpoint_id: str = Field(min_length=1)
    checkpoint_digest: str = Field(min_length=1)
    format_spec: CheckpointFormatSpec
    production_policy: CheckpointProductionPolicy
    lineage: CheckpointLineage
    compatibility: CheckpointCompatibility
    files: tuple[CheckpointFileEntry, ...] = Field(min_length=1)
    file_count: int = Field(ge=1)
    total_bytes: int = Field(ge=0)
    generated_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("manifest files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate file paths in manifest")
        roles = [f.role for f in self.files]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate file roles in manifest")
        if set(roles) != set(self.format_spec.expected_file_roles):
            raise ValueError(
                "manifest roles do not match the format spec's expected roles")
        if self.file_count != len(self.files):
            raise ValueError("file_count does not match the file entries")
        if self.total_bytes != sum(f.size for f in self.files):
            raise ValueError("total_bytes does not match the file entries")
        if self.compatibility.format_spec_id != self.format_spec.format_spec_id:
            raise ValueError("compatibility binds a different format spec")
        if (self.compatibility.model_spec_id != self.lineage.model_spec_id
                or self.compatibility.tokenizer_spec_id
                != self.lineage.tokenizer_spec_id):
            raise ValueError("compatibility and lineage disagree on specs")
        if self.format_spec.artifact_kind not in (
                self.production_policy.permitted_artifact_kinds):
            raise ValueError("artifact kind not permitted by the policy")
        expected_id = derive_checkpoint_id(
            format_spec_id=self.format_spec.format_spec_id,
            lineage_id=self.lineage.lineage_id,
            declared_file_roles=tuple(roles),
            simulated=self.simulated,
            model_spec_id=self.lineage.model_spec_id,
            tokenizer_spec_id=self.lineage.tokenizer_spec_id,
            checkpoint_version=self.checkpoint_format_version)
        if self.checkpoint_id != expected_id:
            raise ValueError("checkpoint_id does not match the manifest content")
        expected_digest = compute_checkpoint_digest(
            schema_version=self.schema_version,
            checkpoint_format_version=self.checkpoint_format_version,
            checkpoint_id=self.checkpoint_id, format_spec=self.format_spec,
            production_policy=self.production_policy, lineage=self.lineage,
            compatibility=self.compatibility, simulated=self.simulated,
            generated_by=self.generated_by, files=self.files)
        if self.checkpoint_digest != expected_digest:
            raise ValueError("checkpoint_digest does not match the content")
        return self


def audit_checkpoint_lineage(manifest: CheckpointManifest) -> tuple[DatasetCheck, ...]:
    """Independent lineage-integrity audit: recompute EVERY derived identity.

    Guards against ``model_construct``-style bypass of parse-time validation:
    the lineage id, compatibility id, checkpoint id, digest, parent policy,
    and file-role conformance are all recomputed from primary fields here.
    """
    checks: list[DatasetCheck] = []

    def _c(rule: str, passed: bool, detail: str = "") -> None:
        checks.append(DatasetCheck(rule=rule, passed=passed, detail=detail))

    _c("lineage_id_recomputes",
       manifest.lineage.lineage_id == derive_lineage_id(manifest.lineage))
    _c("compatibility_id_recomputes",
       manifest.compatibility.compatibility_id
       == derive_compatibility_id(manifest.compatibility))
    _c("parent_checkpoint_absent", manifest.lineage.parent_checkpoint_id is None)
    _c("parent_policy_forbids",
       manifest.production_policy.parent_checkpoint_policy == "forbidden")
    roles = tuple(f.role for f in manifest.files)
    _c("file_roles_conform",
       sorted(set(roles)) == sorted(manifest.format_spec.expected_file_roles)
       and len(roles) == len(set(roles)))
    _c("checkpoint_id_recomputes",
       manifest.checkpoint_id == derive_checkpoint_id(
           format_spec_id=manifest.format_spec.format_spec_id,
           lineage_id=manifest.lineage.lineage_id,
           declared_file_roles=roles, simulated=manifest.simulated,
           model_spec_id=manifest.lineage.model_spec_id,
           tokenizer_spec_id=manifest.lineage.tokenizer_spec_id,
           checkpoint_version=manifest.checkpoint_format_version))
    _c("checkpoint_digest_recomputes",
       manifest.checkpoint_digest == compute_checkpoint_digest(
           schema_version=manifest.schema_version,
           checkpoint_format_version=manifest.checkpoint_format_version,
           checkpoint_id=manifest.checkpoint_id,
           format_spec=manifest.format_spec,
           production_policy=manifest.production_policy,
           lineage=manifest.lineage, compatibility=manifest.compatibility,
           simulated=manifest.simulated, generated_by=manifest.generated_by,
           files=manifest.files))
    _c("simulation_honest",
       manifest.simulated is True
       and manifest.compatibility.simulated_only is True
       and manifest.compatibility.loadable_as_real_model is False
       and manifest.format_spec.artifact_kind == "simulated_checkpoint"
       and not manifest.compatibility.supported_inference_backends)
    return tuple(checks)


@dataclass(frozen=True)
class WrittenCheckpoint:
    root: Path
    checkpoint_id: str
    checkpoint_digest: str
    file_count: int
    total_bytes: int


def write_checkpoint(
    candidate: CheckpointCandidate, checkpoints_root: str | Path,
) -> WrittenCheckpoint:
    """Persist an untrusted candidate as a verified immutable checkpoint."""
    # Re-validate the candidate through a full parse round-trip: a caller that
    # bypassed validation with model_construct is caught here.
    candidate = CheckpointCandidate.model_validate(candidate.model_dump())

    entries = tuple(sorted((
        CheckpointFileEntry(
            relative_path=f.relative_path, role=f.role,
            size=len(f.content), sha256=sha256_bytes(f.content),
            serialization_id=f.serialization_id, required=f.required)
        for f in candidate.files), key=lambda e: e.relative_path))
    digest = compute_checkpoint_digest(
        schema_version=1, checkpoint_format_version=CHECKPOINT_FORMAT_VERSION,
        checkpoint_id=candidate.intended_checkpoint_id,
        format_spec=candidate.format_spec,
        production_policy=candidate.production_policy,
        lineage=candidate.lineage, compatibility=candidate.compatibility,
        simulated=True, generated_by=CHECKPOINT_GENERATOR, files=entries)
    manifest = CheckpointManifest(
        checkpoint_id=candidate.intended_checkpoint_id,
        checkpoint_digest=digest, format_spec=candidate.format_spec,
        production_policy=candidate.production_policy,
        lineage=candidate.lineage, compatibility=candidate.compatibility,
        files=entries, file_count=len(entries),
        total_bytes=sum(e.size for e in entries),
        generated_by=CHECKPOINT_GENERATOR)

    root = Path(checkpoints_root) / manifest.checkpoint_id
    if root.exists() and any(root.iterdir()):
        raise CheckpointStoreError(f"checkpoint already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / CHECKPOINT_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    (root / CHECKPOINT_PAYLOAD_DIR).mkdir(exist_ok=True)
    for f in candidate.files:
        atomic_write_bytes(root / f.relative_path, f.content)
    atomic_write_bytes(root / CHECKPOINT_MANIFEST_FILE,
                       canonical_json_bytes(manifest))
    result = verify_checkpoint(root)
    hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise CheckpointStoreError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenCheckpoint(
        root=root, checkpoint_id=manifest.checkpoint_id,
        checkpoint_digest=manifest.checkpoint_digest,
        file_count=manifest.file_count, total_bytes=manifest.total_bytes)


class CheckpointVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    checkpoint_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _check(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def verify_checkpoint(checkpoint_dir: str | Path) -> CheckpointVerificationResult:
    """Structured, fail-closed verification of a persisted checkpoint."""
    root = Path(checkpoint_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_check("checkpoint_dir_present", False,
                             f"not a directory: {root}"))
        return CheckpointVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_check("checkpoint_dir_present", True))

    marker_absent = not (root / CHECKPOINT_INCOMPLETE_MARKER).exists()
    checks.append(_check("incomplete_marker_absent", marker_absent))

    manifest_path = root / CHECKPOINT_MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_check("manifest_present", False))
        return CheckpointVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_check("manifest_present", True))
    try:
        manifest = CheckpointManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_check("manifest_parses", False, str(exc).splitlines()[0]))
        return CheckpointVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_check("manifest_parses", True))
    digest = manifest.checkpoint_digest

    checks.append(_check(
        "schema_supported",
        manifest.schema_version in SUPPORTED_CHECKPOINT_SCHEMA))
    checks.append(_check(
        "format_supported",
        manifest.checkpoint_format_version in SUPPORTED_CHECKPOINT_FORMAT))
    checks.append(_check(
        "checkpoint_dir_matches_id", root.name == manifest.checkpoint_id,
        "" if root.name == manifest.checkpoint_id
        else f"directory {root.name} != {manifest.checkpoint_id}"))

    declared = {f.relative_path for f in manifest.files}
    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != CHECKPOINT_INCOMPLETE_MARKER
    }
    allowed = declared | {CHECKPOINT_MANIFEST_FILE}
    missing = sorted(allowed - on_disk)
    unexpected = sorted(on_disk - allowed)
    checks.append(_check("no_missing_files", not missing,
                         "" if not missing else f"missing={missing}"))
    checks.append(_check("no_unexpected_files", not unexpected,
                         "" if not unexpected else f"unexpected={unexpected}"))

    symlink_free = not any(
        p.is_symlink() for p in root.rglob("*"))
    checks.append(_check("no_symlinks", symlink_free))
    executables = sorted(
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and os.access(p, os.X_OK))
    checks.append(_check("no_executable_payloads", not executables,
                         "" if not executables else f"executable={executables}"))

    hash_ok, size_ok = True, True
    detail = ""
    total = 0
    for entry in manifest.files:
        fpath = root / entry.relative_path
        if not fpath.is_file():
            hash_ok, detail = False, f"missing {entry.relative_path}"
            break
        raw = fpath.read_bytes()
        total += len(raw)
        if len(raw) != entry.size:
            size_ok, detail = False, f"size mismatch for {entry.relative_path}"
            break
        if sha256_bytes(raw) != entry.sha256:
            hash_ok, detail = False, f"hash mismatch for {entry.relative_path}"
            break
    checks.append(_check("file_hashes_match", hash_ok, detail if not hash_ok else ""))
    checks.append(_check("file_sizes_match", size_ok, detail if not size_ok else ""))
    checks.append(_check(
        "total_size_matches",
        hash_ok and size_ok and total == manifest.total_bytes))
    checks.append(_check("file_count_matches",
                         manifest.file_count == len(manifest.files)))

    # Simulation honesty against the actual bytes: the fake payload must carry
    # the fake magic, so it can never be a real serialized model.
    fake_entry = next(
        (f for f in manifest.files
         if f.role is CheckpointFileRole.FAKE_MODEL_PAYLOAD), None)
    magic_ok = False
    if fake_entry is not None and (root / fake_entry.relative_path).is_file():
        magic_ok = (root / fake_entry.relative_path).read_bytes().startswith(
            FAKE_PAYLOAD_MAGIC)
    checks.append(_check("fake_payload_magic_present", magic_ok))

    checks.extend(audit_checkpoint_lineage(manifest))

    return CheckpointVerificationResult(
        verified=all(c.passed for c in checks), checkpoint_digest=digest,
        checks=tuple(checks))


@dataclass(frozen=True)
class CheckpointPayloadDescriptor:
    """A SAFE payload reference: path + role + facts, never interpreted bytes."""

    relative_path: str
    role: CheckpointFileRole
    size: int
    sha256: str
    serialization_id: str


@dataclass(frozen=True)
class LoadedCheckpoint:
    root: Path
    manifest: CheckpointManifest
    payloads: tuple[CheckpointPayloadDescriptor, ...]


def read_checkpoint_manifest(checkpoint_dir: str | Path) -> CheckpointManifest:
    """Verify then return the manifest only; fail closed."""
    root = Path(checkpoint_dir)
    result = verify_checkpoint(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise CheckpointStoreError(f"checkpoint failed verification: {detail}")
    return CheckpointManifest.model_validate_json(
        (root / CHECKPOINT_MANIFEST_FILE).read_bytes())


def read_verified_checkpoint(checkpoint_dir: str | Path) -> LoadedCheckpoint:
    """Verify then return metadata + payload DESCRIPTORS (never model objects).

    Deliberately, there is no ``load_model`` anywhere in this package: a
    Gate 10D fake payload cannot be interpreted as weights through this API.
    """
    manifest = read_checkpoint_manifest(checkpoint_dir)
    payloads = tuple(
        CheckpointPayloadDescriptor(
            relative_path=f.relative_path, role=f.role, size=f.size,
            sha256=f.sha256, serialization_id=f.serialization_id)
        for f in manifest.files)
    return LoadedCheckpoint(root=Path(checkpoint_dir), manifest=manifest,
                            payloads=payloads)


def open_checkpoint_payload(
    checkpoint_dir: str | Path, relative_path: str,
) -> bytes:
    """Return the raw bytes of ONE declared payload file, after verification.

    This is a byte accessor for audit/tooling — it performs no interpretation,
    no deserialization into model objects, and refuses undeclared paths.
    """
    manifest = read_checkpoint_manifest(checkpoint_dir)
    declared = {f.relative_path for f in manifest.files}
    if relative_path not in declared:
        raise CheckpointStoreError(
            f"path is not a declared checkpoint payload: {relative_path!r}")
    return (Path(checkpoint_dir) / relative_path).read_bytes()
