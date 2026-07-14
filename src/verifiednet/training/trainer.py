"""Trainer abstraction: capabilities, request, plan, fake trainer (Gate 10B).

The authoritative trainer operation in this gate is ``plan`` — NOT ``train``. A
``Trainer`` receives only a ``TrainingSpec`` and a narrow
``TrainingCorpusDescriptor`` (identities + counts, never raw examples, never
prepared/evaluation/benchmark artifacts, never trace metadata) and returns a
deterministic ``TrainingPlan``. The optional simulated execution is explicitly
named ``simulate`` and returns a result stamped ``simulated=True`` — nothing in
this gate can be mistaken for real training or a real checkpoint.

All derived arithmetic is integer with explicit remainder behavior:

    batches_per_epoch          = ceil(example_count / per_device_batch_size)
                                 (the final partial batch counts as one batch)
    optimizer_steps_per_epoch  = ceil(batches_per_epoch / gradient_accumulation)
                                 (a partial accumulation window flushes as one step)
    epochs budget:  optimizer_steps = epochs * optimizer_steps_per_epoch
    steps budget:   optimizer_steps = max_optimizer_steps (epochs not derived)

The FakeTrainer declares ONLY capabilities it genuinely simulates and claims
``deterministic`` honestly (it is pure). A real backend would declare
``best_effort_deterministic`` or ``conditional`` — the plan never overstates.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel
from verifiednet.training.spec import EpochBudget, TrainingSpec
from verifiednet.training.store import TrainingCorpusManifest

TRAINER_CAPABILITY_VERSION = 1
TRAINING_PLAN_VERSION = 1
FAKE_TRAINER_IMPLEMENTATION_ID = "fake-trainer-v1"


class TrainerPlanError(VerifiedNetError):
    """A training request/plan could not be built (capability/binding mismatch)."""


class DeterminismClaim(StrEnum):
    DETERMINISTIC = "deterministic"
    BEST_EFFORT_DETERMINISTIC = "best_effort_deterministic"
    NONDETERMINISTIC = "nondeterministic"


class TrainerCapabilities(StrictModel):
    """Frozen, content-addressed declaration of what a trainer genuinely supports."""

    schema_version: Literal[1] = 1
    capability_contract_version: Literal[1] = 1
    trainer_implementation_id: str = Field(min_length=1)
    supported_model_families: tuple[str, ...] = Field(min_length=1)
    supported_precisions: tuple[str, ...] = Field(min_length=1)
    supported_optimizers: tuple[str, ...] = Field(min_length=1)
    supported_schedulers: tuple[str, ...] = Field(min_length=1)
    supported_checkpoint_policies: tuple[str, ...] = Field(min_length=1)
    supports_deterministic: Literal["yes", "no", "conditional"]
    supports_cpu: bool
    supports_gpu: bool
    supports_adapter_training: bool
    supports_full_finetuning: bool
    supports_distributed: bool
    capability_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainerCapabilities:
        for name in ("supported_model_families", "supported_precisions",
                     "supported_optimizers", "supported_schedulers",
                     "supported_checkpoint_policies"):
            values = list(getattr(self, name))
            if values != sorted(values) or len(values) != len(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        expected = derive_capability_id(self)
        if self.capability_id != expected:
            raise ValueError("capability_id does not match the declared capabilities")
        return self


def _capability_id_from_payload(payload: dict[str, object]) -> str:
    return "traincap-" + sha256_canonical(payload)[:16]


def derive_capability_id(caps: TrainerCapabilities) -> str:
    payload = caps.model_dump(mode="json")
    payload.pop("capability_id", None)
    return _capability_id_from_payload(payload)


def build_capabilities(
    *,
    trainer_implementation_id: str,
    supported_model_families: tuple[str, ...],
    supported_precisions: tuple[str, ...],
    supported_optimizers: tuple[str, ...],
    supported_schedulers: tuple[str, ...],
    supported_checkpoint_policies: tuple[str, ...],
    supports_deterministic: Literal["yes", "no", "conditional"],
    supports_cpu: bool,
    supports_gpu: bool,
    supports_adapter_training: bool,
    supports_full_finetuning: bool,
    supports_distributed: bool,
) -> TrainerCapabilities:
    """Construct capabilities with the content-derived, self-validated id."""
    payload: dict[str, object] = {
        "schema_version": 1, "capability_contract_version": 1,
        "trainer_implementation_id": trainer_implementation_id,
        "supported_model_families": list(supported_model_families),
        "supported_precisions": list(supported_precisions),
        "supported_optimizers": list(supported_optimizers),
        "supported_schedulers": list(supported_schedulers),
        "supported_checkpoint_policies": list(supported_checkpoint_policies),
        "supports_deterministic": supports_deterministic,
        "supports_cpu": supports_cpu, "supports_gpu": supports_gpu,
        "supports_adapter_training": supports_adapter_training,
        "supports_full_finetuning": supports_full_finetuning,
        "supports_distributed": supports_distributed,
    }
    return TrainerCapabilities(
        trainer_implementation_id=trainer_implementation_id,
        supported_model_families=supported_model_families,
        supported_precisions=supported_precisions,
        supported_optimizers=supported_optimizers,
        supported_schedulers=supported_schedulers,
        supported_checkpoint_policies=supported_checkpoint_policies,
        supports_deterministic=supports_deterministic,
        supports_cpu=supports_cpu, supports_gpu=supports_gpu,
        supports_adapter_training=supports_adapter_training,
        supports_full_finetuning=supports_full_finetuning,
        supports_distributed=supports_distributed,
        capability_id=_capability_id_from_payload(payload),
    )


class TrainingCorpusDescriptor(StrictModel):
    """The ONLY corpus view a trainer receives: identities + counts, no examples."""

    schema_version: Literal[1] = 1
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    example_count: int = Field(ge=1)  # an empty corpus cannot be planned
    task_id: str = Field(min_length=1)
    training_data_policy_id: str = Field(min_length=1)
    input_template_id: str = Field(min_length=1)
    target_template_id: str = Field(min_length=1)
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    source_partition: Literal["train"] = "train"


def descriptor_from_manifest(manifest: TrainingCorpusManifest) -> TrainingCorpusDescriptor:
    """Derive the narrow descriptor from a verified Gate 10A corpus manifest."""
    if manifest.example_count < 1:
        raise TrainerPlanError("an empty training corpus cannot be planned")
    return TrainingCorpusDescriptor(
        training_corpus_id=manifest.training_corpus_id,
        training_corpus_digest=manifest.training_corpus_digest,
        example_count=manifest.example_count, task_id=manifest.task_id,
        training_data_policy_id=manifest.training_data_policy.training_data_policy_id,
        input_template_id=manifest.input_template.input_template_id,
        target_template_id=manifest.target_template.target_template_id,
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
    )


class TrainingRequest(StrictModel):
    """A validated binding of spec + corpus descriptor + trainer capability."""

    schema_version: Literal[1] = 1
    spec: TrainingSpec
    corpus: TrainingCorpusDescriptor
    trainer_capability_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingRequest:
        if self.spec.training_corpus_id != self.corpus.training_corpus_id:
            raise ValueError("spec and corpus descriptor disagree on corpus id")
        if self.spec.training_corpus_digest != self.corpus.training_corpus_digest:
            raise ValueError("spec and corpus descriptor disagree on corpus digest")
        if self.spec.task_id != self.corpus.task_id:
            raise ValueError("spec and corpus descriptor disagree on task id")
        expected = derive_request_id(
            training_spec_id=self.spec.training_spec_id,
            corpus=self.corpus, trainer_capability_id=self.trainer_capability_id)
        if self.request_id != expected:
            raise ValueError("request_id does not match the request content")
        return self


def derive_request_id(
    *, training_spec_id: str, corpus: TrainingCorpusDescriptor,
    trainer_capability_id: str,
) -> str:
    payload = {
        "training_spec_id": training_spec_id,
        "corpus": corpus.model_dump(mode="json"),
        "trainer_capability_id": trainer_capability_id,
    }
    return "trainreq-" + sha256_canonical(payload)[:16]


def build_training_request(
    *, spec: TrainingSpec, corpus: TrainingCorpusDescriptor,
    capabilities: TrainerCapabilities,
) -> TrainingRequest:
    """Capability negotiation — fail closed on ANY unsupported requirement."""
    if spec.trainer_implementation_id != capabilities.trainer_implementation_id:
        raise TrainerPlanError("spec targets a different trainer implementation")
    if spec.model.provider not in capabilities.supported_model_families:
        raise TrainerPlanError(
            f"model family {spec.model.provider!r} unsupported by the trainer")
    if spec.precision_policy not in capabilities.supported_precisions:
        raise TrainerPlanError(
            f"precision {spec.precision_policy!r} unsupported by the trainer")
    if spec.optimization.optimizer_name not in capabilities.supported_optimizers:
        raise TrainerPlanError(
            f"optimizer {spec.optimization.optimizer_name!r} unsupported")
    if spec.scheduler.scheduler_name not in capabilities.supported_schedulers:
        raise TrainerPlanError(
            f"scheduler {spec.scheduler.scheduler_name!r} unsupported")
    if spec.checkpoint_policy not in capabilities.supported_checkpoint_policies:
        raise TrainerPlanError(
            f"checkpoint policy {spec.checkpoint_policy!r} unsupported")
    return TrainingRequest(
        spec=spec, corpus=corpus, trainer_capability_id=capabilities.capability_id,
        request_id=derive_request_id(
            training_spec_id=spec.training_spec_id, corpus=corpus,
            trainer_capability_id=capabilities.capability_id))


# ---------------------------------------------------------------------------
# Derived arithmetic + the plan
# ---------------------------------------------------------------------------


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def compute_batches_per_epoch(example_count: int, per_device_batch_size: int) -> int:
    return _ceil_div(example_count, per_device_batch_size)


def compute_optimizer_steps_per_epoch(
    batches_per_epoch: int, gradient_accumulation_steps: int,
) -> int:
    return _ceil_div(batches_per_epoch, gradient_accumulation_steps)


def compute_plan_counts(request: TrainingRequest) -> tuple[int, int | None, int]:
    """Return (batches_per_epoch, expected_epochs, total optimizer steps)."""
    spec = request.spec
    batches = compute_batches_per_epoch(
        request.corpus.example_count, spec.batch.per_device_batch_size)
    steps_per_epoch = compute_optimizer_steps_per_epoch(
        batches, spec.batch.gradient_accumulation_steps)
    if isinstance(spec.budget, EpochBudget):
        return batches, spec.budget.epochs, spec.budget.epochs * steps_per_epoch
    return batches, None, spec.budget.max_optimizer_steps


class TrainingPlan(StrictModel):
    """The principal Gate 10B artifact: a deterministic, validated run plan.

    It contains NO checkpoint and no raw training examples; the expected input
    source is the trainer-facing pairs of the bound corpus, and the output
    namespace is a declaration only (nothing is written there in this gate).
    """

    schema_version: Literal[1] = 1
    plan_format_version: Literal[1] = 1
    request: TrainingRequest
    expected_example_count: int = Field(ge=1)
    expected_epochs: int | None = None
    batches_per_epoch: int = Field(ge=1)
    optimizer_steps: int = Field(ge=1)
    effective_batch_size: int = Field(ge=1)
    data_order: Literal["canonical"] = "canonical"
    input_source: Literal["training_corpus_pairs"] = "training_corpus_pairs"
    output_namespace: str = Field(min_length=1)
    determinism_claim: DeterminismClaim
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    training_plan_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingPlan:
        batches, epochs, steps = compute_plan_counts(self.request)
        if self.expected_example_count != self.request.corpus.example_count:
            raise ValueError("expected_example_count does not match the corpus")
        if self.batches_per_epoch != batches:
            raise ValueError("batches_per_epoch does not match the derivation")
        if self.expected_epochs != epochs:
            raise ValueError("expected_epochs does not match the budget")
        if self.optimizer_steps != steps:
            raise ValueError("optimizer_steps does not match the derivation")
        if self.effective_batch_size != self.request.spec.batch.effective_batch_size:
            raise ValueError("effective_batch_size does not match the spec")
        expected = derive_training_plan_id(self)
        if self.training_plan_id != expected:
            raise ValueError("training_plan_id does not match the plan content")
        return self


def _plan_id_from_values(
    *, request_id: str, trainer_capability_id: str, expected_example_count: int,
    expected_epochs: int | None, batches_per_epoch: int, optimizer_steps: int,
    effective_batch_size: int, data_order: str, input_source: str,
    output_namespace: str, determinism_claim: str, warnings: tuple[str, ...],
) -> str:
    payload = {
        "request_id": request_id,
        "trainer_capability_id": trainer_capability_id,
        "expected_example_count": expected_example_count,
        "expected_epochs": expected_epochs,
        "batches_per_epoch": batches_per_epoch,
        "optimizer_steps": optimizer_steps,
        "effective_batch_size": effective_batch_size,
        "data_order": data_order,
        "input_source": input_source,
        "output_namespace": output_namespace,
        "determinism_claim": determinism_claim,
        "warnings": list(warnings),
    }
    return "trainplan-" + sha256_canonical(payload)[:24]


def derive_training_plan_id(plan: TrainingPlan) -> str:
    return _plan_id_from_values(
        request_id=plan.request.request_id,
        trainer_capability_id=plan.request.trainer_capability_id,
        expected_example_count=plan.expected_example_count,
        expected_epochs=plan.expected_epochs,
        batches_per_epoch=plan.batches_per_epoch,
        optimizer_steps=plan.optimizer_steps,
        effective_batch_size=plan.effective_batch_size,
        data_order=plan.data_order, input_source=plan.input_source,
        output_namespace=plan.output_namespace,
        determinism_claim=plan.determinism_claim.value, warnings=plan.warnings,
    )


class SimulatedTrainingResult(StrictModel):
    """Deterministic SYNTHETIC facts from the fake trainer. Never a checkpoint."""

    schema_version: Literal[1] = 1
    simulated: Literal[True] = True
    request_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    simulated_completed_steps: int = Field(ge=1)
    simulated_final_loss: str = Field(min_length=1)  # canonical decimal string
    produced_checkpoint: Literal[False] = False


@runtime_checkable
class Trainer(Protocol):
    """The narrow trainer contract: capabilities + planning. No real training."""

    @property
    def capabilities(self) -> TrainerCapabilities: ...

    def plan(
        self, *, spec: TrainingSpec, corpus: TrainingCorpusDescriptor
    ) -> TrainingPlan: ...


class FakeTrainer:
    """A deterministic fake trainer proving orchestration and identity rules.

    It genuinely simulates: CPU-only planning for the "fake" model family at
    float32 with adamw + constant/linear_warmup schedulers and the "none"
    checkpoint policy, fully deterministically. It declares nothing else. It
    never loads a model or tokenizer, computes a gradient, or writes weights.
    """

    def __init__(self) -> None:
        self._capabilities = build_capabilities(
            trainer_implementation_id=FAKE_TRAINER_IMPLEMENTATION_ID,
            supported_model_families=("fake",),
            supported_precisions=("float32",),
            supported_optimizers=("adamw",),
            supported_schedulers=("constant", "linear_warmup"),
            supported_checkpoint_policies=("none",),
            supports_deterministic="yes",
            supports_cpu=True, supports_gpu=False,
            supports_adapter_training=False, supports_full_finetuning=False,
            supports_distributed=False,
        )

    @property
    def capabilities(self) -> TrainerCapabilities:
        return self._capabilities

    def plan(
        self, *, spec: TrainingSpec, corpus: TrainingCorpusDescriptor
    ) -> TrainingPlan:
        request = build_training_request(
            spec=spec, corpus=corpus, capabilities=self._capabilities)
        batches, epochs, steps = compute_plan_counts(request)
        output_namespace = f"training-runs/{request.request_id}"
        plan_id = _plan_id_from_values(
            request_id=request.request_id,
            trainer_capability_id=request.trainer_capability_id,
            expected_example_count=corpus.example_count, expected_epochs=epochs,
            batches_per_epoch=batches, optimizer_steps=steps,
            effective_batch_size=spec.batch.effective_batch_size,
            data_order="canonical", input_source="training_corpus_pairs",
            output_namespace=output_namespace,
            determinism_claim=DeterminismClaim.DETERMINISTIC.value, warnings=(),
        )
        return TrainingPlan(
            request=request, expected_example_count=corpus.example_count,
            expected_epochs=epochs, batches_per_epoch=batches,
            optimizer_steps=steps,
            effective_batch_size=spec.batch.effective_batch_size,
            output_namespace=output_namespace,
            determinism_claim=DeterminismClaim.DETERMINISTIC,
            training_plan_id=plan_id,
        )

    def simulate(self, plan: TrainingPlan) -> SimulatedTrainingResult:
        """Deterministic synthetic outcome (content-derived; explicitly simulated)."""
        # A fake loss in [0.100000, 1.099999], derived purely from the plan id.
        digest = sha256_canonical({"plan": plan.training_plan_id})
        fake_loss = Decimal(int(digest[:6], 16) % 1_000_000) / Decimal(1_000_000)
        loss_str = format((fake_loss + Decimal("0.1")).quantize(Decimal("0.000001")), "f")
        return SimulatedTrainingResult(
            request_id=plan.request.request_id,
            training_plan_id=plan.training_plan_id,
            simulated_completed_steps=plan.optimizer_steps,
            simulated_final_loss=loss_str,
        )

