"""Reproducible training specification (Gate 10B).

A ``TrainingSpec`` is the complete, frozen, content-addressed description of a
FUTURE training run: model identity, tokenizer identity, corpus binding,
precision/sequence/batch/optimization/scheduler/budget/seed/data-order policies.
Every field that could affect future weights is explicit here — nothing hides
inside a trainer implementation — and every identity is a validated content hash
(no timestamps, hosts, users, env, or paths).

Numeric identity-bearing fields (learning rate, weight decay, betas, epsilon,
gradient clipping, warmup ratio) are CANONICAL DECIMAL STRINGS: the validator
normalizes each value (``Decimal.normalize`` + fixed-point formatting), so
equivalent numbers ("1e-3", "0.0010") serialize — and hash — identically, with
no binary floating-point ambiguity.

Gate 10B executes nothing: no optimizer, scheduler, gradient, model, or
tokenizer is ever loaded or run (the AST boundary guard forbids ML frameworks in
this package).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel

TRAINING_SPEC_VERSION = 1
SEED_POLICY_VERSION = 1
DATA_ORDER_POLICY_VERSION = 1

#: Mutable revision aliases that can silently change weights — always refused.
FORBIDDEN_REVISIONS: frozenset[str] = frozenset({"latest", "main", "master", "head", ""})


def canonical_decimal(value: str) -> str:
    """Normalize a decimal string to its canonical fixed-point form.

    Equivalent numeric inputs ("1e-3", "0.0010", "0.001") all normalize to the
    same string ("0.001"), so identity hashes are free of representation noise.
    Non-finite or unparseable values are rejected.
    """
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a decimal number: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite decimal not allowed: {value!r}")
    return format(parsed.normalize(), "f")


def _positive_decimal(value: str) -> str:
    out = canonical_decimal(value)
    if Decimal(out) <= 0:
        raise ValueError(f"must be > 0: {value!r}")
    return out


def _nonnegative_decimal(value: str) -> str:
    out = canonical_decimal(value)
    if Decimal(out) < 0:
        raise ValueError(f"must be >= 0: {value!r}")
    return out


class TrainableModelSpec(StrictModel):
    """Frozen model identity. An immutable revision is REQUIRED — mutable
    aliases like ``latest`` are refused so the identity can never drift."""

    schema_version: Literal[1] = 1
    provider: str = Field(min_length=1)  # e.g. "huggingface", "local"
    model_identifier: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)  # commit hash / content hash — immutable
    model_class: str = Field(min_length=1)  # architecture / model class identifier
    load_precision: Literal["float32", "float16", "bfloat16"] = "float32"
    trust_remote_code: Literal[False] = False
    model_spec_id: str = Field(min_length=1)

    @field_validator("model_revision")
    @classmethod
    def _immutable_revision(cls, value: str) -> str:
        if value.strip().lower() in FORBIDDEN_REVISIONS:
            raise ValueError(f"mutable model revision not allowed: {value!r}")
        return value

    @field_validator("model_identifier")
    @classmethod
    def _no_paths(cls, value: str) -> str:
        if value.startswith("/") or value.startswith("~"):
            raise ValueError("absolute local paths may not participate in model identity")
        return value

    @model_validator(mode="after")
    def _valid(self) -> TrainableModelSpec:
        expected = "model-" + sha256_canonical({
            "schema_version": self.schema_version, "provider": self.provider,
            "model_identifier": self.model_identifier,
            "model_revision": self.model_revision, "model_class": self.model_class,
            "load_precision": self.load_precision,
            "trust_remote_code": self.trust_remote_code,
        })[:16]
        if self.model_spec_id != expected:
            raise ValueError("model_spec_id does not match the model specification")
        return self


def derive_model_spec_id(
    *, provider: str, model_identifier: str, model_revision: str,
    model_class: str, load_precision: str, trust_remote_code: bool = False,
) -> str:
    return "model-" + sha256_canonical({
        "schema_version": 1, "provider": provider,
        "model_identifier": model_identifier, "model_revision": model_revision,
        "model_class": model_class, "load_precision": load_precision,
        "trust_remote_code": trust_remote_code,
    })[:16]


class TokenizerSpec(StrictModel):
    """Frozen tokenizer identity. Never assumed to match the model — the pairing
    is an explicit declaration validated by the trainer request."""

    schema_version: Literal[1] = 1
    tokenizer_identifier: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    tokenizer_class: str = Field(min_length=1)
    special_vocab_policy: Literal["model_defaults"] = "model_defaults"
    padding_policy: Literal["right", "left"] = "right"
    truncation_policy: Literal["fail_closed", "truncate_right"] = "fail_closed"
    tokenizer_spec_id: str = Field(min_length=1)

    @field_validator("tokenizer_revision")
    @classmethod
    def _immutable_revision(cls, value: str) -> str:
        if value.strip().lower() in FORBIDDEN_REVISIONS:
            raise ValueError(f"mutable tokenizer revision not allowed: {value!r}")
        return value

    @model_validator(mode="after")
    def _valid(self) -> TokenizerSpec:
        expected = derive_tokenizer_spec_id(
            tokenizer_identifier=self.tokenizer_identifier,
            tokenizer_revision=self.tokenizer_revision,
            tokenizer_class=self.tokenizer_class,
            special_vocab_policy=self.special_vocab_policy,
            padding_policy=self.padding_policy,
            truncation_policy=self.truncation_policy,
        )
        if self.tokenizer_spec_id != expected:
            raise ValueError("tokenizer_spec_id does not match the tokenizer spec")
        return self


def derive_tokenizer_spec_id(
    *, tokenizer_identifier: str, tokenizer_revision: str, tokenizer_class: str,
    special_vocab_policy: str, padding_policy: str, truncation_policy: str,
) -> str:
    return "tok-" + sha256_canonical({
        "schema_version": 1, "tokenizer_identifier": tokenizer_identifier,
        "tokenizer_revision": tokenizer_revision, "tokenizer_class": tokenizer_class,
        "special_vocab_policy": special_vocab_policy,
        "padding_policy": padding_policy, "truncation_policy": truncation_policy,
    })[:16]


class SequenceLengthPolicy(StrictModel):
    """Explicit sequence budget. The default overlength behavior FAILS CLOSED —
    nothing is ever silently truncated without a recorded rule."""

    schema_version: Literal[1] = 1
    max_input_tokens: int = Field(ge=1)
    max_target_tokens: int = Field(ge=1)
    max_total_tokens: int = Field(ge=2)
    overlength_behavior: Literal["fail_closed", "truncate_input_right"] = "fail_closed"

    @model_validator(mode="after")
    def _consistent(self) -> SequenceLengthPolicy:
        if self.max_total_tokens < self.max_input_tokens + self.max_target_tokens:
            raise ValueError(
                "max_total_tokens must cover max_input_tokens + max_target_tokens")
        return self


class BatchConfig(StrictModel):
    """Explicit batch arithmetic. ``declared_world_size`` is LOCKED to 1 in Gate
    10B (never derived from the machine or environment); distributed training is
    modeled only as this explicit declaration."""

    schema_version: Literal[1] = 1
    per_device_batch_size: int = Field(ge=1)
    gradient_accumulation_steps: int = Field(ge=1)
    declared_world_size: Literal[1] = 1
    effective_batch_size: int = Field(ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> BatchConfig:
        expected = (self.per_device_batch_size * self.gradient_accumulation_steps
                    * self.declared_world_size)
        if self.effective_batch_size != expected:
            raise ValueError(
                f"effective_batch_size must be {expected}, got {self.effective_batch_size}")
        return self


class OptimizationConfig(StrictModel):
    """Immutable optimization declaration (nothing here is executed).

    All numeric fields are canonical decimal strings (see ``canonical_decimal``).
    """

    schema_version: Literal[1] = 1
    optimizer_name: str = Field(min_length=1)
    learning_rate: str
    weight_decay: str = "0"
    beta1: str = "0.9"
    beta2: str = "0.999"
    epsilon: str = "0.00000001"
    max_grad_norm: str | None = "1"
    loss_reduction: Literal["mean"] = "mean"

    @field_validator("learning_rate")
    @classmethod
    def _lr(cls, value: str) -> str:
        return _positive_decimal(value)

    @field_validator("weight_decay", "beta1", "beta2", "epsilon")
    @classmethod
    def _nonneg(cls, value: str) -> str:
        return _nonnegative_decimal(value)

    @field_validator("max_grad_norm")
    @classmethod
    def _clip(cls, value: str | None) -> str | None:
        return None if value is None else _positive_decimal(value)


class SchedulerConfig(StrictModel):
    """Immutable scheduler declaration. Warmup is EITHER steps OR ratio — a
    contradictory pair is rejected."""

    schema_version: Literal[1] = 1
    scheduler_name: Literal["constant", "linear_warmup"] = "constant"
    warmup_steps: int | None = None
    warmup_ratio: str | None = None
    total_step_source: Literal["derived_from_budget"] = "derived_from_budget"
    min_learning_rate: str | None = None

    @field_validator("warmup_ratio", "min_learning_rate")
    @classmethod
    def _dec(cls, value: str | None) -> str | None:
        return None if value is None else _nonnegative_decimal(value)

    @model_validator(mode="after")
    def _consistent(self) -> SchedulerConfig:
        if self.warmup_steps is not None and self.warmup_ratio is not None:
            raise ValueError("warmup_steps and warmup_ratio are mutually exclusive")
        if self.warmup_steps is not None and self.warmup_steps < 0:
            raise ValueError("warmup_steps must be >= 0")
        if self.scheduler_name == "constant" and (
                self.warmup_steps or self.warmup_ratio):
            raise ValueError("constant scheduler takes no warmup")
        return self


class EpochBudget(StrictModel):
    """Fixed epochs; optimizer steps derive deterministically (integer ceil)."""

    schema_version: Literal[1] = 1
    kind: Literal["epochs"] = "epochs"
    epochs: int = Field(ge=1)


class StepBudget(StrictModel):
    """Fixed optimizer steps; epochs are not a stopping criterion."""

    schema_version: Literal[1] = 1
    kind: Literal["steps"] = "steps"
    max_optimizer_steps: int = Field(ge=1)


TrainingBudget = Annotated[EpochBudget | StepBudget, Field(discriminator="kind")]


class SeedPolicy(StrictModel):
    """Versioned deterministic seeds (never time/PID/env-derived).

    These are REQUESTED seeds: a future real backend is expected to apply them to
    data ordering, model init, dropout, and its own RNG, but backend
    nondeterminism (e.g. non-deterministic CUDA kernels) may remain — that
    limitation is carried honestly by the plan's determinism claim, never hidden.
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    data_order_seed: int = Field(ge=0)
    model_init_seed: int = Field(ge=0)
    dropout_seed: int = Field(ge=0)
    backend_seed: int = Field(ge=0)


