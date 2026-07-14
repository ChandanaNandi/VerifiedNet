"""Execution preflight: staged readiness checks + authorization (Gate 10E).

Preflight answers, BEFORE any model loading or gradient could exist: is this
one environment capable of executing this verified plan, with every mutable
input resolved to an immutable identity, at an honestly assessed determinism
level? The output is a ``TrainingExecutionAuthorization`` — evidence that one
specific environment was suitable at inspection time — or a structured
refusal. Authorization never changes the plan: a new environment may produce
a different authorization id for the same plan, and that is the point.

Ordered stages (every stage always appears in the findings; skipped stages
are reported as errors, never hidden):

    PLAN_VERIFICATION → CORPUS_VERIFICATION → BACKEND_CONTRACT →
    PACKAGE_CHECK → DEVICE_CHECK → MODEL_RESOLUTION → TOKENIZER_RESOLUTION →
    PRECISION_CHECK → MEMORY_ESTIMATE → DETERMINISM_ASSESSMENT →
    CHECKPOINT_COMPATIBILITY → AUTHORIZATION

No training happens here: no gradients, no optimizer/scheduler instantiation,
no weight mutation, no checkpoint, no model/tokenizer loading, no network.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel
from verifiednet.training.backend import (
    DeterminismCategory,
    EnvironmentProbe,
    RealTrainerBackendSpec,
    SystemEnvironmentProbe,
    TrainingEnvironmentSnapshot,
    build_hf_backend_capabilities,
    build_hf_full_finetune_backend_spec,
    snapshot_from_probe,
)
from verifiednet.training.planstore import LoadedTrainingPlan, read_training_plan
from verifiednet.training.resolve import (
    ArtifactResolutionError,
    ModelArtifactResolver,
    ResolvedModelArtifact,
    ResolvedTokenizerArtifact,
    TokenizerArtifactResolver,
)
from verifiednet.training.store import load_training_pairs, verify_training_corpus
from verifiednet.training.trainer import FAKE_TRAINER_IMPLEMENTATION_ID

PRECISION_BYTES: dict[str, int] = {"float32": 4, "float16": 2, "bfloat16": 2}
#: AdamW keeps two float32 moment tensors per parameter.
ADAMW_OPTIMIZER_BYTES_PER_PARAM = 8
#: Conservative per-token activation allowance (bytes) for the estimator.
DEFAULT_ACTIVATION_BYTES_PER_TOKEN = 8192
#: Conservative multiplicative overhead: x1.25 as integer arithmetic.
OVERHEAD_NUMERATOR, OVERHEAD_DENOMINATOR = 5, 4


class PreflightStage(StrEnum):
    PLAN_VERIFICATION = "plan_verification"
    CORPUS_VERIFICATION = "corpus_verification"
    BACKEND_CONTRACT = "backend_contract"
    PACKAGE_CHECK = "package_check"
    DEVICE_CHECK = "device_check"
    MODEL_RESOLUTION = "model_resolution"
    TOKENIZER_RESOLUTION = "tokenizer_resolution"
    PRECISION_CHECK = "precision_check"
    MEMORY_ESTIMATE = "memory_estimate"
    DETERMINISM_ASSESSMENT = "determinism_assessment"
    CHECKPOINT_COMPATIBILITY = "checkpoint_compatibility"
    AUTHORIZATION = "authorization"


PREFLIGHT_STAGE_ORDER: tuple[PreflightStage, ...] = tuple(PreflightStage)


class FindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class PreflightFinding(StrictModel):
    """One immutable, deterministic preflight observation."""

    stage: PreflightStage
    code: str = Field(min_length=1)
    severity: FindingSeverity
    message: str = Field(min_length=1)
    detail: str = ""
    affected_identity: str | None = None
    remediation: Literal["fix_plan", "fix_corpus", "fix_environment",
                         "fix_dependencies", "fix_artifacts", "none"]


class TrainingExecutionAuthorization(StrictModel):
    """Evidence that ONE environment was suitable for ONE plan at inspection
    time — or a structured refusal. Never a mutation of the plan; identical
    plans on different environments legitimately produce different
    authorization ids. Contains no timestamps."""

    schema_version: Literal[1] = 1
    authorization_contract_version: Literal[1] = 1
    training_plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    backend_spec_id: str = Field(min_length=1)
    environment_snapshot_id: str = Field(min_length=1)
    model_artifact: ResolvedModelArtifact | None = None
    tokenizer_artifact: ResolvedTokenizerArtifact | None = None
    device_capability_id: str = Field(min_length=1)
    determinism_category: DeterminismCategory
    checkpoint_format_id: str = Field(min_length=1)
    findings: tuple[PreflightFinding, ...] = Field(min_length=1)
    authorized: bool
    authorization_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingExecutionAuthorization:
        order = {stage: i for i, stage in enumerate(PREFLIGHT_STAGE_ORDER)}
        indices = [order[f.stage] for f in self.findings]
        if indices != sorted(indices):
            raise ValueError("findings must be ordered by preflight stage")
        stages_present = {f.stage for f in self.findings}
        missing = [s.value for s in PREFLIGHT_STAGE_ORDER
                   if s not in stages_present]
        if missing:
            raise ValueError(f"incomplete preflight stages: {missing}")
        has_error = any(f.severity is FindingSeverity.ERROR
                        for f in self.findings)
        if self.authorized and has_error:
            raise ValueError("authorized=True with ERROR findings is invalid")
        if self.authorized:
            if self.model_artifact is None or self.tokenizer_artifact is None:
                raise ValueError(
                    "authorization requires resolved model and tokenizer")
            if self.model_artifact.verification_status != "verified":
                raise ValueError("authorization requires a verified model")
            if self.tokenizer_artifact.verification_status != "verified":
                raise ValueError("authorization requires a verified tokenizer")
        if self.authorization_id != derive_authorization_id(self):
            raise ValueError("authorization_id does not match the evidence")
        return self


def derive_authorization_id(auth: TrainingExecutionAuthorization) -> str:
    payload = auth.model_dump(mode="json")
    payload.pop("authorization_id", None)
    return "trainauth-" + sha256_canonical(payload)[:24]


# ---------------------------------------------------------------------------
# Memory estimation (conservative, deterministic, integer arithmetic)
# ---------------------------------------------------------------------------


def estimate_training_memory_bytes(
    *,
    parameter_count: int,
    precision: str,
    per_device_batch_size: int,
    max_total_tokens: int,
    optimizer_name: str,
    activation_bytes_per_token: int = DEFAULT_ACTIVATION_BYTES_PER_TOKEN,
) -> int:
    """Conservative full-fine-tune memory estimate (an APPROXIMATION, not a
    guarantee — its only job is to refuse obviously impossible plans before
    model loading). Weights + gradients at the declared precision, AdamW
    moments at float32, a per-token activation allowance, x1.25 overhead.
    Parameter count must be EXPLICIT (resolved metadata) — never inferred
    from a model name. Pure integers, no live free-memory readings."""
    if parameter_count < 1:
        raise ValueError("parameter_count must be explicit and positive")
    if precision not in PRECISION_BYTES:
        raise ValueError(f"unknown precision: {precision!r}")
    if optimizer_name != "adamw":
        raise ValueError(f"no estimator for optimizer {optimizer_name!r}")
    weight_and_grad = parameter_count * PRECISION_BYTES[precision] * 2
    optimizer_state = parameter_count * ADAMW_OPTIMIZER_BYTES_PER_PARAM
    activations = (per_device_batch_size * max_total_tokens
                   * activation_bytes_per_token)
    subtotal = weight_and_grad + optimizer_state + activations
    return subtotal * OVERHEAD_NUMERATOR // OVERHEAD_DENOMINATOR


# ---------------------------------------------------------------------------
# Determinism assessment (honest; never overclaims)
# ---------------------------------------------------------------------------


def assess_determinism(
    *, snapshot: TrainingEnvironmentSnapshot,
) -> tuple[DeterminismCategory, str]:
    """Honest category + explanation for a REAL backend on this environment.

    Bit-identical weights are never claimed: even the strongest category here
    (``deterministic_supported``) means "the platform supports deterministic
    algorithms under fixed seeds, canonical data order, and a single process"
    — checkpoint serialization stability and kernel-level guarantees remain
    to be proven by an actual run (Gate 10F)."""
    if not snapshot.backend_available:
        return (DeterminismCategory.UNSUPPORTED,
                "required backend packages are missing or incompatible")
    if not snapshot.deterministic_algorithms_supported:
        return (DeterminismCategory.NONDETERMINISTIC,
                "framework deterministic-algorithm mode is unavailable")
    if snapshot.device.device_type == "cpu":
        return (DeterminismCategory.DETERMINISTIC_SUPPORTED,
                "cpu with deterministic algorithms: reproducible under fixed "
                "seeds, canonical data order, and a single process; "
                "bit-identical weights are not guaranteed until proven by an "
                "actual run")
    return (DeterminismCategory.BEST_EFFORT_DETERMINISTIC,
            f"{snapshot.device.device_type} kernels include operations "
            "without deterministic implementations; reproducibility is best "
            "effort even with deterministic mode enabled")


# ---------------------------------------------------------------------------
# The preflight run
# ---------------------------------------------------------------------------


class _Findings:
    """Ordered finding accumulator with per-stage bookkeeping."""

    def __init__(self) -> None:
        self.items: list[PreflightFinding] = []

    def add(self, stage: PreflightStage, code: str, severity: FindingSeverity,
            message: str, *, detail: str = "",
            affected_identity: str | None = None,
            remediation: Literal["fix_plan", "fix_corpus", "fix_environment",
                                 "fix_dependencies", "fix_artifacts",
                                 "none"] = "none") -> None:
        self.items.append(PreflightFinding(
            stage=stage, code=code, severity=severity, message=message,
            detail=detail, affected_identity=affected_identity,
            remediation=remediation))

    def ok(self, stage: PreflightStage, message: str) -> None:
        self.add(stage, "stage_passed", FindingSeverity.INFO, message)

    def skip(self, stage: PreflightStage, reason: str) -> None:
        self.add(stage, "stage_skipped", FindingSeverity.ERROR,
                 f"stage skipped: {reason}", remediation="none")

    def stage_has_error(self, stage: PreflightStage) -> bool:
        return any(f.stage is stage and f.severity is FindingSeverity.ERROR
                   for f in self.items)

    def has_error(self) -> bool:
        return any(f.severity is FindingSeverity.ERROR for f in self.items)


def run_preflight(
    *,
    plan_dir: str | Path,
    corpus_root: str | Path,
    backend_spec: RealTrainerBackendSpec,
    probe: EnvironmentProbe,
    model_resolver: ModelArtifactResolver,
    tokenizer_resolver: TokenizerArtifactResolver,
    allowed_determinism: tuple[DeterminismCategory, ...] = (
        DeterminismCategory.DETERMINISTIC_SUPPORTED,),
) -> tuple[TrainingExecutionAuthorization, TrainingEnvironmentSnapshot]:
    """Run every preflight stage in order; produce authorization or refusal.

    ``allowed_determinism`` is the explicit acknowledgement policy: to accept
    a best-effort environment the caller must say so — nothing downgrades
    silently.
    """
    f = _Findings()
    S, E, W = PreflightStage, FindingSeverity.ERROR, FindingSeverity.WARNING

    snapshot = snapshot_from_probe(probe, backend_spec)
    capabilities = build_hf_backend_capabilities()

    # 1. PLAN_VERIFICATION -------------------------------------------------
    loaded_plan: LoadedTrainingPlan | None = None
    try:
        loaded_plan = read_training_plan(plan_dir)
    except Exception as exc:
        f.add(S.PLAN_VERIFICATION, "plan_artifact_invalid", E,
              "training plan failed verification",
              detail=str(exc).splitlines()[0], remediation="fix_plan")
    if loaded_plan is not None:
        f.ok(S.PLAN_VERIFICATION, "training plan artifact verified")

    plan = loaded_plan.plan if loaded_plan is not None else None
    spec = plan.request.spec if plan is not None else None

    # 2. CORPUS_VERIFICATION ----------------------------------------------
    if plan is None or spec is None:
        f.skip(S.CORPUS_VERIFICATION, "no verified plan to bind against")
    else:
        result = verify_training_corpus(corpus_root)
        if not result.verified:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
            f.add(S.CORPUS_VERIFICATION, "corpus_artifact_invalid", E,
                  "training corpus failed verification", detail=detail,
                  remediation="fix_corpus")
        else:
            corpus = plan.request.corpus
            from verifiednet.training.store import load_training_corpus

            manifest = load_training_corpus(corpus_root).manifest
            binding_ok = (
                manifest.training_corpus_id == corpus.training_corpus_id
                and manifest.training_corpus_digest
                == corpus.training_corpus_digest
                and manifest.task_id == corpus.task_id
                and manifest.feature_policy_id == corpus.feature_policy_id
                and manifest.label_policy_id == corpus.label_policy_id
                and manifest.training_data_policy.training_data_policy_id
                == corpus.training_data_policy_id
                and manifest.input_template.input_template_id
                == corpus.input_template_id
                and manifest.target_template.target_template_id
                == corpus.target_template_id
                and manifest.example_count == corpus.example_count)
            if not binding_ok:
                f.add(S.CORPUS_VERIFICATION, "corpus_plan_mismatch", E,
                      "corpus artifact does not match the plan's binding",
                      affected_identity=manifest.training_corpus_id,
                      remediation="fix_plan")
            else:
                pairs = load_training_pairs(corpus_root)
                pair_fields = set(type(pairs[0]).model_fields)
                if len(pairs) != plan.expected_example_count:
                    f.add(S.CORPUS_VERIFICATION, "corpus_count_mismatch", E,
                          "trainer-facing pair count does not match the plan",
                          remediation="fix_corpus")
                elif pair_fields != {"schema_version", "input_text",
                                     "target_text"}:
                    f.add(S.CORPUS_VERIFICATION, "pair_loader_leaks_metadata",
                          E, "pair loader exposes non-pair fields",
                          remediation="fix_corpus")
                else:
                    f.ok(S.CORPUS_VERIFICATION,
                         "corpus verified and bound to the plan")

    # 3. BACKEND_CONTRACT --------------------------------------------------
    if spec is None or plan is None:
        f.skip(S.BACKEND_CONTRACT, "no verified plan")
    else:
        if spec.trainer_implementation_id == FAKE_TRAINER_IMPLEMENTATION_ID:
            f.add(S.BACKEND_CONTRACT, "fake_plan_on_real_backend", E,
                  "a fake-trainer plan is never executed by a real backend",
                  affected_identity=spec.trainer_implementation_id,
                  remediation="fix_plan")
        elif spec.trainer_implementation_id != backend_spec.backend_name:
            f.add(S.BACKEND_CONTRACT, "backend_implementation_mismatch", E,
                  "plan targets a different trainer implementation",
                  affected_identity=spec.trainer_implementation_id,
                  remediation="fix_plan")
        else:
            checks = (
                (spec.model.provider in capabilities.supported_model_families,
                 "model_family_unsupported", spec.model.provider),
                (spec.precision_policy in backend_spec.supported_precisions,
                 "precision_unsupported", spec.precision_policy),
                (spec.optimization.optimizer_name
                 in backend_spec.supported_optimizers,
                 "optimizer_unsupported", spec.optimization.optimizer_name),
                (spec.scheduler.scheduler_name
                 in backend_spec.supported_schedulers,
                 "scheduler_unsupported", spec.scheduler.scheduler_name),
                (spec.batch.declared_world_size == 1,
                 "world_size_unsupported", str(spec.batch.declared_world_size)),
                (plan.data_order == "canonical",
                 "data_order_unsupported", plan.data_order),
            )
            bad = [(code, value) for ok, code, value in checks if not ok]
            for code, value in bad:
                f.add(S.BACKEND_CONTRACT, code, E,
                      f"plan requirement unsupported by the backend: {value}",
                      remediation="fix_plan")
            if not bad:
                f.ok(S.BACKEND_CONTRACT, "plan is within the backend contract")

    # 4. PACKAGE_CHECK -----------------------------------------------------
    for record in snapshot.package_records:
        if record.status != "compatible":
            f.add(S.PACKAGE_CHECK, f"package_{record.status}", E,
                  f"required package {record.package_name} is {record.status}",
                  detail=f"required {record.required_constraint}, "
                         f"detected {record.detected_version}",
                  affected_identity=record.package_record_id,
                  remediation="fix_dependencies")
    if not f.stage_has_error(S.PACKAGE_CHECK):
        f.ok(S.PACKAGE_CHECK, "all required packages compatible")

    # 5. DEVICE_CHECK ------------------------------------------------------
    device = snapshot.device
    if snapshot.os_family not in backend_spec.supported_operating_systems:
        f.add(S.DEVICE_CHECK, "operating_system_unsupported", E,
              f"operating system {snapshot.os_family} unsupported",
              remediation="fix_environment")
    if device.device_type not in backend_spec.supported_device_types:
        f.add(S.DEVICE_CHECK, "device_type_unsupported", E,
              f"device type {device.device_type} unsupported",
              remediation="fix_environment")
    if device.declared_device_count == 0:
        f.add(S.DEVICE_CHECK, "no_supported_device", E,
              "no supported device is available",
              remediation="fix_environment")
    elif device.declared_device_count > 1:
        f.add(S.DEVICE_CHECK, "implicit_distributed_rejected", E,
              "multiple devices declared; the single-device contract rejects "
              "implicit distributed execution",
              remediation="fix_environment")
    if not f.stage_has_error(S.DEVICE_CHECK):
        f.ok(S.DEVICE_CHECK, "exactly one supported device")

    # 6. MODEL_RESOLUTION --------------------------------------------------
    model_artifact: ResolvedModelArtifact | None = None
    if spec is None:
        f.skip(S.MODEL_RESOLUTION, "no verified plan")
    else:
        try:
            model_artifact = model_resolver.resolve(spec.model)
        except ArtifactResolutionError as exc:
            f.add(S.MODEL_RESOLUTION, "model_resolution_failed", E, str(exc),
                  affected_identity=spec.model.model_spec_id,
                  remediation="fix_artifacts")
        if model_artifact is not None:
            if model_artifact.model_spec_id != spec.model.model_spec_id:
                f.add(S.MODEL_RESOLUTION, "model_spec_mismatch", E,
                      "resolver returned an artifact for a different model",
                      remediation="fix_artifacts")
            elif model_artifact.verification_status != "verified":
                f.add(S.MODEL_RESOLUTION, "model_unresolved", E,
                      "model artifact is not immutably resolved (no verified "
                      "local content); remote download is a separate, "
                      "explicitly authorized operation",
                      affected_identity=spec.model.model_spec_id,
                      remediation="fix_artifacts")
            else:
                f.ok(S.MODEL_RESOLUTION, "model resolved immutably")

    # 7. TOKENIZER_RESOLUTION ----------------------------------------------
    tokenizer_artifact: ResolvedTokenizerArtifact | None = None
    if spec is None:
        f.skip(S.TOKENIZER_RESOLUTION, "no verified plan")
    else:
        try:
            tokenizer_artifact = tokenizer_resolver.resolve(spec.tokenizer)
        except ArtifactResolutionError as exc:
            f.add(S.TOKENIZER_RESOLUTION, "tokenizer_resolution_failed", E,
                  str(exc), affected_identity=spec.tokenizer.tokenizer_spec_id,
                  remediation="fix_artifacts")
        if tokenizer_artifact is not None:
            if (tokenizer_artifact.tokenizer_spec_id
                    != spec.tokenizer.tokenizer_spec_id):
                f.add(S.TOKENIZER_RESOLUTION, "tokenizer_spec_mismatch", E,
                      "resolver returned an artifact for a different tokenizer",
                      remediation="fix_artifacts")
            elif tokenizer_artifact.verification_status != "verified":
                f.add(S.TOKENIZER_RESOLUTION, "tokenizer_unresolved", E,
                      "tokenizer artifact is not immutably resolved or its "
                      "special-vocabulary/padding contract disagrees",
                      affected_identity=spec.tokenizer.tokenizer_spec_id,
                      remediation="fix_artifacts")
            else:
                f.ok(S.TOKENIZER_RESOLUTION, "tokenizer resolved immutably")

    # 8. PRECISION_CHECK ---------------------------------------------------
    if spec is None:
        f.skip(S.PRECISION_CHECK, "no verified plan")
    elif spec.precision_policy not in device.supported_precisions:
        f.add(S.PRECISION_CHECK, "precision_unavailable_on_device", E,
              f"device does not support precision {spec.precision_policy}",
              remediation="fix_environment")
    else:
        f.ok(S.PRECISION_CHECK, "declared precision available on the device")

    # 9. MEMORY_ESTIMATE ---------------------------------------------------
    if spec is None:
        f.skip(S.MEMORY_ESTIMATE, "no verified plan")
    elif (model_artifact is None
          or model_artifact.declared_parameter_count is None):
        f.skip(S.MEMORY_ESTIMATE, "no resolved parameter count")
    elif device.total_memory_bytes <= 0:
        f.add(S.MEMORY_ESTIMATE, "total_memory_undeclared", E,
              "device total memory is not declared; the estimate cannot be "
              "compared (live free-memory readings are deliberately unused)",
              remediation="fix_environment")
    else:
        estimate = estimate_training_memory_bytes(
            parameter_count=model_artifact.declared_parameter_count,
            precision=spec.model.load_precision,
            per_device_batch_size=spec.batch.per_device_batch_size,
            max_total_tokens=spec.sequence_policy.max_total_tokens,
            optimizer_name=spec.optimization.optimizer_name)
        if estimate > device.total_memory_bytes:
            f.add(S.MEMORY_ESTIMATE, "insufficient_total_memory", E,
                  "conservative estimate exceeds declared device memory",
                  detail=f"estimated {estimate} > total "
                         f"{device.total_memory_bytes}",
                  remediation="fix_environment")
        else:
            f.add(S.MEMORY_ESTIMATE, "stage_passed", FindingSeverity.INFO,
                  "conservative estimate fits declared device memory",
                  detail=f"estimated {estimate} <= total "
                         f"{device.total_memory_bytes}")

    # 10. DETERMINISM_ASSESSMENT --------------------------------------------
    category, explanation = assess_determinism(snapshot=snapshot)
    if category not in allowed_determinism:
        f.add(S.DETERMINISM_ASSESSMENT, "determinism_category_forbidden", E,
              f"category {category.value} is not allowed by the policy",
              detail=explanation, remediation="fix_environment")
    elif category is DeterminismCategory.BEST_EFFORT_DETERMINISTIC:
        f.add(S.DETERMINISM_ASSESSMENT, "best_effort_acknowledged", W,
              "best-effort determinism explicitly acknowledged by policy",
              detail=explanation)
    else:
        f.add(S.DETERMINISM_ASSESSMENT, "stage_passed", FindingSeverity.INFO,
              f"determinism category: {category.value}", detail=explanation)

    # 11. CHECKPOINT_COMPATIBILITY -------------------------------------------
    if spec is None:
        f.skip(S.CHECKPOINT_COMPATIBILITY, "no verified plan")
    elif (spec.checkpoint_policy
          not in backend_spec.supported_checkpoint_declarations):
        f.add(S.CHECKPOINT_COMPATIBILITY, "checkpoint_declaration_unsupported",
              E, f"checkpoint declaration {spec.checkpoint_policy!r} "
              "unsupported", remediation="fix_plan")
    else:
        f.ok(S.CHECKPOINT_COMPATIBILITY,
             "checkpoint declaration supported (none: no checkpoint output)")

    # 12. AUTHORIZATION -------------------------------------------------------
    refused = f.has_error()
    if refused:
        f.add(S.AUTHORIZATION, "authorization_refused", E,
              "preflight refused: ERROR findings are present")
    else:
        f.add(S.AUTHORIZATION, "authorization_granted", FindingSeverity.INFO,
              "every stage passed; execution may be authorized")

    fields: dict[str, object] = {
        "training_plan_id": (plan.training_plan_id if plan is not None
                             else "unverified"),
        "plan_digest": (loaded_plan.manifest.plan_digest
                        if loaded_plan is not None else "unverified"),
        "training_corpus_id": (spec.training_corpus_id if spec is not None
                               else "unverified"),
        "training_corpus_digest": (spec.training_corpus_digest
                                   if spec is not None else "unverified"),
        "backend_spec_id": backend_spec.backend_spec_id,
        "environment_snapshot_id": snapshot.environment_snapshot_id,
        "model_artifact": model_artifact,
        "tokenizer_artifact": tokenizer_artifact,
        "device_capability_id": device.device_capability_id,
        "determinism_category": category,
        "checkpoint_format_id": (spec.checkpoint_policy if spec is not None
                                 else "none"),
        "findings": tuple(f.items),
        "authorized": not refused,
    }
    probe_model = TrainingExecutionAuthorization.model_construct(**fields)  # type: ignore[arg-type]
    authorization = TrainingExecutionAuthorization(
        **fields,  # type: ignore[arg-type]
        authorization_id=derive_authorization_id(probe_model))
    return authorization, snapshot


# ---------------------------------------------------------------------------
# The real backend adapter boundary (no train() exists)
# ---------------------------------------------------------------------------


@runtime_checkable
class RealTrainerBackend(Protocol):
    """The real-backend boundary: contract + evidence + preflight. There is
    deliberately NO ``train`` method in this gate — a later gate may consume
    a VERIFIED authorization to execute, making it impossible to reach
    execution without preflight."""

    @property
    def spec(self) -> RealTrainerBackendSpec: ...

    def inspect_environment(self) -> TrainingEnvironmentSnapshot: ...

    def preflight(
        self,
        *,
        plan_dir: str | Path,
        corpus_root: str | Path,
        model_resolver: ModelArtifactResolver,
        tokenizer_resolver: TokenizerArtifactResolver,
        allowed_determinism: tuple[DeterminismCategory, ...],
    ) -> tuple[TrainingExecutionAuthorization, TrainingEnvironmentSnapshot]: ...


class HuggingFaceFullFinetuneBackend:
    """The single Gate 10E real-backend adapter (preflight only).

    Observation happens through an injectable ``EnvironmentProbe`` — the
    deterministic ``FakeEnvironmentProbe`` in the offline suite, the CPU-only
    ``SystemEnvironmentProbe`` in optional integration tests. No ML framework
    is imported by this class, ever; package presence is metadata-observed.
    """

    def __init__(self, probe: EnvironmentProbe | None = None) -> None:
        self._spec = build_hf_full_finetune_backend_spec()
        self._probe: EnvironmentProbe = (
            probe if probe is not None else SystemEnvironmentProbe())

    @property
    def spec(self) -> RealTrainerBackendSpec:
        return self._spec

    def inspect_environment(self) -> TrainingEnvironmentSnapshot:
        return snapshot_from_probe(self._probe, self._spec)

    def preflight(
        self,
        *,
        plan_dir: str | Path,
        corpus_root: str | Path,
        model_resolver: ModelArtifactResolver,
        tokenizer_resolver: TokenizerArtifactResolver,
        allowed_determinism: tuple[DeterminismCategory, ...] = (
            DeterminismCategory.DETERMINISTIC_SUPPORTED,),
    ) -> tuple[TrainingExecutionAuthorization, TrainingEnvironmentSnapshot]:
        return run_preflight(
            plan_dir=plan_dir, corpus_root=corpus_root,
            backend_spec=self._spec, probe=self._probe,
            model_resolver=model_resolver,
            tokenizer_resolver=tokenizer_resolver,
            allowed_determinism=allowed_determinism)
