"""Immutable model/tokenizer artifact resolution (Gate 10E).

Before execution can be authorized, every mutable external input must resolve
to an immutable identity: a pinned revision PLUS a content hash binding actual
local artifact bytes where available. A name alone is never proof of immutable
resolution; a mutable alias (``latest``/``main``/…) is rejected twice — once
by the Gate 10B spec validators, and again here, defensively.

Resolvers observe; they never download. Offline preflight performs NO network
access — remote download is a separate, explicitly authorized operation that
does not exist in this gate. Tokenizer resolution is deliberately independent
of model resolution: tokenizer identity is never assumed from model identity,
and any disagreement on the pinned revision, special-vocabulary policy, or
padding/truncation contract fails preflight.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel
from verifiednet.training.spec import (
    FORBIDDEN_REVISIONS,
    TokenizerSpec,
    TrainableModelSpec,
)

FAKE_MODEL_RESOLVER_ID = "fake-model-resolver-v1"
FAKE_TOKENIZER_RESOLVER_ID = "fake-tokenizer-resolver-v1"


class ArtifactResolutionError(VerifiedNetError):
    """A model/tokenizer artifact could not be resolved immutably."""


def _require_immutable(revision: str, what: str) -> None:
    if revision.strip().lower() in FORBIDDEN_REVISIONS:
        raise ArtifactResolutionError(
            f"mutable {what} revision cannot be resolved: {revision!r}")


class ResolvedModelArtifact(StrictModel):
    """An immutable resolution of one model spec on one machine."""

    schema_version: Literal[1] = 1
    model_spec_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    content_hash: str | None = None
    artifact_source: Literal["local_cache", "fake", "unresolved"]
    locally_cached: bool
    required_files: tuple[str, ...] = Field(default_factory=tuple)
    declared_parameter_count: int | None = Field(default=None, ge=1)
    verification_status: Literal["verified", "unverified"]
    resolved_model_artifact_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ResolvedModelArtifact:
        if self.model_revision.strip().lower() in FORBIDDEN_REVISIONS:
            raise ValueError("a mutable model revision is never resolved")
        files = list(self.required_files)
        if files != sorted(files) or len(files) != len(set(files)):
            raise ValueError("required_files must be sorted and unique")
        if self.verification_status == "verified":
            if self.content_hash is None:
                raise ValueError("verified resolution requires a content hash")
            if not self.locally_cached:
                raise ValueError("verified resolution requires a local artifact")
            if self.declared_parameter_count is None:
                raise ValueError(
                    "verified resolution requires resolved parameter count")
        if self.resolved_model_artifact_id != derive_model_artifact_id(self):
            raise ValueError(
                "resolved_model_artifact_id does not match the resolution")
        return self


def derive_model_artifact_id(artifact: ResolvedModelArtifact) -> str:
    payload = artifact.model_dump(mode="json")
    payload.pop("resolved_model_artifact_id", None)
    return "modelart-" + sha256_canonical(payload)[:16]


class ResolvedTokenizerArtifact(StrictModel):
    """An immutable resolution of one tokenizer spec on one machine."""

    schema_version: Literal[1] = 1
    tokenizer_spec_id: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    content_hash: str | None = None
    artifact_source: Literal["local_cache", "fake", "unresolved"]
    locally_cached: bool
    special_vocab_policy_agreement: bool
    padding_truncation_compatible: bool
    required_files: tuple[str, ...] = Field(default_factory=tuple)
    verification_status: Literal["verified", "unverified"]
    resolved_tokenizer_artifact_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ResolvedTokenizerArtifact:
        if self.tokenizer_revision.strip().lower() in FORBIDDEN_REVISIONS:
            raise ValueError("a mutable tokenizer revision is never resolved")
        files = list(self.required_files)
        if files != sorted(files) or len(files) != len(set(files)):
            raise ValueError("required_files must be sorted and unique")
        if self.verification_status == "verified":
            if self.content_hash is None:
                raise ValueError("verified resolution requires a content hash")
            if not self.locally_cached:
                raise ValueError("verified resolution requires a local artifact")
            if not (self.special_vocab_policy_agreement
                    and self.padding_truncation_compatible):
                raise ValueError(
                    "verified tokenizer resolution requires policy agreement")
        if (self.resolved_tokenizer_artifact_id
                != derive_tokenizer_artifact_id(self)):
            raise ValueError(
                "resolved_tokenizer_artifact_id does not match the resolution")
        return self


def derive_tokenizer_artifact_id(artifact: ResolvedTokenizerArtifact) -> str:
    payload = artifact.model_dump(mode="json")
    payload.pop("resolved_tokenizer_artifact_id", None)
    return "tokart-" + sha256_canonical(payload)[:16]


@runtime_checkable
class ModelArtifactResolver(Protocol):
    def resolve(self, spec: TrainableModelSpec) -> ResolvedModelArtifact: ...


@runtime_checkable
class TokenizerArtifactResolver(Protocol):
    def resolve(self, spec: TokenizerSpec) -> ResolvedTokenizerArtifact: ...


class FakeModelArtifactResolver:
    """Deterministic offline resolver: content hash derived from the spec.

    ``cached=False`` simulates a missing local artifact (unverified →
    preflight refuses); ``parameter_count`` is the EXPLICIT declaration the
    memory estimator requires (never inferred from a model name).
    """

    resolver_id = FAKE_MODEL_RESOLVER_ID

    def __init__(self, *, parameter_count: int = 10_000_000,
                 cached: bool = True) -> None:
        self._parameter_count = parameter_count
        self._cached = cached

    def resolve(self, spec: TrainableModelSpec) -> ResolvedModelArtifact:
        _require_immutable(spec.model_revision, "model")
        if not self._cached:
            probe = ResolvedModelArtifact.model_construct(
                model_spec_id=spec.model_spec_id,
                model_revision=spec.model_revision, content_hash=None,
                artifact_source="unresolved", locally_cached=False,
                required_files=(), declared_parameter_count=None,
                verification_status="unverified")
            return ResolvedModelArtifact(
                model_spec_id=spec.model_spec_id,
                model_revision=spec.model_revision,
                artifact_source="unresolved", locally_cached=False,
                verification_status="unverified",
                resolved_model_artifact_id=derive_model_artifact_id(probe))
        content_hash = "sha256:" + sha256_canonical({
            "resolver": self.resolver_id, "model_spec_id": spec.model_spec_id,
            "revision": spec.model_revision})
        files = ("config.json", "model.weights")
        probe = ResolvedModelArtifact.model_construct(
            model_spec_id=spec.model_spec_id,
            model_revision=spec.model_revision, content_hash=content_hash,
            artifact_source="fake", locally_cached=True,
            required_files=files,
            declared_parameter_count=self._parameter_count,
            verification_status="verified")
        return ResolvedModelArtifact(
            model_spec_id=spec.model_spec_id,
            model_revision=spec.model_revision, content_hash=content_hash,
            artifact_source="fake", locally_cached=True,
            required_files=files,
            declared_parameter_count=self._parameter_count,
            verification_status="verified",
            resolved_model_artifact_id=derive_model_artifact_id(probe))


class FakeTokenizerArtifactResolver:
    """Deterministic offline tokenizer resolver, independent of the model."""

    resolver_id = FAKE_TOKENIZER_RESOLVER_ID

    def __init__(self, *, cached: bool = True,
                 special_vocab_agrees: bool = True) -> None:
        self._cached = cached
        self._special_vocab_agrees = special_vocab_agrees

    def resolve(self, spec: TokenizerSpec) -> ResolvedTokenizerArtifact:
        _require_immutable(spec.tokenizer_revision, "tokenizer")
        if not self._cached or not self._special_vocab_agrees:
            probe = ResolvedTokenizerArtifact.model_construct(
                tokenizer_spec_id=spec.tokenizer_spec_id,
                tokenizer_revision=spec.tokenizer_revision, content_hash=None,
                artifact_source="unresolved", locally_cached=self._cached,
                special_vocab_policy_agreement=self._special_vocab_agrees,
                padding_truncation_compatible=True, required_files=(),
                verification_status="unverified")
            return ResolvedTokenizerArtifact(
                tokenizer_spec_id=spec.tokenizer_spec_id,
                tokenizer_revision=spec.tokenizer_revision,
                artifact_source="unresolved", locally_cached=self._cached,
                special_vocab_policy_agreement=self._special_vocab_agrees,
                padding_truncation_compatible=True,
                verification_status="unverified",
                resolved_tokenizer_artifact_id=derive_tokenizer_artifact_id(probe))
        content_hash = "sha256:" + sha256_canonical({
            "resolver": self.resolver_id,
            "tokenizer_spec_id": spec.tokenizer_spec_id,
            "revision": spec.tokenizer_revision})
        files = ("tokenizer.json", "tokenizer_config.json")
        probe = ResolvedTokenizerArtifact.model_construct(
            tokenizer_spec_id=spec.tokenizer_spec_id,
            tokenizer_revision=spec.tokenizer_revision,
            content_hash=content_hash, artifact_source="fake",
            locally_cached=True, special_vocab_policy_agreement=True,
            padding_truncation_compatible=True, required_files=files,
            verification_status="verified")
        return ResolvedTokenizerArtifact(
            tokenizer_spec_id=spec.tokenizer_spec_id,
            tokenizer_revision=spec.tokenizer_revision,
            content_hash=content_hash, artifact_source="fake",
            locally_cached=True, special_vocab_policy_agreement=True,
            padding_truncation_compatible=True, required_files=files,
            verification_status="verified",
            resolved_tokenizer_artifact_id=derive_tokenizer_artifact_id(probe))
