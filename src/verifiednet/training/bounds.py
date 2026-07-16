"""Bounded policies for the FIRST real training run (Gate 10F).

The first genuine weight mutation happens under four explicit, content-
addressed policies — nothing about the run is discretionary at execution time:

* ``BoundedTrainingModelPolicy``   — which ONE model may be trained, how big
                                     it may be, and from where (local only);
* ``BoundedCorpusSlicePolicy``     — which deterministic slice of the verified
                                     Gate 10A corpus feeds the run (first-N in
                                     canonical corpus order; never random,
                                     never informed by evaluation/benchmarks);
* ``TrainingObjectivePolicy``      — the EXACT causal-LM objective: input and
                                     target serialization, separator, special
                                     tokens, label masking, padding, loss
                                     reduction, EOS handling. Changing any of
                                     it changes an identity-bearing policy id;
* ``RealTrainingExecutionPolicy``  — Literal-locked safety bounds (steps,
                                     epochs, examples, sequence, batch), plus
                                     checkpoint-on-completion-only and
                                     no-retry/no-resume.

Everything here is dependency-free and deterministic; no ML import exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel
from verifiednet.training.spec import FORBIDDEN_REVISIONS
from verifiednet.training.store import TrainingPair, load_training_corpus

SLICE_ALGORITHM_V1 = "first-n-canonical-order-v1"


class BoundedTrainingError(VerifiedNetError):
    """A bounded-training policy was violated or could not be applied."""


# ---------------------------------------------------------------------------
# Approved bounded model
# ---------------------------------------------------------------------------


class BoundedTrainingModelPolicy(StrictModel):
    """Which ONE model the first real run may train. Not user-discretionary:
    an arbitrary model cannot be substituted without a new policy id."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    permitted_model_family: Literal["huggingface"] = "huggingface"
    permitted_model_identifier: str = Field(min_length=1)
    permitted_model_revision: str = Field(min_length=1)
    permitted_architecture_class: str = Field(min_length=1)
    permitted_tokenizer_revision: str = Field(min_length=1)
    max_declared_parameter_count: int = Field(ge=1)
    local_cache_only: Literal[True] = True
    permitted_checkpoint_format: Literal["verifiednet.real-checkpoint-v1"] = (
        "verifiednet.real-checkpoint-v1")
    max_sequence_length: int = Field(ge=1)
    max_example_count: int = Field(ge=1)
    max_epochs: int = Field(ge=1)
    max_optimizer_steps: int = Field(ge=1)
    max_effective_batch_size: int = Field(ge=1)
    permitted_device_types: tuple[str, ...] = Field(min_length=1)
    bounded_model_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> BoundedTrainingModelPolicy:
        for revision in (self.permitted_model_revision,
                         self.permitted_tokenizer_revision):
            if revision.strip().lower() in FORBIDDEN_REVISIONS:
                raise ValueError("mutable revisions are never approved")
        devices = list(self.permitted_device_types)
        if devices != sorted(devices) or len(devices) != len(set(devices)):
            raise ValueError("permitted_device_types must be sorted and unique")
        if self.bounded_model_policy_id != derive_bounded_model_policy_id(self):
            raise ValueError(
                "bounded_model_policy_id does not match the policy")
        return self


