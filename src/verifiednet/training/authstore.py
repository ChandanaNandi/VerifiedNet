"""Immutable authorization persistence: manifest, writer, verifier, reader.

    training-authorizations/<authorization_id>/
        manifest.json         # self-validating ids + file hashes + digest
        environment.json      # TrainingEnvironmentSnapshot (runtime evidence)
        findings.json         # ordered findings (standalone copy)
        authorization.json    # TrainingExecutionAuthorization

No model or tokenizer bytes, no training examples, no checkpoint bytes, no
timestamps, and no host secrets: the snapshot and authorization schemas are
``extra="forbid"``, so usernames/hostnames/paths/env-vars are structurally
unrepresentable in the persisted evidence. The verifier recomputes validity —
the stored ``authorized`` boolean is never trusted: an artifact claiming
authorization while carrying an ERROR finding, an incomplete stage list, a
mutable revision, or an unverified resolution fails closed.
"""

from __future__ import annotations

import json
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
from verifiednet.training.backend import (
    DeterminismCategory,
    TrainingEnvironmentSnapshot,
)
from verifiednet.training.preflight import (
    FindingSeverity,
    PreflightFinding,
    TrainingExecutionAuthorization,
)
from verifiednet.training.spec import FORBIDDEN_REVISIONS

AUTHORIZATION_FORMAT_VERSION = 1
AUTHORIZATION_GENERATOR = "verifiednet.training.preflight"

AUTH_MANIFEST_FILE = "manifest.json"
AUTH_ENVIRONMENT_FILE = "environment.json"
AUTH_FINDINGS_FILE = "findings.json"
AUTH_AUTHORIZATION_FILE = "authorization.json"
AUTH_INCOMPLETE_MARKER = ".INCOMPLETE"
AUTH_CONTENT_FILES = (AUTH_AUTHORIZATION_FILE, AUTH_ENVIRONMENT_FILE,
                      AUTH_FINDINGS_FILE)
SUPPORTED_AUTH_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_AUTH_FORMAT: frozenset[int] = frozenset({1})


class AuthorizationStoreError(VerifiedNetError):
    """Writing/reading/verifying an authorization directory failed."""


