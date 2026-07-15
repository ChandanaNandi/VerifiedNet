"""Verified base-model bundle + matched base-model predictor (Gate 12).

Gate 12 needs the scientifically useful comparison: same architecture, same
prompt, same decoding, same task, same corpus — DIFFERENT WEIGHTS ONLY. The
trained side is the Gate 11 verified checkpoint; this module provides the
matched BASE side: the approved original local snapshot
(``Qwen/Qwen2.5-0.5B-Instruct`` at its pinned immutable revision), verified
fail-closed from the on-disk files and wrapped in a bundle that satisfies the
same ``VerifiedInferenceBundle`` protocol — so the Gate 11 HF inference
backend, prompt template, parser, decoding, and device policy are reused
UNCHANGED. No second inference stack exists.

Like the checkpoint bundle: construction loads no model, trust comes only
from disk (required files, no symlinks, structural safetensors parse, content
hashes), identity is content-addressed (``basemodel-``), and eligibility is
re-verified at the moment of first load. Mutable revisions (``main``,
``latest``, …) are unrepresentable — only a full 40-hex snapshot commit is
accepted. This module never trains, never mutates the snapshot, and never
downloads anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.canonical import canonical_json_str
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.features import DatasetFeatures
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.baseline import BaselineSpec, derive_baseline_id
from verifiednet.evaluation.checkpointpred import (
    HF_LOCAL_BACKEND_FAMILY,
    CheckpointInferenceCompatibility,
    CheckpointInferenceDevicePolicy,
    CheckpointPredictionError,
)
from verifiednet.evaluation.contract import EvaluationTask, NormalizationPolicy
from verifiednet.evaluation.inference import (
    BackendUnavailableError,
    DecodingConfig,
    InferenceBackend,
    InferenceError,
    InferenceTimeoutError,
)
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    InvalidPrediction,
)
from verifiednet.evaluation.prompt import PromptTemplate
from verifiednet.evaluation.slm import (
    build_backend_invalid_prediction,
    parse_backend_response,
)
from verifiednet.schemas.base import StrictModel
from verifiednet.training.realckptstore import parse_safetensors_header

BASE_MODEL_PREDICTOR_VERSION = 1
#: Exactly the files the Gate 11 inference backend consumes; nothing else is
#: hashed into the base-model identity, and nothing else may be relied on.
BASE_MODEL_REQUIRED_FILES: tuple[str, ...] = (
    "config.json", "model.safetensors", "tokenizer.json")
_IMMUTABLE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


class BaseModelVerificationResult(StrictModel):
    """Structured, fail-closed verdict on a local base-model snapshot dir."""

    schema_version: Literal[1] = 1
    verified: bool
    content_hashes: dict[str, str] = Field(default_factory=dict)
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def verify_base_model_dir(
    model_dir: str | Path,
    *,
    architecture_class: str,
) -> BaseModelVerificationResult:
    """Verify the snapshot from disk only: files, symlinks, structure, hashes.

    Never loads a model, never follows symlinks, never trusts caller-supplied
    metadata. The declared architecture must be exactly the single entry in
    the snapshot's own ``config.json``.
    """
    root = Path(model_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("base_model_dir_present", False, str(root)))
        return BaseModelVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("base_model_dir_present", True))

    hashes: dict[str, str] = {}
    contents: dict[str, bytes] = {}
    files_ok = True
    for name in BASE_MODEL_REQUIRED_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            checks.append(_c("required_file_regular", False, name))
            files_ok = False
            continue
        raw = path.read_bytes()
        contents[name] = raw
        hashes[name] = sha256_bytes(raw)
    checks.append(_c("required_files_present", files_ok))
    if not files_ok:
        return BaseModelVerificationResult(verified=False, checks=tuple(checks))

    weights_ok = True
    try:
        parse_safetensors_header(contents["model.safetensors"])
    except Exception as exc:  # RealCheckpointError — structural refusal
        weights_ok = False
        checks.append(_c("safetensors_structurally_valid", False, str(exc)))
    if weights_ok:
        checks.append(_c("safetensors_structurally_valid", True))

    config_ok, architectures = True, None
    try:
        config = json.loads(contents["config.json"].decode("utf-8"))
        architectures = config.get("architectures")
    except (UnicodeDecodeError, ValueError):
        config_ok = False
    checks.append(_c("config_parses", config_ok))
    checks.append(_c(
        "architecture_matches_config",
        isinstance(architectures, list) and architectures == [architecture_class],
        repr(architectures)))

    tokenizer_ok = True
    try:
        json.loads(contents["tokenizer.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        tokenizer_ok = False
    checks.append(_c("tokenizer_snapshot_parses", tokenizer_ok))

    return BaseModelVerificationResult(
        verified=all(c.passed for c in checks), content_hashes=hashes,
        checks=tuple(checks))


def derive_base_model_id(
    *,
    model_identifier: str,
    model_revision: str,
    architecture_class: str,
    content_hashes: dict[str, str],
) -> str:
    payload = {
        "model_identifier": model_identifier,
        "model_revision": model_revision,
        "architecture_class": architecture_class,
        "content_hashes": {k: content_hashes[k] for k in sorted(content_hashes)},
    }
    return "basemodel-" + sha256_canonical(payload)[:16]


@dataclass(frozen=True)
class VerifiedBaseModelBundle:
    """Verified descriptors + payload paths for the approved base snapshot.

    Satisfies ``VerifiedInferenceBundle``: the Gate 11 backend consumes it
    exactly like a verified checkpoint bundle. Construction loads no model.
    """

    root: Path
    model_identifier: str
    model_revision: str
    architecture_class: str
    content_hashes: dict[str, str]
    base_model_id: str
    inference_compatibility: CheckpointInferenceCompatibility
    weights_path: Path
    config_path: Path
    tokenizer_path: Path

    def fingerprint(self) -> dict[str, str]:
        """Fresh sha256 of every required file (immutability proofs)."""
        return {name: sha256_bytes((self.root / name).read_bytes())
                for name in BASE_MODEL_REQUIRED_FILES}

    def reverify(self) -> BaseModelVerificationResult:
        """Re-verify at the moment of use; refuse ANY byte drift since load."""
        result = verify_base_model_dir(
            self.root, architecture_class=self.architecture_class)
        if not result.verified:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
            raise CheckpointPredictionError(
                f"base model snapshot is no longer verified: {detail}")
        if result.content_hashes != self.content_hashes:
            raise CheckpointPredictionError(
                "base model snapshot bytes changed since verification")
        return result


def load_verified_base_model_bundle(
    model_dir: str | Path,
    *,
    model_identifier: str,
    model_revision: str,
    architecture_class: str = "Qwen2ForCausalLM",
    compatibility: CheckpointInferenceCompatibility,
) -> VerifiedBaseModelBundle:
    """Verify the snapshot and bind its content-addressed identity; fail-closed."""
    if not model_identifier:
        raise CheckpointPredictionError("model_identifier must be non-empty")
    if not _IMMUTABLE_REVISION_RE.match(model_revision):
        raise CheckpointPredictionError(
            "model_revision must be a full 40-hex immutable snapshot commit "
            "(mutable aliases like 'main' are unrepresentable)")
    if architecture_class not in compatibility.supported_architectures:
        raise CheckpointPredictionError(
            f"architecture {architecture_class!r} is outside the inference "
            f"compatibility scope {compatibility.supported_architectures!r}")
    root = Path(model_dir)
    result = verify_base_model_dir(root, architecture_class=architecture_class)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise CheckpointPredictionError(
            f"base model snapshot failed verification: {detail}")
    return VerifiedBaseModelBundle(
        root=root, model_identifier=model_identifier,
        model_revision=model_revision, architecture_class=architecture_class,
        content_hashes=dict(result.content_hashes),
        base_model_id=derive_base_model_id(
            model_identifier=model_identifier, model_revision=model_revision,
            architecture_class=architecture_class,
            content_hashes=result.content_hashes),
        inference_compatibility=compatibility,
        weights_path=root / "model.safetensors",
        config_path=root / "config.json",
        tokenizer_path=root / "tokenizer.json")


# ---------------------------------------------------------------------------
# Matched base-model predictor spec + predictor
# ---------------------------------------------------------------------------


def derive_base_model_predictor_id(
    *,
    schema_version: int,
    predictor_version: int,
    base_model_id: str,
    model_identifier: str,
    model_revision: str,
    architecture_class: str,
    weights_sha256: str,
    config_sha256: str,
    tokenizer_sha256: str,
    compatibility_id: str,
    prompt_template_id: str,
    decoding_config: dict[str, object],
    normalization_policy_id: str,
    backend_family: str,
    inference_precision: str,
    device_policy: str,
) -> str:
    """Pure content identity: any weight or config byte change changes it."""
    payload = {
        "schema_version": schema_version,
        "predictor_version": predictor_version,
        "base_model_id": base_model_id,
        "model_identifier": model_identifier,
        "model_revision": model_revision,
        "architecture_class": architecture_class,
        "weights_sha256": weights_sha256,
        "config_sha256": config_sha256,
        "tokenizer_sha256": tokenizer_sha256,
        "compatibility_id": compatibility_id,
        "prompt_template_id": prompt_template_id,
        "decoding_config": decoding_config,
        "normalization_policy_id": normalization_policy_id,
        "backend_family": backend_family,
        "inference_precision": inference_precision,
        "device_policy": device_policy,
    }
    return "basepred-" + sha256_canonical(payload)[:24]


class BaseModelPredictorSpec(StrictModel):
    """Frozen, content-addressed spec of the MATCHED base-model predictor.

    Binds the exact snapshot bytes (per-file hashes), the pinned immutable
    revision, and the exact Gate 8/11 prompt/decoding/normalization/backend/
    precision/device configuration — the same fields the checkpoint-predictor
    spec binds, so fairness checks can compare them one-to-one. No path, host,
    time, or label participates.
    """

    schema_version: Literal[1] = 1
    predictor_version: Literal[1] = 1
    base_model_id: str = Field(min_length=1)
    model_identifier: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    architecture_class: str = Field(min_length=1)
    weights_sha256: str = Field(min_length=64, max_length=64)
    config_sha256: str = Field(min_length=64, max_length=64)
    tokenizer_sha256: str = Field(min_length=64, max_length=64)
    compatibility_id: str = Field(min_length=1)
    prompt_template_id: str = Field(min_length=1)
    decoding: DecodingConfig
    normalization_policy_id: str = Field(min_length=1)
    backend_family: str = Field(min_length=1)
    inference_precision: Literal["float32"] = "float32"
    device_policy_id: str = Field(min_length=1)
    predictor_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> BaseModelPredictorSpec:
        if not _IMMUTABLE_REVISION_RE.match(self.model_revision):
            raise ValueError("model_revision must be an immutable 40-hex commit")
        expected = derive_base_model_predictor_id(
            schema_version=self.schema_version,
            predictor_version=self.predictor_version,
            base_model_id=self.base_model_id,
            model_identifier=self.model_identifier,
            model_revision=self.model_revision,
            architecture_class=self.architecture_class,
            weights_sha256=self.weights_sha256,
            config_sha256=self.config_sha256,
            tokenizer_sha256=self.tokenizer_sha256,
            compatibility_id=self.compatibility_id,
            prompt_template_id=self.prompt_template_id,
            decoding_config=self.decoding.model_dump(mode="json"),
            normalization_policy_id=self.normalization_policy_id,
            backend_family=self.backend_family,
            inference_precision=self.inference_precision,
            device_policy=self.device_policy_id,
        )
        if self.predictor_id != expected:
            raise ValueError(
                "predictor_id does not match the predictor configuration")
        return self


class VerifiedBaseModelPredictor:
    """The matched base-model predictor on the Gate 7 baseline boundary.

    Identical pipeline to the Gate 11 checkpoint predictor — same prompt
    template, same shared Gate 8 parser, same prediction union and id
    algorithm, same failure semantics (backend failure is an explicit
    ``InvalidPrediction``, never an abstention) — differing ONLY in where the
    verified weights come from. That is the entire point: base versus trained
    with weights as the only variable.
    """

    def __init__(
        self,
        *,
        task: EvaluationTask,
        bundle: VerifiedBaseModelBundle,
        backend: InferenceBackend,
        prompt_template: PromptTemplate,
        device_policy: CheckpointInferenceDevicePolicy,
        backend_family: str = HF_LOCAL_BACKEND_FAMILY,
        decoding: DecodingConfig | None = None,
        normalization: NormalizationPolicy | None = None,
        predictor_name: str = "verified_base_model_predictor",
    ) -> None:
        self._task_id = task.task_id
        self._backend = backend
        self._template = prompt_template
        self._decoding = decoding or DecodingConfig()
        if self._decoding.temperature != 0.0:
            raise CheckpointPredictionError(
                "matched base-model prediction requires greedy decoding "
                "(temperature 0)")
        self._norm = normalization or NormalizationPolicy()
        self._candidates = frozenset(
            self._norm.normalize(f) for f in prompt_template.candidate_families
        )
        predictor_id = derive_base_model_predictor_id(
            schema_version=1,
            predictor_version=BASE_MODEL_PREDICTOR_VERSION,
            base_model_id=bundle.base_model_id,
            model_identifier=bundle.model_identifier,
            model_revision=bundle.model_revision,
            architecture_class=bundle.architecture_class,
            weights_sha256=bundle.content_hashes["model.safetensors"],
            config_sha256=bundle.content_hashes["config.json"],
            tokenizer_sha256=bundle.content_hashes["tokenizer.json"],
            compatibility_id=bundle.inference_compatibility.compatibility_id,
            prompt_template_id=prompt_template.prompt_template_id,
            decoding_config=self._decoding.model_dump(mode="json"),
            normalization_policy_id=self._norm.policy_id,
            backend_family=backend_family,
            inference_precision=device_policy.inference_precision,
            device_policy=device_policy.device_policy_id,
        )
        self._predictor_spec = BaseModelPredictorSpec(
            base_model_id=bundle.base_model_id,
            model_identifier=bundle.model_identifier,
            model_revision=bundle.model_revision,
            architecture_class=bundle.architecture_class,
            weights_sha256=bundle.content_hashes["model.safetensors"],
            config_sha256=bundle.content_hashes["config.json"],
            tokenizer_sha256=bundle.content_hashes["tokenizer.json"],
            compatibility_id=bundle.inference_compatibility.compatibility_id,
            prompt_template_id=prompt_template.prompt_template_id,
            decoding=self._decoding,
            normalization_policy_id=self._norm.policy_id,
            backend_family=backend_family,
            device_policy_id=device_policy.device_policy_id,
            predictor_id=predictor_id,
        )
        cfg = {
            "base_model_predictor_id": predictor_id,
            "base_model_predictor_spec": canonical_json_str(
                self._predictor_spec),
        }
        self._spec = BaselineSpec(
            baseline_name=predictor_name,
            rule_set_version=BASE_MODEL_PREDICTOR_VERSION,
            task_id=task.task_id, rule_configuration=cfg,
            baseline_id=derive_baseline_id(
                schema_version=1, baseline_name=predictor_name,
                baseline_version=1,
                rule_set_version=BASE_MODEL_PREDICTOR_VERSION,
                task_id=task.task_id, rule_configuration=cfg),
        )

    @property
    def spec(self) -> BaselineSpec:
        return self._spec

    @property
    def predictor_spec(self) -> BaseModelPredictorSpec:
        return self._predictor_spec

    def predict(
        self, features: DatasetFeatures
    ) -> DiagnosisPrediction | AbstentionPrediction | InvalidPrediction:
        prompt = self._template.render(features)
        payload = features.model_dump(mode="json")
        try:
            response = self._backend.generate(prompt, decoding=self._decoding)
        except BackendUnavailableError as exc:
            return self._invalid(payload, "backend_unavailable", str(exc))
        except InferenceTimeoutError as exc:
            return self._invalid(payload, "inference_timeout", str(exc))
        except InferenceError as exc:
            return self._invalid(payload, "backend_error", str(exc))
        return parse_backend_response(
            response.text, baseline_id=self._spec.baseline_id,
            task_id=self._task_id, features_payload=payload,
            normalization=self._norm, normalized_candidates=self._candidates,
        )

    def _invalid(
        self, payload: dict[str, object], reason: str, raw: str
    ) -> InvalidPrediction:
        return build_backend_invalid_prediction(
            baseline_id=self._spec.baseline_id, task_id=self._task_id,
            features_payload=payload, reason_code=reason, raw_excerpt=raw,
        )
