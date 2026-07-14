"""Checkpoint contracts: format, policy, lineage, compatibility, identity (10D).

Gate 10D defines WHAT a trained checkpoint is — its format contract, its
provenance lineage, its compatibility declaration, its identity, and its
untrusted candidate form — while the only payloads that exist are FAKE,
deterministic, and unmistakably simulated. No real training, no real weights,
no model/tokenizer loading, no ML framework.

The core distinction, preserved everywhere in this module and the store:

* ``CheckpointFormatSpec``       — the declared format/compatibility contract;
* ``CheckpointCandidate``        — UNTRUSTED files produced by a backend,
                                   carrying raw content, never hashes to trust;
* verified checkpoint artifact   — exists ONLY as the output of the writer +
                                   verifier in ``checkpointstore`` (an on-disk
                                   directory with a self-validating manifest).
  Instantiating a manifest model does not make a checkpoint trusted; trust
  comes from ``verify_checkpoint`` over the persisted artifact.

Identity is two-layered and explicit:

* ``checkpoint_id``     — LOGICAL identity: format + lineage + declared roles +
                          simulated status + model/tokenizer compatibility +
                          checkpoint version. Never depends on paths or bytes.
* ``checkpoint_digest`` — CONTENT identity: the verified bytes (path-sorted
                          file hashes/sizes/roles) plus every configuration
                          block. Computed in ``checkpointstore``.

Gate 10B's ``checkpoint_policy="none"`` is untouched: it means the TRAINER
writes no checkpoints during execution. Gate 10D production is a separate,
post-execution artifact operation over a verified completed execution.
"""

from __future__ import annotations

import posixpath
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel

CHECKPOINT_VERSION = 1
FAKE_CHECKPOINT_PAYLOAD_FORMAT = "verifiednet.fake-checkpoint-v1"
FAKE_CHECKPOINT_PRODUCER_ID = "fake-checkpoint-producer-v1"
#: Every fake payload binary starts with this magic; nothing real begins so.
FAKE_PAYLOAD_MAGIC = b"VERIFIEDNET-FAKE-CHECKPOINT-V1\n"


class CheckpointError(VerifiedNetError):
    """A checkpoint contract, candidate, or artifact operation failed."""


class CheckpointFileRole(StrEnum):
    """Explicit roles a checkpoint file may declare."""

    FAKE_MODEL_PAYLOAD = "fake_model_payload"
    MODEL_CONFIG_METADATA = "model_config_metadata"
    TOKENIZER_COMPAT_METADATA = "tokenizer_compat_metadata"
    CHECKPOINT_METADATA = "checkpoint_metadata"
    # Declared for the contract's completeness; FORBIDDEN by the Gate 10D
    # format spec (excluded inclusions / no adapters exist yet):
    RESUME_METADATA = "resume_metadata"
    ADAPTER_METADATA = "adapter_metadata"


#: The only roles a Gate 10D checkpoint may contain, exactly once each.
FAKE_CHECKPOINT_ROLES: tuple[CheckpointFileRole, ...] = (
    CheckpointFileRole.CHECKPOINT_METADATA,
    CheckpointFileRole.FAKE_MODEL_PAYLOAD,
    CheckpointFileRole.MODEL_CONFIG_METADATA,
    CheckpointFileRole.TOKENIZER_COMPAT_METADATA,
)