def compute_authorization_digest(
    *,
    schema_version: int,
    authorization_format_version: int,
    authorization_id: str,
    training_plan_id: str,
    plan_digest: str,
    training_corpus_id: str,
    training_corpus_digest: str,
    backend_spec_id: str,
    environment_snapshot_id: str,
    model_artifact_id: str | None,
    tokenizer_artifact_id: str | None,
    device_capability_id: str,
    determinism_category: str,
    authorized: bool,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    payload = {
        "schema_version": schema_version,
        "authorization_format_version": authorization_format_version,
        "authorization_id": authorization_id,
        "training_plan_id": training_plan_id,
        "plan_digest": plan_digest,
        "training_corpus_id": training_corpus_id,
        "training_corpus_digest": training_corpus_digest,
        "backend_spec_id": backend_spec_id,
        "environment_snapshot_id": environment_snapshot_id,
        "model_artifact_id": model_artifact_id,
        "tokenizer_artifact_id": tokenizer_artifact_id,
        "device_capability_id": device_capability_id,
        "determinism_category": determinism_category,
        "authorized": authorized,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "authdig-" + sha256_canonical(payload)[:24]


class TrainingAuthorizationManifest(StrictModel):
    """Self-validating manifest for one persisted preflight authorization."""

    schema_version: Literal[1] = 1
    authorization_format_version: Literal[1] = 1
    authorization_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    backend_spec_id: str = Field(min_length=1)
    environment_snapshot_id: str = Field(min_length=1)
    model_artifact_id: str | None = None
    tokenizer_artifact_id: str | None = None
    device_capability_id: str = Field(min_length=1)
    determinism_category: DeterminismCategory
    authorized: bool
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    authorization_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> TrainingAuthorizationManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        if set(paths) != set(AUTH_CONTENT_FILES):
            raise ValueError("manifest files do not match the declared layout")
        expected = compute_authorization_digest(
            schema_version=self.schema_version,
            authorization_format_version=self.authorization_format_version,
            authorization_id=self.authorization_id,
            training_plan_id=self.training_plan_id,
            plan_digest=self.plan_digest,
            training_corpus_id=self.training_corpus_id,
            training_corpus_digest=self.training_corpus_digest,
            backend_spec_id=self.backend_spec_id,
            environment_snapshot_id=self.environment_snapshot_id,
            model_artifact_id=self.model_artifact_id,
            tokenizer_artifact_id=self.tokenizer_artifact_id,
            device_capability_id=self.device_capability_id,
            determinism_category=self.determinism_category.value,
            authorized=self.authorized, generated_by=self.generated_by,
            files=self.files)
        if self.authorization_digest != expected:
            raise ValueError(
                "authorization_digest does not match manifest content")
        return self


@dataclass(frozen=True)
class WrittenAuthorization:
    root: Path
    authorization_id: str
    authorization_digest: str
    authorized: bool


def write_training_authorization(
    authorization: TrainingExecutionAuthorization,
    snapshot: TrainingEnvironmentSnapshot,
    authorizations_root: str | Path,
) -> WrittenAuthorization:
    """Persist preflight evidence immutably; never overwrite."""
    if snapshot.environment_snapshot_id != authorization.environment_snapshot_id:
        raise AuthorizationStoreError(
            "snapshot does not match the authorization's evidence")
    content: dict[str, bytes] = {
        AUTH_AUTHORIZATION_FILE: canonical_json_bytes(authorization),
        AUTH_ENVIRONMENT_FILE: canonical_json_bytes(snapshot),
        AUTH_FINDINGS_FILE: canonical_json_bytes(
            [f.model_dump(mode="json") for f in authorization.findings]),
    }
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path))
    model_artifact_id = (
        authorization.model_artifact.resolved_model_artifact_id
        if authorization.model_artifact is not None else None)
    tokenizer_artifact_id = (
        authorization.tokenizer_artifact.resolved_tokenizer_artifact_id
        if authorization.tokenizer_artifact is not None else None)
    digest = compute_authorization_digest(
        schema_version=1,
        authorization_format_version=AUTHORIZATION_FORMAT_VERSION,
        authorization_id=authorization.authorization_id,
        training_plan_id=authorization.training_plan_id,
        plan_digest=authorization.plan_digest,
        training_corpus_id=authorization.training_corpus_id,
        training_corpus_digest=authorization.training_corpus_digest,
        backend_spec_id=authorization.backend_spec_id,
        environment_snapshot_id=authorization.environment_snapshot_id,
        model_artifact_id=model_artifact_id,
        tokenizer_artifact_id=tokenizer_artifact_id,
        device_capability_id=authorization.device_capability_id,
        determinism_category=authorization.determinism_category.value,
        authorized=authorization.authorized,
        generated_by=AUTHORIZATION_GENERATOR, files=files)
    manifest = TrainingAuthorizationManifest(
        authorization_id=authorization.authorization_id,
        training_plan_id=authorization.training_plan_id,
        plan_digest=authorization.plan_digest,
        training_corpus_id=authorization.training_corpus_id,
        training_corpus_digest=authorization.training_corpus_digest,
        backend_spec_id=authorization.backend_spec_id,
        environment_snapshot_id=authorization.environment_snapshot_id,
        model_artifact_id=model_artifact_id,
        tokenizer_artifact_id=tokenizer_artifact_id,
        device_capability_id=authorization.device_capability_id,
        determinism_category=authorization.determinism_category,
        authorized=authorization.authorized,
        generated_by=AUTHORIZATION_GENERATOR, files=files,
        authorization_digest=digest)

    root = Path(authorizations_root) / authorization.authorization_id
    if root.exists() and any(root.iterdir()):
        raise AuthorizationStoreError(f"authorization already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / AUTH_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    for name, payload in sorted(content.items()):
        atomic_write_bytes(root / name, payload)
    atomic_write_bytes(root / AUTH_MANIFEST_FILE, canonical_json_bytes(manifest))
    result = verify_training_authorization(root)
    hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise AuthorizationStoreError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenAuthorization(
        root=root, authorization_id=authorization.authorization_id,
        authorization_digest=digest, authorized=authorization.authorized)


class AuthorizationVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    authorization_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def verify_training_authorization(
    authorization_dir: str | Path,
) -> AuthorizationVerificationResult:
    """Verify a persisted authorization; recompute validity; fail closed."""
    root = Path(authorization_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("authorization_dir_present", False,
                         f"not a directory: {root}"))
        return AuthorizationVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("authorization_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / AUTH_INCOMPLETE_MARKER).exists()))

    manifest_path = root / AUTH_MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return AuthorizationVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = TrainingAuthorizationManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return AuthorizationVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.authorization_digest

    checks.append(_c("schema_supported",
                     manifest.schema_version in SUPPORTED_AUTH_SCHEMA))
    checks.append(_c(
        "format_supported",
        manifest.authorization_format_version in SUPPORTED_AUTH_FORMAT))

    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != AUTH_INCOMPLETE_MARKER
    }
    allowed = set(AUTH_CONTENT_FILES) | {AUTH_MANIFEST_FILE}
    missing = sorted(allowed - on_disk)
    unexpected = sorted(on_disk - allowed)
    checks.append(_c("no_missing_files", not missing,
                     "" if not missing else f"missing={missing}"))
    checks.append(_c("no_unexpected_files", not unexpected,
                     "" if not unexpected else f"unexpected={unexpected}"))

    hash_ok, hash_detail = True, ""
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

    # Reconstruct the evidence; every self-validating model recomputes its own
    # ids and validity, so the stored authorized boolean is never trusted.
    auth_ok, env_ok, findings_ok, binding_ok, revision_ok = (
        True, True, True, True, True)
    detail = ""
    if hash_ok:
        try:
            authorization = TrainingExecutionAuthorization.model_validate_json(
                (root / AUTH_AUTHORIZATION_FILE).read_bytes())
        except (OSError, ValidationError) as exc:
            auth_ok = False
            detail = str(exc).splitlines()[0]
            authorization = None
        try:
            snapshot = TrainingEnvironmentSnapshot.model_validate_json(
                (root / AUTH_ENVIRONMENT_FILE).read_bytes())
        except (OSError, ValidationError) as exc:
            env_ok = False
            detail = detail or str(exc).splitlines()[0]
            snapshot = None
        if auth_ok and authorization is not None:
            try:
                raw_findings = json.loads(
                    (root / AUTH_FINDINGS_FILE).read_bytes())
                findings = tuple(
                    PreflightFinding.model_validate_json(json.dumps(item))
                    for item in raw_findings)
                findings_ok = findings == authorization.findings
            except (OSError, ValueError, ValidationError):
                findings_ok = False
            binding_ok = (
                authorization.authorization_id == manifest.authorization_id
                and authorization.training_plan_id == manifest.training_plan_id
                and authorization.plan_digest == manifest.plan_digest
                and authorization.training_corpus_id
                == manifest.training_corpus_id
                and authorization.training_corpus_digest
                == manifest.training_corpus_digest
                and authorization.backend_spec_id == manifest.backend_spec_id
                and authorization.environment_snapshot_id
                == manifest.environment_snapshot_id
                and authorization.device_capability_id
                == manifest.device_capability_id
                and authorization.determinism_category
                is manifest.determinism_category
                and authorization.authorized == manifest.authorized
                and (snapshot is None or snapshot.environment_snapshot_id
                     == authorization.environment_snapshot_id))
            # recomputed validity: authorized requires zero ERROR findings and
            # immutably resolved artifacts (also enforced at parse; recheck
            # here so a bypassed construction cannot slip through the store)
            has_error = any(f.severity is FindingSeverity.ERROR
                            for f in authorization.findings)
            if authorization.authorized and has_error:
                binding_ok = False
                detail = detail or "authorized=True with ERROR findings"
            for artifact_revision in (
                (authorization.model_artifact.model_revision
                 if authorization.model_artifact is not None else None),
                (authorization.tokenizer_artifact.tokenizer_revision
                 if authorization.tokenizer_artifact is not None else None),
            ):
                if (artifact_revision is not None
                        and artifact_revision.strip().lower()
                        in FORBIDDEN_REVISIONS):
                    revision_ok = False
                    detail = detail or "mutable artifact revision persisted"
    else:
        auth_ok = env_ok = False
    checks.append(_c("authorization_parses_and_revalidates", auth_ok, detail))
    checks.append(_c("environment_snapshot_parses", env_ok))
    checks.append(_c("findings_match_authorization", findings_ok))
    checks.append(_c("manifest_matches_authorization", binding_ok, detail))
    checks.append(_c("no_mutable_revisions", revision_ok, detail))

    return AuthorizationVerificationResult(
        verified=all(c.passed for c in checks), authorization_digest=digest,
        checks=tuple(checks))


@dataclass(frozen=True)
class LoadedAuthorization:
    manifest: TrainingAuthorizationManifest
    authorization: TrainingExecutionAuthorization
    snapshot: TrainingEnvironmentSnapshot


def read_training_authorization(
    authorization_dir: str | Path,
) -> LoadedAuthorization:
    """Verify then reconstruct persisted preflight evidence; fail closed."""
    root = Path(authorization_dir)
    result = verify_training_authorization(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise AuthorizationStoreError(
            f"authorization failed verification: {detail}")
    return LoadedAuthorization(
        manifest=TrainingAuthorizationManifest.model_validate_json(
            (root / AUTH_MANIFEST_FILE).read_bytes()),
        authorization=TrainingExecutionAuthorization.model_validate_json(
            (root / AUTH_AUTHORIZATION_FILE).read_bytes()),
        snapshot=TrainingEnvironmentSnapshot.model_validate_json(
            (root / AUTH_ENVIRONMENT_FILE).read_bytes()))
