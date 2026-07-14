"""Verified checkpoint-backed predictor (Gate 11).

The FIRST predictor whose behavior comes from real trained weights — and the
weights may enter prediction ONLY through a VERIFIED immutable real checkpoint
(the Gate 10F ``verifiednet.real-checkpoint-v1`` format). There is no path,
cache, or "just load this directory" shortcut: eligibility is assessed from the
on-disk artifact alone (never a caller-supplied manifest), fail-closed, before
any model bytes are interpreted.

The predictor sits behind the EXACT Gate 7/8 feature-only boundary
(``predict(features: DatasetFeatures) -> DatasetPrediction``) and REUSES the
Gate 8 prompt template, response parser, normalization policy, prediction
union, and prediction-id algorithm — it introduces no new prompt and no new
parsing rules. Inference is READ-ONLY: no training API, no weight mutation, no
network, single process, CPU, float32 (recorded explicitly; MPS/CUDA are not
modeled by any contract and there is no silent fallback).

This module is import-pure (no ML library). The one sanctioned lazy-ML
inference site is ``verifiednet.evaluation.hfinference`` (mirror of the
training-side ``hfexecutor`` precedent). This module is also the ONE sanctioned
consumer of ``verifiednet.training.realckptstore`` — evaluation consumes
verified training ARTIFACTS (checkpoints); training still never imports
evaluation (ADR-0022 is unchanged). See ADR-0028.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.canonical import canonical_json_str
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.features import DatasetFeatures
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.baseline import BaselineSpec, derive_baseline_id
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
from verifiednet.training.realckptstore import (
    REAL_CHECKPOINT_MANIFEST_FILE,
    RealCheckpointFileRole,
    RealCheckpointManifest,
    verify_real_checkpoint,
)

CHECKPOINT_PREDICTOR_VERSION = 1
#: The only supported inference backend family in Gate 11: local Hugging Face
#: Transformers over a verified checkpoint payload. No remote code, no network.
HF_LOCAL_BACKEND_FAMILY = "hf-transformers-local"


class CheckpointPredictionError(VerifiedNetError):
    """A checkpoint-backed predictor could not be constructed (fail-closed)."""


# ---------------------------------------------------------------------------
# Device policy + inference compatibility (both frozen, content-addressed)
# ---------------------------------------------------------------------------


class CheckpointInferenceDevicePolicy(StrictModel):
    """CPU-only, float32, single-process inference. Literal-locked.

    CPU is the SAFEST honest device: MPS/CUDA behavior is not modeled by any
    VerifiedNet contract, and this policy forbids silent fallback — a device
    change requires a new policy (and therefore a new predictor id).
    """

    schema_version: Literal[1] = 1
    device_kind: Literal["cpu"] = "cpu"
    inference_precision: Literal["float32"] = "float32"
    single_process: Literal[True] = True
    allow_silent_fallback: Literal[False] = False
    device_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointInferenceDevicePolicy:
        if self.device_policy_id != derive_inference_device_policy_id(self):
            raise ValueError("device_policy_id does not match the policy content")
        return self


def derive_inference_device_policy_id(
    policy: CheckpointInferenceDevicePolicy,
) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("device_policy_id", None)
    return "infdev-" + sha256_canonical(payload)[:16]


def build_cpu_inference_device_policy() -> CheckpointInferenceDevicePolicy:
    probe = CheckpointInferenceDevicePolicy.model_construct()
    return CheckpointInferenceDevicePolicy(
        device_policy_id=derive_inference_device_policy_id(probe))


class CheckpointInferenceCompatibility(StrictModel):
    """The NARROW inference scope a checkpoint must fit (Gate 11).

    Deliberately small: one architecture family, tokenizer from the verified
    checkpoint payload only, local-files-only Transformers, single process and
    device, no quantization, no adapters, no remote code, no network. Distinct
    from the checkpoint manifest's own ``RealCheckpointCompatibility`` (Gate
    10F), which stays byte-identical — Gate 11 never rewrites checkpoints.
    """

    schema_version: Literal[1] = 1
    backend_family: Literal["hf-transformers-local"] = "hf-transformers-local"
    supported_architectures: tuple[str, ...] = Field(min_length=1)
    tokenizer_source: Literal["checkpoint_payload_only"] = (
        "checkpoint_payload_only")
    local_files_only: Literal[True] = True
    trust_remote_code: Literal[False] = False
    quantization: Literal["none"] = "none"
    adapters: Literal["none"] = "none"
    network_access: Literal[False] = False
    single_process: Literal[True] = True
    single_device: Literal[True] = True
    compatibility_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointInferenceCompatibility:
        arches = list(self.supported_architectures)
        if arches != sorted(arches) or len(arches) != len(set(arches)):
            raise ValueError("supported_architectures must be sorted and unique")
        if self.compatibility_id != derive_inference_compatibility_id(self):
            raise ValueError("compatibility_id does not match the content")
        return self


def derive_inference_compatibility_id(
    compat: CheckpointInferenceCompatibility,
) -> str:
    payload = compat.model_dump(mode="json")
    payload.pop("compatibility_id", None)
    return "infcompat-" + sha256_canonical(payload)[:16]


def build_checkpoint_inference_compatibility(
    *,
    supported_architectures: tuple[str, ...] = ("Qwen2ForCausalLM",),
) -> CheckpointInferenceCompatibility:
    arches = tuple(sorted(set(supported_architectures)))
    probe = CheckpointInferenceCompatibility.model_construct(
        supported_architectures=arches)
    return CheckpointInferenceCompatibility(
        supported_architectures=arches,
        compatibility_id=derive_inference_compatibility_id(probe))


# ---------------------------------------------------------------------------
# Eligibility (structured, fail-closed, from the on-disk artifact ONLY)
# ---------------------------------------------------------------------------


class CheckpointEligibilityResult(StrictModel):
    """The structured verdict on whether a checkpoint may back a predictor."""

    schema_version: Literal[1] = 1
    eligible: bool
    checkpoint_id: str | None = None
    checkpoint_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def assess_checkpoint_prediction_eligibility(
    checkpoint_dir: str | Path,
    compatibility: CheckpointInferenceCompatibility,
) -> CheckpointEligibilityResult:
    """Decide, fail-closed, whether ``checkpoint_dir`` may back a predictor.

    Trust comes ONLY from the on-disk artifact: the full Gate 10F structural
    verification runs first (directory shape, manifest self-validation, file
    hashes, safetensors structure, no symlinks, no undeclared files, no
    optimizer/scheduler/RNG/resume state). A caller-supplied manifest is never
    accepted — there is no parameter for one. A Gate 10D SIMULATED checkpoint
    fails here (its manifest cannot validate as a real-checkpoint manifest),
    as does any corrupt, incomplete, or foreign directory.
    """
    root = Path(checkpoint_dir)
    verification = verify_real_checkpoint(root)
    checks: list[DatasetCheck] = list(verification.checks)
    if not verification.verified:
        return CheckpointEligibilityResult(
            eligible=False, checkpoint_digest=verification.checkpoint_digest,
            checks=tuple(checks))

    manifest = RealCheckpointManifest.model_validate_json(
        (root / REAL_CHECKPOINT_MANIFEST_FILE).read_bytes())
    checks.append(_c(
        "genuine_real_payload_format",
        manifest.format_spec.payload_format == "verifiednet.real-checkpoint-v1"))
    checks.append(_c("not_simulated", manifest.simulated is False))
    checks.append(_c(
        "loadable_as_real_model",
        manifest.compatibility.loadable_as_real_model is True))
    checks.append(_c(
        "never_evaluated_or_benchmarked",
        manifest.compatibility.evaluated is False
        and manifest.compatibility.benchmarked is False))
    checks.append(_c(
        "architecture_supported",
        manifest.compatibility.architecture_id
        in compatibility.supported_architectures,
        manifest.compatibility.architecture_id))
    checks.append(_c(
        "completed_execution_recorded",
        bool(manifest.lineage.real_execution_id)
        and manifest.lineage.completed_optimizer_steps >= 1))

    return CheckpointEligibilityResult(
        eligible=all(c.passed for c in checks),
        checkpoint_id=manifest.checkpoint_id,
        checkpoint_digest=manifest.checkpoint_digest, checks=tuple(checks))


# ---------------------------------------------------------------------------
# Verified checkpoint bundle (verified descriptors only; NO model loading)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedCheckpointBundle:
    """Verified descriptors + payload paths for one eligible checkpoint.

    Constructing a bundle NEVER loads a model, imports an ML library, or reads
    weight bytes into memory beyond hashing. It exists so the inference backend
    receives only already-verified, role-resolved paths — never a raw
    directory. Build it with ``load_verified_checkpoint_bundle`` (fail-closed).
    """

    root: Path
    manifest: RealCheckpointManifest
    inference_compatibility: CheckpointInferenceCompatibility
    eligibility: CheckpointEligibilityResult
    weights_path: Path
    config_path: Path
    tokenizer_path: Path

    def fingerprint(self) -> dict[str, str]:
        """Fresh sha256 of every checkpoint file (immutability proofs)."""
        out: dict[str, str] = {
            REAL_CHECKPOINT_MANIFEST_FILE: sha256_bytes(
                (self.root / REAL_CHECKPOINT_MANIFEST_FILE).read_bytes())}
        for entry in self.manifest.files:
            out[entry.relative_path] = sha256_bytes(
                (self.root / entry.relative_path).read_bytes())
        return out

    def reverify(self) -> CheckpointEligibilityResult:
        """Re-assess eligibility at the moment of use; raise on any failure.

        Mirrors the Gate 10E rule that environmental trust is revalidated when
        it is USED, not only when it was created — a checkpoint mutated after
        bundle construction is refused before any weight byte is interpreted.
        """
        result = assess_checkpoint_prediction_eligibility(
            self.root, self.inference_compatibility)
        if not result.eligible:
            detail = "; ".join(
                f"{c.rule}: {c.detail}" for c in result.failures)
            raise CheckpointPredictionError(
                f"checkpoint is no longer eligible: {detail}")
        return result


def load_verified_checkpoint_bundle(
    checkpoint_dir: str | Path,
    *,
    compatibility: CheckpointInferenceCompatibility,
) -> VerifiedCheckpointBundle:
    """Assess eligibility and bind the verified payload paths; fail-closed."""
    root = Path(checkpoint_dir)
    eligibility = assess_checkpoint_prediction_eligibility(root, compatibility)
    if not eligibility.eligible:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in eligibility.failures)
        raise CheckpointPredictionError(
            f"checkpoint is not eligible for prediction: {detail}")
    manifest = RealCheckpointManifest.model_validate_json(
        (root / REAL_CHECKPOINT_MANIFEST_FILE).read_bytes())
    by_role = {entry.role: root / entry.relative_path
               for entry in manifest.files}
    return VerifiedCheckpointBundle(
        root=root, manifest=manifest, inference_compatibility=compatibility,
        eligibility=eligibility,
        weights_path=by_role[RealCheckpointFileRole.MODEL_WEIGHTS],
        config_path=by_role[RealCheckpointFileRole.MODEL_CONFIG],
        tokenizer_path=by_role[RealCheckpointFileRole.TOKENIZER_SNAPSHOT])


# ---------------------------------------------------------------------------
# Checkpoint predictor spec (frozen, content-addressed)
# ---------------------------------------------------------------------------


def derive_checkpoint_predictor_id(
    *,
    schema_version: int,
    predictor_version: int,
    checkpoint_id: str,
    checkpoint_digest: str,
    checkpoint_format_id: str,
    compatibility_id: str,
    model_spec_id: str,
    tokenizer_spec_id: str,
    prompt_template_id: str,
    decoding_config: dict[str, object],
    normalization_policy_id: str,
    backend_family: str,
    inference_precision: str,
    device_policy: str,
) -> str:
    """The Gate 11 predictor identity: pure content, never path/host/time."""
    payload = {
        "schema_version": schema_version,
        "predictor_version": predictor_version,
        "checkpoint_id": checkpoint_id,
        "checkpoint_digest": checkpoint_digest,
        "checkpoint_format_id": checkpoint_format_id,
        "compatibility_id": compatibility_id,
        "model_spec_id": model_spec_id,
        "tokenizer_spec_id": tokenizer_spec_id,
        "prompt_template_id": prompt_template_id,
        "decoding_config": decoding_config,
        "normalization_policy_id": normalization_policy_id,
        "backend_family": backend_family,
        "inference_precision": inference_precision,
        "device_policy": device_policy,
    }
    return "ckptpred-" + sha256_canonical(payload)[:24]


class CheckpointPredictorSpec(StrictModel):
    """A frozen, content-addressed checkpoint-predictor specification.

    Binds the exact verified checkpoint (id + digest + format + specs), the
    exact Gate 8 prompt/decoding/normalization, and the exact backend family,
    precision, and device policy. It carries NO absolute path, hostname,
    timestamp, or label — changing any prediction-affecting input changes
    ``predictor_id``.
    """

    schema_version: Literal[1] = 1
    predictor_version: Literal[1] = 1
    checkpoint_id: str = Field(min_length=1)
    checkpoint_digest: str = Field(min_length=1)
    checkpoint_format_id: str = Field(min_length=1)
    compatibility_id: str = Field(min_length=1)
    model_spec_id: str = Field(min_length=1)
    tokenizer_spec_id: str = Field(min_length=1)
    prompt_template_id: str = Field(min_length=1)
    decoding: DecodingConfig
    normalization_policy_id: str = Field(min_length=1)
    backend_family: str = Field(min_length=1)
    inference_precision: Literal["float32"] = "float32"
    device_policy_id: str = Field(min_length=1)
    predictor_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointPredictorSpec:
        if not self.checkpoint_id.startswith("realckpt-"):
            raise ValueError("checkpoint_id must reference a REAL checkpoint")
        if not self.checkpoint_digest.startswith("realdig-"):
            raise ValueError("checkpoint_digest must be a real-checkpoint digest")
        expected = derive_checkpoint_predictor_id(
            schema_version=self.schema_version,
            predictor_version=self.predictor_version,
            checkpoint_id=self.checkpoint_id,
            checkpoint_digest=self.checkpoint_digest,
            checkpoint_format_id=self.checkpoint_format_id,
            compatibility_id=self.compatibility_id,
            model_spec_id=self.model_spec_id,
            tokenizer_spec_id=self.tokenizer_spec_id,
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


# ---------------------------------------------------------------------------
# The predictor (Gate 7/8 feature-only boundary, Gate 8 pipeline reuse)
# ---------------------------------------------------------------------------


class VerifiedCheckpointPredictor:
    """A checkpoint-backed predictor on the Gate 7 baseline boundary.

    Identical shape to every other predictor: ``predict(features:
    DatasetFeatures) -> DatasetPrediction``. It renders the SAME Gate 8 prompt
    template, calls an ``InferenceBackend`` (the verified-checkpoint HF backend
    in production; a fake in tests), and parses through the SAME Gate 8
    pipeline. Backend failures become explicit ``InvalidPrediction`` — never an
    abstention, never an escaping exception. The Gate-7 ``BaselineSpec`` embeds
    the full ``CheckpointPredictorSpec`` so evaluation manifests persist it
    with no structural change (ready for Gate 9 comparison, NOT run here).
    """

    def __init__(
        self,
        *,
        task: EvaluationTask,
        bundle: VerifiedCheckpointBundle,
        backend: InferenceBackend,
        prompt_template: PromptTemplate,
        device_policy: CheckpointInferenceDevicePolicy,
        backend_family: str = HF_LOCAL_BACKEND_FAMILY,
        decoding: DecodingConfig | None = None,
        normalization: NormalizationPolicy | None = None,
        predictor_name: str = "verified_checkpoint_predictor",
    ) -> None:
        self._task_id = task.task_id
        self._backend = backend
        self._template = prompt_template
        self._decoding = decoding or DecodingConfig()
        if self._decoding.temperature != 0.0:
            raise CheckpointPredictionError(
                "checkpoint-backed prediction requires greedy decoding "
                "(temperature 0)")
        self._norm = normalization or NormalizationPolicy()
        self._candidates = frozenset(
            self._norm.normalize(f) for f in prompt_template.candidate_families
        )
        manifest = bundle.manifest
        predictor_id = derive_checkpoint_predictor_id(
            schema_version=1,
            predictor_version=CHECKPOINT_PREDICTOR_VERSION,
            checkpoint_id=manifest.checkpoint_id,
            checkpoint_digest=manifest.checkpoint_digest,
            checkpoint_format_id=manifest.format_spec.format_spec_id,
            compatibility_id=bundle.inference_compatibility.compatibility_id,
            model_spec_id=manifest.compatibility.model_spec_id,
            tokenizer_spec_id=manifest.compatibility.tokenizer_spec_id,
            prompt_template_id=prompt_template.prompt_template_id,
            decoding_config=self._decoding.model_dump(mode="json"),
            normalization_policy_id=self._norm.policy_id,
            backend_family=backend_family,
            inference_precision=device_policy.inference_precision,
            device_policy=device_policy.device_policy_id,
        )
        self._predictor_spec = CheckpointPredictorSpec(
            checkpoint_id=manifest.checkpoint_id,
            checkpoint_digest=manifest.checkpoint_digest,
            checkpoint_format_id=manifest.format_spec.format_spec_id,
            compatibility_id=bundle.inference_compatibility.compatibility_id,
            model_spec_id=manifest.compatibility.model_spec_id,
            tokenizer_spec_id=manifest.compatibility.tokenizer_spec_id,
            prompt_template_id=prompt_template.prompt_template_id,
            decoding=self._decoding,
            normalization_policy_id=self._norm.policy_id,
            backend_family=backend_family,
            device_policy_id=device_policy.device_policy_id,
            predictor_id=predictor_id,
        )
        # Gate-7 BaselineSpec: the checkpoint-predictor spec is embedded (and
        # hashed) so evaluation manifests persist it with no structural change.
        cfg = {
            "checkpoint_predictor_id": predictor_id,
            "checkpoint_predictor_spec": canonical_json_str(
                self._predictor_spec),
        }
        self._spec = BaselineSpec(
            baseline_name=predictor_name,
            rule_set_version=CHECKPOINT_PREDICTOR_VERSION,
            task_id=task.task_id, rule_configuration=cfg,
            baseline_id=derive_baseline_id(
                schema_version=1, baseline_name=predictor_name,
                baseline_version=1,
                rule_set_version=CHECKPOINT_PREDICTOR_VERSION,
                task_id=task.task_id, rule_configuration=cfg),
        )

    @property
    def spec(self) -> BaselineSpec:
        return self._spec

    @property
    def predictor_spec(self) -> CheckpointPredictorSpec:
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
            # Any other structured backend failure (overlength prompt, load
            # refusal, unsupported decoding) — explicit invalid, NEVER abstention.
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