def validate_checkpoint_relative_path(path: str) -> str:
    """Fail closed on any unsafe or non-canonical relative path."""
    if not path:
        raise ValueError("empty relative path")
    if "\\" in path:
        raise ValueError(f"backslash separators are forbidden: {path!r}")
    if path.startswith("/") or path.startswith("~"):
        raise ValueError(f"absolute or home-relative path forbidden: {path!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"unsafe path component in {path!r}")
    if posixpath.normpath(path) != path:
        raise ValueError(f"non-canonical path: {path!r}")
    if not path.startswith("payload/"):
        raise ValueError(f"checkpoint files must live under payload/: {path!r}")
    return path


# ---------------------------------------------------------------------------
# Format specification (fake-only in this gate)
# ---------------------------------------------------------------------------


class CheckpointFormatSpec(StrictModel):
    """Frozen, versioned checkpoint format and compatibility contract.

    Every field that could make a Gate 10D artifact impersonate something real
    is Literal-locked: the kind is ``simulated_checkpoint``, the payload format
    is the fake format, weights are declared simulated, optimizer/scheduler/
    resume state are excluded, tokenizer/configuration are metadata-only. Real
    formats (full model, LoRA/QLoRA adapters, safetensors, HF layouts) must be
    introduced EXPLICITLY by a later gate as new spec versions — they cannot
    be expressed here.
    """

    schema_version: Literal[1] = 1
    format_version: Literal[1] = 1
    artifact_kind: Literal["simulated_checkpoint"] = "simulated_checkpoint"
    payload_format: Literal["verifiednet.fake-checkpoint-v1"] = (
        "verifiednet.fake-checkpoint-v1")
    expected_file_roles: tuple[CheckpointFileRole, ...] = Field(min_length=1)
    weights_declaration: Literal["simulated_none"] = "simulated_none"
    tokenizer_inclusion: Literal["metadata_only"] = "metadata_only"
    configuration_inclusion: Literal["metadata_only"] = "metadata_only"
    optimizer_state_inclusion: Literal["excluded"] = "excluded"
    scheduler_state_inclusion: Literal["excluded"] = "excluded"
    resume_state_inclusion: Literal["excluded"] = "excluded"
    serialization_format: Literal["canonical-json+fake-bytes-v1"] = (
        "canonical-json+fake-bytes-v1")
    compatibility_version: Literal[1] = 1
    format_spec_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointFormatSpec:
        roles = list(self.expected_file_roles)
        if roles != sorted(roles) or len(roles) != len(set(roles)):
            raise ValueError("expected_file_roles must be sorted and unique")
        forbidden = {CheckpointFileRole.RESUME_METADATA,
                     CheckpointFileRole.ADAPTER_METADATA}
        if forbidden & set(roles):
            raise ValueError(
                "resume/adapter roles are forbidden by the Gate 10D format")
        if self.format_spec_id != derive_format_spec_id(self):
            raise ValueError("format_spec_id does not match the format spec")
        return self


def derive_format_spec_id(spec: CheckpointFormatSpec) -> str:
    payload = spec.model_dump(mode="json")
    payload.pop("format_spec_id", None)
    return "ckptfmt-" + sha256_canonical(payload)[:16]


def build_fake_checkpoint_format_spec() -> CheckpointFormatSpec:
    """The ONLY checkpoint format constructible in Gate 10D."""
    probe = CheckpointFormatSpec.model_construct(
        expected_file_roles=tuple(sorted(FAKE_CHECKPOINT_ROLES)))
    return CheckpointFormatSpec(
        expected_file_roles=tuple(sorted(FAKE_CHECKPOINT_ROLES)),
        format_spec_id=derive_format_spec_id(probe))


# ---------------------------------------------------------------------------
# Production policy
# ---------------------------------------------------------------------------


class CheckpointProductionPolicy(StrictModel):
    """Frozen, versioned policy for turning an execution into a checkpoint."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    required_execution_state: Literal["completed"] = "completed"
    permitted_artifact_kinds: tuple[Literal["simulated_checkpoint"], ...] = (
        ("simulated_checkpoint",))
    include_tokenizer_metadata: Literal[True] = True
    include_configuration_metadata: Literal[True] = True
    include_optimizer_state: Literal[False] = False
    include_scheduler_state: Literal[False] = False
    include_resume_state: Literal[False] = False
    parent_checkpoint_policy: Literal["forbidden"] = "forbidden"
    production_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointProductionPolicy:
        if len(set(self.permitted_artifact_kinds)) != len(
                self.permitted_artifact_kinds):
            raise ValueError("permitted_artifact_kinds must be unique")
        if self.production_policy_id != derive_production_policy_id(self):
            raise ValueError("production_policy_id does not match the policy")
        return self


def derive_production_policy_id(policy: CheckpointProductionPolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("production_policy_id", None)
    return "ckptpol-" + sha256_canonical(payload)[:16]


def build_default_checkpoint_production_policy() -> CheckpointProductionPolicy:
    probe = CheckpointProductionPolicy.model_construct()
    return CheckpointProductionPolicy(
        production_policy_id=derive_production_policy_id(probe))


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


class CheckpointLineage(StrictModel):
    """Frozen, content-addressed provenance binding for a checkpoint.

    Binds the checkpoint to the EXACT verified sources it came from. In this
    gate ``parent_checkpoint_id`` is structurally ``None``: no prior checkpoint
    can have been consumed, because none exists. A RESUMED execution does NOT
    create checkpoint ancestry — resume lineage is already recorded inside the
    execution artifact (``resumed_from_execution_id``); the checkpoint simply
    binds to that verified execution. Checkpoint chaining (warm starts,
    adapter continuation) is a later gate's explicit contract.
    """

    schema_version: Literal[1] = 1
    lineage_version: Literal[1] = 1
    source_execution_id: str = Field(min_length=1)
    source_execution_digest: str = Field(min_length=1)
    source_training_plan_id: str = Field(min_length=1)
    source_plan_digest: str = Field(min_length=1)
    training_request_id: str = Field(min_length=1)
    training_spec_id: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    model_spec_id: str = Field(min_length=1)
    tokenizer_spec_id: str = Field(min_length=1)
    trainer_implementation_id: str = Field(min_length=1)
    trainer_capability_id: str = Field(min_length=1)
    execution_policy_id: str = Field(min_length=1)
    retry_number: int = Field(ge=0)
    parent_checkpoint_id: None = None
    lineage_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointLineage:
        if self.lineage_id != derive_lineage_id(self):
            raise ValueError("lineage_id does not match the lineage content")
        return self


def derive_lineage_id(lineage: CheckpointLineage) -> str:
    payload = lineage.model_dump(mode="json")
    payload.pop("lineage_id", None)
    return "ckptlin-" + sha256_canonical(payload)[:16]


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------


class CheckpointCompatibility(StrictModel):
    """What (if anything) may consume this checkpoint. In Gate 10D: nothing
    real. ``simulated_only`` and ``loadable_as_real_model`` are Literal-locked
    so a fake checkpoint structurally CANNOT claim real loadability, and the
    supported real inference backend list is locked empty."""

    schema_version: Literal[1] = 1
    format_spec_id: str = Field(min_length=1)
    model_spec_id: str = Field(min_length=1)
    tokenizer_spec_id: str = Field(min_length=1)
    architecture_id: str = Field(min_length=1)
    predictor_adapter_version: Literal["metadata-only-v0"] = "metadata-only-v0"
    supported_inference_backends: tuple[str, ...] = Field(
        default_factory=tuple, max_length=0)
    simulated_only: Literal[True] = True
    loadable_as_real_model: Literal[False] = False
    compatibility_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointCompatibility:
        if self.compatibility_id != derive_compatibility_id(self):
            raise ValueError(
                "compatibility_id does not match the compatibility content")
        return self


def derive_compatibility_id(compat: CheckpointCompatibility) -> str:
    payload = compat.model_dump(mode="json")
    payload.pop("compatibility_id", None)
    return "ckptcompat-" + sha256_canonical(payload)[:16]


# ---------------------------------------------------------------------------
# Logical checkpoint identity
# ---------------------------------------------------------------------------


def derive_checkpoint_id(
    *,
    format_spec_id: str,
    lineage_id: str,
    declared_file_roles: tuple[CheckpointFileRole, ...],
    simulated: bool,
    model_spec_id: str,
    tokenizer_spec_id: str,
    checkpoint_version: int,
) -> str:
    """LOGICAL checkpoint identity — never depends on paths or payload bytes."""
    payload = {
        "format_spec_id": format_spec_id,
        "lineage_id": lineage_id,
        "declared_file_roles": sorted(r.value for r in declared_file_roles),
        "simulated": simulated,
        "model_spec_id": model_spec_id,
        "tokenizer_spec_id": tokenizer_spec_id,
        "checkpoint_version": checkpoint_version,
    }
    return "checkpoint-" + sha256_canonical(payload)[:24]


# ---------------------------------------------------------------------------
# Untrusted candidate
# ---------------------------------------------------------------------------


class CandidateFile(StrictModel):
    """One UNTRUSTED candidate file: raw content, role, serialization.

    Deliberately carries no hash — the writer recomputes hashes and sizes from
    the content itself, so candidate-supplied integrity claims cannot exist.
    """

    relative_path: str = Field(min_length=1)
    role: CheckpointFileRole
    serialization_id: str = Field(min_length=1)
    required: bool
    content: bytes

    @model_validator(mode="after")
    def _valid(self) -> CandidateFile:
        validate_checkpoint_relative_path(self.relative_path)
        return self


class CheckpointCandidate(StrictModel):
    """Files produced by a backend but NOT yet trusted.

    A candidate is not a checkpoint. It becomes one only by passing through
    ``write_checkpoint`` (which recomputes every hash and re-verifies the
    persisted artifact). The candidate validates its own internal coherence —
    intended id, unique safe paths, unique roles, format conformance — but
    coherence is not trust.
    """

    schema_version: Literal[1] = 1
    checkpoint_version: Literal[1] = 1
    simulated: Literal[True] = True
    producer_id: Literal["fake-checkpoint-producer-v1"] = (
        "fake-checkpoint-producer-v1")
    intended_checkpoint_id: str = Field(min_length=1)
    lineage: CheckpointLineage
    format_spec: CheckpointFormatSpec
    production_policy: CheckpointProductionPolicy
    compatibility: CheckpointCompatibility
    files: tuple[CandidateFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointCandidate:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("candidate files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate candidate file paths")
        roles = [f.role for f in self.files]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate candidate file roles")
        if set(roles) != set(self.format_spec.expected_file_roles):
            raise ValueError(
                "candidate roles do not match the format spec's expected roles")
        if not all(f.required for f in self.files):
            raise ValueError(
                "every Gate 10D checkpoint file is required by the format")
        if self.compatibility.format_spec_id != self.format_spec.format_spec_id:
            raise ValueError("compatibility binds a different format spec")
        if self.compatibility.model_spec_id != self.lineage.model_spec_id:
            raise ValueError("compatibility and lineage disagree on model spec")
        if (self.compatibility.tokenizer_spec_id
                != self.lineage.tokenizer_spec_id):
            raise ValueError(
                "compatibility and lineage disagree on tokenizer spec")
        if self.format_spec.artifact_kind not in (
                self.production_policy.permitted_artifact_kinds):
            raise ValueError("artifact kind not permitted by the policy")
        payload = next(f for f in self.files
                       if f.role is CheckpointFileRole.FAKE_MODEL_PAYLOAD)
        if not payload.content.startswith(FAKE_PAYLOAD_MAGIC):
            raise ValueError(
                "fake model payload must start with the fake-checkpoint magic")
        expected = derive_checkpoint_id(
            format_spec_id=self.format_spec.format_spec_id,
            lineage_id=self.lineage.lineage_id,
            declared_file_roles=tuple(roles),
            simulated=self.simulated,
            model_spec_id=self.lineage.model_spec_id,
            tokenizer_spec_id=self.lineage.tokenizer_spec_id,
            checkpoint_version=self.checkpoint_version)
        if self.intended_checkpoint_id != expected:
            raise ValueError(
                "intended_checkpoint_id does not match the candidate content")
        return self
