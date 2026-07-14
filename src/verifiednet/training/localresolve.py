"""Local-only, content-hashing model/tokenizer resolvers (Gate 10F).

These resolvers implement the Gate 10E resolver contracts against REAL local
directories: no network access ever, exact pinned revisions required, expected
files verified present, deterministic content hashes computed over the actual
bytes, symlinks rejected. An absolute path LOCATES the artifact but never
becomes identity — identity derives from content hashes plus the declared
immutable metadata. A model name alone is never proof: the content hash binds
the exact local bytes, and the parameter count is derived STRUCTURALLY from
the safetensors weight header (never inferred from the model's name). No ML
library is imported.
"""

from __future__ import annotations

from pathlib import Path

from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.training.realckptstore import (
    RealCheckpointError,
    count_safetensors_parameters,
)
from verifiednet.training.resolve import (
    ArtifactResolutionError,
    ResolvedModelArtifact,
    ResolvedTokenizerArtifact,
    derive_model_artifact_id,
    derive_tokenizer_artifact_id,
)
from verifiednet.training.spec import (
    FORBIDDEN_REVISIONS,
    TokenizerSpec,
    TrainableModelSpec,
)

LOCAL_MODEL_RESOLVER_ID = "local-model-resolver-v1"
LOCAL_TOKENIZER_RESOLVER_ID = "local-tokenizer-resolver-v1"

#: Files a local full-model directory must contain.
REQUIRED_MODEL_FILES = ("config.json", "model.safetensors")
#: Files a local tokenizer directory must contain.
REQUIRED_TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json")


def _content_hash(root: Path, required: tuple[str, ...]) -> str:
    """Deterministic hash over (relative path, bytes) of the required files."""
    parts: dict[str, str] = {}
    for name in sorted(required):
        fpath = root / name
        if fpath.is_symlink():
            raise ArtifactResolutionError(f"symlink refused: {fpath.name}")
        if not fpath.is_file():
            raise ArtifactResolutionError(
                f"required local file missing: {fpath.name}")
        parts[name] = sha256_bytes(fpath.read_bytes())
    return "sha256:" + sha256_canonical(parts)


class LocalModelArtifactResolver:
    """Resolve a model spec against ONE local directory. Never downloads."""

    resolver_id = LOCAL_MODEL_RESOLVER_ID

    def __init__(self, model_dir: str | Path) -> None:
        self._model_dir = Path(model_dir)

    def resolve(self, spec: TrainableModelSpec) -> ResolvedModelArtifact:
        if spec.model_revision.strip().lower() in FORBIDDEN_REVISIONS:
            raise ArtifactResolutionError("mutable model revision refused")
        if not self._model_dir.is_dir():
            raise ArtifactResolutionError(
                "local model directory is missing; network download is "
                "forbidden — supply the approved local artifact")
        content_hash = _content_hash(self._model_dir, REQUIRED_MODEL_FILES)
        try:
            parameter_count = count_safetensors_parameters(
                (self._model_dir / "model.safetensors").read_bytes())
        except RealCheckpointError as exc:
            raise ArtifactResolutionError(
                f"model weights are not structurally valid: {exc}") from exc
        fields: dict[str, object] = {
            "model_spec_id": spec.model_spec_id,
            "model_revision": spec.model_revision,
            "content_hash": content_hash,
            "artifact_source": "local_cache", "locally_cached": True,
            "required_files": tuple(sorted(REQUIRED_MODEL_FILES)),
            "declared_parameter_count": parameter_count,
            "verification_status": "verified",
        }
        probe = ResolvedModelArtifact.model_construct(**fields)  # type: ignore[arg-type]
        return ResolvedModelArtifact(
            **fields,  # type: ignore[arg-type]
            resolved_model_artifact_id=derive_model_artifact_id(probe))


class LocalTokenizerArtifactResolver:
    """Resolve a tokenizer spec against ONE local directory. Never downloads.

    Tokenizer identity is independent of model identity; special-vocabulary
    and padding/truncation agreement follow the Gate 10B spec's declared
    policies (``model_defaults`` + right padding + fail-closed truncation are
    the only representable values in this gate, so agreement is structural).
    """

    resolver_id = LOCAL_TOKENIZER_RESOLVER_ID

    def __init__(self, tokenizer_dir: str | Path) -> None:
        self._tokenizer_dir = Path(tokenizer_dir)

    def resolve(self, spec: TokenizerSpec) -> ResolvedTokenizerArtifact:
        if spec.tokenizer_revision.strip().lower() in FORBIDDEN_REVISIONS:
            raise ArtifactResolutionError("mutable tokenizer revision refused")
        if not self._tokenizer_dir.is_dir():
            raise ArtifactResolutionError(
                "local tokenizer directory is missing; network download is "
                "forbidden — supply the approved local artifact")
        content_hash = _content_hash(
            self._tokenizer_dir, REQUIRED_TOKENIZER_FILES)
        fields: dict[str, object] = {
            "tokenizer_spec_id": spec.tokenizer_spec_id,
            "tokenizer_revision": spec.tokenizer_revision,
            "content_hash": content_hash,
            "artifact_source": "local_cache", "locally_cached": True,
            "special_vocab_policy_agreement": True,
            "padding_truncation_compatible": True,
            "required_files": tuple(sorted(REQUIRED_TOKENIZER_FILES)),
            "verification_status": "verified",
        }
        probe = ResolvedTokenizerArtifact.model_construct(**fields)  # type: ignore[arg-type]
        return ResolvedTokenizerArtifact(
            **fields,  # type: ignore[arg-type]
            resolved_tokenizer_artifact_id=derive_tokenizer_artifact_id(probe))