def derive_bounded_model_policy_id(policy: BoundedTrainingModelPolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("bounded_model_policy_id", None)
    return "bmodel-" + sha256_canonical(payload)[:16]


def build_bounded_model_policy(
    *,
    permitted_model_identifier: str,
    permitted_model_revision: str,
    permitted_architecture_class: str,
    permitted_tokenizer_revision: str,
    max_declared_parameter_count: int,
    max_sequence_length: int,
    max_example_count: int,
    max_epochs: int,
    max_optimizer_steps: int,
    max_effective_batch_size: int,
    permitted_device_types: tuple[str, ...] = ("cpu",),
) -> BoundedTrainingModelPolicy:
    fields: dict[str, object] = {
        "permitted_model_identifier": permitted_model_identifier,
        "permitted_model_revision": permitted_model_revision,
        "permitted_architecture_class": permitted_architecture_class,
        "permitted_tokenizer_revision": permitted_tokenizer_revision,
        "max_declared_parameter_count": max_declared_parameter_count,
        "max_sequence_length": max_sequence_length,
        "max_example_count": max_example_count,
        "max_epochs": max_epochs,
        "max_optimizer_steps": max_optimizer_steps,
        "max_effective_batch_size": max_effective_batch_size,
        "permitted_device_types": tuple(sorted(permitted_device_types)),
    }
    probe = BoundedTrainingModelPolicy.model_construct(**fields)  # type: ignore[arg-type]
    return BoundedTrainingModelPolicy(
        **fields,  # type: ignore[arg-type]
        bounded_model_policy_id=derive_bounded_model_policy_id(probe))


# ---------------------------------------------------------------------------
# Deterministic corpus slice
# ---------------------------------------------------------------------------


class BoundedCorpusSlicePolicy(StrictModel):
    """The deterministic slice of the verified Gate 10A corpus that feeds the
    first real run. Selection is first-N in canonical corpus order — never
    random, never balanced by outcome, never informed by evaluation or
    benchmark artifacts. Selected ids are recorded BEFORE training begins;
    changing the slice changes execution identity and checkpoint lineage."""

    schema_version: Literal[1] = 1
    slice_version: Literal[1] = 1
    source_training_corpus_id: str = Field(min_length=1)
    source_training_corpus_digest: str = Field(min_length=1)
    selection_algorithm: Literal["first-n-canonical-order-v1"] = (
        "first-n-canonical-order-v1")
    ordering_rule: Literal["canonical_corpus_order"] = "canonical_corpus_order"
    max_example_count: int = Field(ge=1)
    selected_training_example_ids: tuple[str, ...] = Field(min_length=1)
    corpus_slice_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> BoundedCorpusSlicePolicy:
        ids = list(self.selected_training_example_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("selected ids must be unique")
        if len(ids) > self.max_example_count:
            raise ValueError("selection exceeds max_example_count")
        if self.corpus_slice_id != derive_corpus_slice_id(self):
            raise ValueError("corpus_slice_id does not match the slice")
        return self


def derive_corpus_slice_id(policy: BoundedCorpusSlicePolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("corpus_slice_id", None)
    return "cslice-" + sha256_canonical(payload)[:16]


def select_corpus_slice(
    corpus_root: str | Path, *, max_example_count: int,
) -> tuple[BoundedCorpusSlicePolicy, tuple[TrainingPair, ...]]:
    """Deterministically select the first N examples in canonical order.

    Verifies the corpus (fail-closed loaders), records the selected ids, and
    returns the trainer-facing PAIRS for exactly those examples in order. No
    randomness; no evaluation/benchmark artifact is consulted or even
    importable from this package.
    """
    loaded = load_training_corpus(corpus_root)
    examples = loaded.examples[:max_example_count]
    if not examples:
        raise BoundedTrainingError("the corpus slice selected zero examples")
    selected_ids = tuple(e.training_example_id for e in examples)
    pairs = tuple(
        TrainingPair(input_text=e.input.text, target_text=e.target.text)
        for e in examples)
    fields: dict[str, object] = {
        "source_training_corpus_id": loaded.manifest.training_corpus_id,
        "source_training_corpus_digest":
            loaded.manifest.training_corpus_digest,
        "max_example_count": max_example_count,
        "selected_training_example_ids": selected_ids,
    }
    probe = BoundedCorpusSlicePolicy.model_construct(**fields)  # type: ignore[arg-type]
    policy = BoundedCorpusSlicePolicy(
        **fields,  # type: ignore[arg-type]
        corpus_slice_id=derive_corpus_slice_id(probe))
    return policy, pairs


# ---------------------------------------------------------------------------
# The exact training objective (causal LM v1)
# ---------------------------------------------------------------------------


class TrainingObjectivePolicy(StrictModel):
    """The EXACT causal-LM objective — nothing is a framework default.

    Serialization: ``input_text + separator + target_text + EOS``. Labels mask
    every input-and-separator token position with ``ignore_index`` so loss is
    computed ONLY on target and EOS positions. Examples are padded on the
    right with the pad token, and padded label positions are also masked.
    Loss reduction is the mean over unmasked positions. No chat template is
    applied — the serialization above IS the contract, consistent with the
    Gate 10A/10B input and target templates.
    """

    schema_version: Literal[1] = 1
    objective_version: Literal[1] = 1
    objective_kind: Literal["causal_lm_full_finetune"] = (
        "causal_lm_full_finetune")
    input_serialization: Literal["training_pair_input_text"] = (
        "training_pair_input_text")
    target_serialization: Literal["training_pair_target_text"] = (
        "training_pair_target_text")
    separator: Literal["\n", ""] = "\n"
    special_vocab_rule: Literal["append_eos_only"] = "append_eos_only"
    label_masking: Literal[
        "mask_input_and_separator", "mask_input_only"] = (
        "mask_input_and_separator")
    ignore_index: Literal[-100] = -100
    padding_rule: Literal["pad_right_mask_labels"] = "pad_right_mask_labels"
    loss_reduction: Literal["mean_over_unmasked"] = "mean_over_unmasked"
    eos_handling: Literal["single_trailing_eos_in_loss"] = (
        "single_trailing_eos_in_loss")
    chat_template: Literal["none"] = "none"
    objective_policy_id: str = Field(min_length=1)

    @property
    def sequence_construction(self) -> str:
        """Explicit generation-boundary contract for executor dispatch.

        ``input_target_eos`` (Gate 17A) supervises the first target token
        under the exact deployed inference prefix — no separator span.
        ``input_separator_target_eos`` (Gate 10F) inserts the single masked
        ``"\\n"`` separator between input and target. This is a derived view
        of the frozen fields, never serialized, so it does not affect the
        content-addressed ``objective_policy_id``.
        """
        return ("input_target_eos"
                if self.label_masking == "mask_input_only"
                else "input_separator_target_eos")

    @model_validator(mode="after")
    def _valid(self) -> TrainingObjectivePolicy:
        # Exactly two coherent boundary configurations are representable: the
        # Gate 10F separator-bearing objective and the Gate 17A boundary-
        # aligned objective. No other (separator, masking) pairing can be
        # constructed — a hidden separator may not coexist with input-only
        # masking, nor input+separator masking with no separator span.
        if self.label_masking == "mask_input_and_separator" and (
                self.separator != "\n"):
            raise ValueError(
                "mask_input_and_separator requires the '\\n' separator")
        if self.label_masking == "mask_input_only" and self.separator != "":
            raise ValueError(
                "mask_input_only forbids a separator (separator must be '')")
        if self.objective_policy_id != derive_objective_policy_id(self):
            raise ValueError("objective_policy_id does not match the policy")
        return self


def derive_objective_policy_id(policy: TrainingObjectivePolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("objective_policy_id", None)
    return "objpol-" + sha256_canonical(payload)[:16]


def build_causal_lm_objective_policy() -> TrainingObjectivePolicy:
    probe = TrainingObjectivePolicy.model_construct()
    return TrainingObjectivePolicy(
        objective_policy_id=derive_objective_policy_id(probe))


def boundary_aligned_objective_policy() -> TrainingObjectivePolicy:
    """Gate 17A: the boundary-aligned causal-LM objective.

    Identical to the Gate 10F objective in every respect EXCEPT that the
    masked ``"\\n"`` separator is removed: sequences are ``input + target +
    EOS`` and loss masks the input span ONLY (target and the single trailing
    EOS are supervised). This makes the supervised first-target-token context
    byte-identical to the frozen deployed inference prompt, which is fed raw
    with no trailing separator (Gate 17 diagnostic: the separator-bearing
    checkpoint emitted immediate EOS on the raw prompt; appending the single
    ``"\\n"`` restored valid JSON). The derived id is deterministic and
    distinct from the separator-bearing ``objpol-e5f36da1a1292f3d``.
    """
    probe = TrainingObjectivePolicy.model_construct(
        separator="", label_masking="mask_input_only")
    return TrainingObjectivePolicy(
        separator="", label_masking="mask_input_only",
        objective_policy_id=derive_objective_policy_id(probe))


def build_causal_lm_example(
    *,
    input_token_ids: tuple[int, ...],
    separator_token_ids: tuple[int, ...],
    target_token_ids: tuple[int, ...],
    eos_token_id: int,
    max_total_tokens: int,
    ignore_index: int = -100,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Pure objective construction: (token_ids, labels) per the v1 policy.

    Labels mask input+separator positions with ``ignore_index``; target and
    the single trailing EOS carry their own ids. Overlength examples FAIL
    CLOSED (the Gate 10B sequence policy's overlength rule) — no silent
    truncation. Pure integers; testable without any tokenizer or framework.
    """
    tokens = (*input_token_ids, *separator_token_ids, *target_token_ids,
              eos_token_id)
    if len(tokens) > max_total_tokens:
        raise BoundedTrainingError(
            f"example length {len(tokens)} exceeds max_total_tokens "
            f"{max_total_tokens}; overlength examples fail closed")
    masked = len(input_token_ids) + len(separator_token_ids)
    labels = ((ignore_index,) * masked, (*target_token_ids, eos_token_id))
    return tokens, (*labels[0], *labels[1])


def build_boundary_aligned_example(
    *,
    input_token_ids: tuple[int, ...],
    target_token_ids: tuple[int, ...],
    eos_token_id: int,
    max_total_tokens: int,
    ignore_index: int = -100,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Gate 17A pure objective construction: ``input + target + EOS``.

    The boundary-aligned counterpart of ``build_causal_lm_example`` with NO
    separator span: there is no separator parameter, so a separator token can
    never enter between the input and the target. Labels mask the input
    positions ONLY (``ignore_index``); the target ids and the single trailing
    EOS carry their own ids. Input and target are supplied already-tokenized
    and independently, so removing the separator never retokenizes either
    side. Overlength examples FAIL CLOSED via the shared core. Pure integers;
    testable without any tokenizer or framework.
    """
    return build_causal_lm_example(
        input_token_ids=input_token_ids,
        separator_token_ids=(),
        target_token_ids=target_token_ids,
        eos_token_id=eos_token_id,
        max_total_tokens=max_total_tokens,
        ignore_index=ignore_index)


# ---------------------------------------------------------------------------
# Real execution policy (Literal-locked safety bounds)
# ---------------------------------------------------------------------------


class RealTrainingExecutionPolicy(StrictModel):
    """Frozen safety contract for the first real run. Hard ceilings are
    Literal-bounded; checkpointing is on-completion only; retries and resume
    do not exist; failure cleanup keeps incomplete outputs marked."""

    schema_version: Literal[1] = 1
    execution_policy_version: Literal[1] = 1
    approved_backend_id: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    bounded_model_policy_id: str = Field(min_length=1)
    corpus_slice_id: str = Field(min_length=1)
    objective_policy_id: str = Field(min_length=1)
    max_runtime_optimizer_steps: int = Field(ge=1, le=64)
    max_epochs: int = Field(ge=1, le=8)
    max_examples: int = Field(ge=1, le=64)
    max_sequence_length: int = Field(ge=1, le=2048)
    max_effective_batch_size: int = Field(ge=1, le=8)
    gradient_clipping_required: Literal[True] = True
    checkpoint_timing: Literal["on_completion_only"] = "on_completion_only"
    max_output_checkpoints: Literal[1] = 1
    failure_cleanup: Literal["mark_incomplete_never_publish"] = (
        "mark_incomplete_never_publish")
    determinism_acceptance: tuple[str, ...] = Field(min_length=1)
    retry_support: Literal["unsupported"] = "unsupported"
    resume_support: Literal["unsupported"] = "unsupported"
    real_execution_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealTrainingExecutionPolicy:
        categories = list(self.determinism_acceptance)
        if categories != sorted(categories) or len(categories) != len(
                set(categories)):
            raise ValueError("determinism_acceptance must be sorted and unique")
        if (self.real_execution_policy_id
                != derive_real_execution_policy_id(self)):
            raise ValueError(
                "real_execution_policy_id does not match the policy")
        return self


def derive_real_execution_policy_id(policy: RealTrainingExecutionPolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("real_execution_policy_id", None)
    return "rexecpol-" + sha256_canonical(payload)[:16]


def build_real_execution_policy(
    *,
    approved_backend_id: str,
    authorization_id: str,
    bounded_model_policy_id: str,
    corpus_slice_id: str,
    objective_policy_id: str,
    max_runtime_optimizer_steps: int,
    max_epochs: int,
    max_examples: int,
    max_sequence_length: int,
    max_effective_batch_size: int,
    determinism_acceptance: tuple[str, ...],
) -> RealTrainingExecutionPolicy:
    fields: dict[str, object] = {
        "approved_backend_id": approved_backend_id,
        "authorization_id": authorization_id,
        "bounded_model_policy_id": bounded_model_policy_id,
        "corpus_slice_id": corpus_slice_id,
        "objective_policy_id": objective_policy_id,
        "max_runtime_optimizer_steps": max_runtime_optimizer_steps,
        "max_epochs": max_epochs,
        "max_examples": max_examples,
        "max_sequence_length": max_sequence_length,
        "max_effective_batch_size": max_effective_batch_size,
        "determinism_acceptance": tuple(sorted(determinism_acceptance)),
    }
    probe = RealTrainingExecutionPolicy.model_construct(**fields)  # type: ignore[arg-type]
    return RealTrainingExecutionPolicy(
        **fields,  # type: ignore[arg-type]
        real_execution_policy_id=derive_real_execution_policy_id(probe))


# ---------------------------------------------------------------------------
# Project-level approved pretrained model record
# ---------------------------------------------------------------------------


class ApprovedTrainingModel(StrictModel):
    """The project's explicit approval record for ONE pretrained model.

    Binds the approved identity, the resolved artifact ids, the bounded-model
    policy, and the reviewed license. Contains NO host facts: no username,
    home path, absolute cache path, timestamp, hostname, or hardware ids —
    the absolute location of the local artifact is runtime evidence only and
    never enters this record.
    """

    schema_version: Literal[1] = 1
    approval_version: Literal[1] = 1
    model_identifier: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    tokenizer_identifier: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    architecture_class: str = Field(min_length=1)
    parameter_count: int = Field(ge=1)
    model_artifact_id: str = Field(min_length=1)
    tokenizer_artifact_id: str = Field(min_length=1)
    bounded_model_policy_id: str = Field(min_length=1)
    license_identifier: str = Field(min_length=1)
    license_review: str = Field(min_length=1)
    local_cache_only: Literal[True] = True
    approval_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ApprovedTrainingModel:
        for revision in (self.model_revision, self.tokenizer_revision):
            if revision.strip().lower() in FORBIDDEN_REVISIONS:
                raise ValueError("mutable revisions are never approved")
        if self.approval_id != derive_model_approval_id(self):
            raise ValueError("approval_id does not match the approval record")
        return self


def derive_model_approval_id(approval: ApprovedTrainingModel) -> str:
    payload = approval.model_dump(mode="json")
    payload.pop("approval_id", None)
    return "modelappr-" + sha256_canonical(payload)[:16]


def build_model_approval(
    *,
    model_identifier: str,
    model_revision: str,
    tokenizer_identifier: str,
    tokenizer_revision: str,
    architecture_class: str,
    parameter_count: int,
    model_artifact_id: str,
    tokenizer_artifact_id: str,
    bounded_model_policy_id: str,
    license_identifier: str,
    license_review: str,
) -> ApprovedTrainingModel:
    fields: dict[str, object] = {
        "model_identifier": model_identifier,
        "model_revision": model_revision,
        "tokenizer_identifier": tokenizer_identifier,
        "tokenizer_revision": tokenizer_revision,
        "architecture_class": architecture_class,
        "parameter_count": parameter_count,
        "model_artifact_id": model_artifact_id,
        "tokenizer_artifact_id": tokenizer_artifact_id,
        "bounded_model_policy_id": bounded_model_policy_id,
        "license_identifier": license_identifier,
        "license_review": license_review,
    }
    probe = ApprovedTrainingModel.model_construct(**fields)  # type: ignore[arg-type]
    return ApprovedTrainingModel(
        **fields,  # type: ignore[arg-type]
        approval_id=derive_model_approval_id(probe))