class DataOrderPolicy(StrictModel):
    """Deterministic training-example ordering. Gate 10B locks CANONICAL order
    (the Gate 10A source-example-id order); a seeded-shuffle policy would be a
    new versioned policy with an explicit owned algorithm, never a framework
    internal."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    ordering: Literal["canonical"] = "canonical"


class TrainingSpec(StrictModel):
    """The complete, content-addressed description of one future training run."""

    schema_version: Literal[1] = 1
    training_spec_version: Literal[1] = 1
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    model: TrainableModelSpec
    tokenizer: TokenizerSpec
    trainer_implementation_id: str = Field(min_length=1)
    precision_policy: Literal["float32", "float16", "bfloat16"] = "float32"
    sequence_policy: SequenceLengthPolicy
    batch: BatchConfig
    optimization: OptimizationConfig
    scheduler: SchedulerConfig
    budget: EpochBudget | StepBudget = Field(discriminator="kind")
    seed_policy: SeedPolicy
    data_order: DataOrderPolicy
    checkpoint_policy: Literal["none"] = "none"  # Gate 10B produces no checkpoints
    training_spec_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingSpec:
        expected = derive_training_spec_id(self)
        if self.training_spec_id != expected:
            raise ValueError("training_spec_id does not match the specification")
        return self


def _spec_id_from_payload(payload: dict[str, object]) -> str:
    return "trainspec-" + sha256_canonical(payload)[:16]


def derive_training_spec_id(spec: TrainingSpec) -> str:
    """Content id over EVERY weight-affecting field (excluding the id itself)."""
    payload = spec.model_dump(mode="json")
    payload.pop("training_spec_id", None)
    return _spec_id_from_payload(payload)


def build_training_spec(
    *,
    training_corpus_id: str,
    training_corpus_digest: str,
    task_id: str,
    model: TrainableModelSpec,
    tokenizer: TokenizerSpec,
    trainer_implementation_id: str,
    sequence_policy: SequenceLengthPolicy,
    batch: BatchConfig,
    optimization: OptimizationConfig,
    scheduler: SchedulerConfig,
    budget: EpochBudget | StepBudget,
    seed_policy: SeedPolicy,
    precision_policy: Literal["float32", "float16", "bfloat16"] = "float32",
    data_order: DataOrderPolicy | None = None,
) -> TrainingSpec:
    """Construct a TrainingSpec with its content-derived, self-validated id."""
    order = data_order or DataOrderPolicy()
    payload: dict[str, object] = {
        "schema_version": 1, "training_spec_version": 1,
        "training_corpus_id": training_corpus_id,
        "training_corpus_digest": training_corpus_digest, "task_id": task_id,
        "model": model.model_dump(mode="json"),
        "tokenizer": tokenizer.model_dump(mode="json"),
        "trainer_implementation_id": trainer_implementation_id,
        "precision_policy": precision_policy,
        "sequence_policy": sequence_policy.model_dump(mode="json"),
        "batch": batch.model_dump(mode="json"),
        "optimization": optimization.model_dump(mode="json"),
        "scheduler": scheduler.model_dump(mode="json"),
        "budget": budget.model_dump(mode="json"),
        "seed_policy": seed_policy.model_dump(mode="json"),
        "data_order": order.model_dump(mode="json"),
        "checkpoint_policy": "none",
    }
    return TrainingSpec(
        training_corpus_id=training_corpus_id,
        training_corpus_digest=training_corpus_digest, task_id=task_id,
        model=model, tokenizer=tokenizer,
        trainer_implementation_id=trainer_implementation_id,
        precision_policy=precision_policy, sequence_policy=sequence_policy,
        batch=batch, optimization=optimization, scheduler=scheduler,
        budget=budget, seed_policy=seed_policy, data_order=order,
        training_spec_id=_spec_id_from_payload(payload),
    )
