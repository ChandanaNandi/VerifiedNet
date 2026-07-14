"""Supervised training corpus: models, leakage audit, pure builder (Gate 10A).

A ``SupervisedTrainingExample`` is three CLEARLY SEPARATED layers: the exact
model input a future trainer will tokenize (rendered from model-visible features
only), the exact supervised target (canonical JSON from the authoritative
accepted label), and non-model audit metadata. The example's identity is a
self-validating content hash binding the rendered input/target to its source
example and the governing policy/templates — so a tampered input, target, or
binding fails at parse time.

``build_training_corpus`` is PURE (no filesystem, network, subprocess, model
execution, randomness, or timestamps). It selects ONLY train-partition
accepted-fault examples with accepted-diagnosis labels, renders inputs/targets
explicitly (allowlist construction, never dump-and-delete), audits every rendered
payload for leakage, sorts deterministically, and fails closed on duplicates or
any policy/template mismatch. Input order never affects output.

Evaluation isolation: this package never imports ``verifiednet.evaluation`` and
never consumes evaluation or benchmark artifacts (ADR-0022, enforced by the AST
boundary guard).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.features import AcceptedLabels, SeparatedDatasetExample
from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition, LeakageSeverity
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.schemas.base import StrictModel
from verifiednet.training.policy import (
    TrainingDataPolicy,
    TrainingInputTemplate,
    TrainingTargetTemplate,
)

TRAINING_CORPUS_VERSION = 1

#: Target JSON may contain exactly these keys — nothing else is authorized.
AUTHORIZED_TARGET_KEYS: frozenset[str] = frozenset({"prediction_type", "fault_family"})

#: Key-like tokens that must never appear in model-visible training text.
FORBIDDEN_INPUT_TOKENS: frozenset[str] = frozenset({
    "example_id", "group_id", "run_id", "run_digest", "source_index_digest",
    "prepared_digest", "dataset_digest", "evaluation_id", "benchmark_id",
    "split_policy_id", "rejection_code", "failed_phase", "outcome_category",
    "correctness", "ranking", "dataset_",
})


class TrainingCorpusError(VerifiedNetError):
    """The training corpus could not be built (eligibility/leakage/duplicate)."""


# ---------------------------------------------------------------------------
# Example models
# ---------------------------------------------------------------------------


class SupervisedTrainingInput(StrictModel):
    """The exact text a future trainer will tokenize (features-derived only)."""

    schema_version: Literal[1] = 1
    text: str = Field(min_length=1)


class SupervisedTrainingTarget(StrictModel):
    """The exact supervised target (canonical JSON from the authoritative label)."""

    schema_version: Literal[1] = 1
    text: str = Field(min_length=1)


class TrainingTraceMetadata(StrictModel):
    """Non-model audit metadata. NEVER enters input or target text.

    Partition is ``Literal["train"]``: a training example bound to any other
    partition cannot be constructed. Corpus-level provenance digests live in the
    manifest, not here, so the per-example files are byte-stable under changes to
    validation/test/abstention examples (the partition-isolation guarantee).
    """

    schema_version: Literal[1] = 1
    source_example_id: str = Field(min_length=1)
    source_group_id: str = Field(min_length=1)
    partition: Literal["train"] = "train"
    example_kind: Literal["accepted_fault"] = "accepted_fault"
    task_id: str = Field(min_length=1)
    training_data_policy_id: str = Field(min_length=1)
    input_template_id: str = Field(min_length=1)
    target_template_id: str = Field(min_length=1)
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    source_schema_version: int = Field(ge=1)


def derive_training_example_id(
    *,
    source_example_id: str,
    task_id: str,
    training_data_policy_id: str,
    input_template_id: str,
    target_template_id: str,
    rendered_input: str,
    rendered_target: str,
) -> str:
    payload = {
        "source_example_id": source_example_id,
        "task_id": task_id,
        "training_data_policy_id": training_data_policy_id,
        "input_template_id": input_template_id,
        "target_template_id": target_template_id,
        "rendered_input": rendered_input,
        "rendered_target": rendered_target,
    }
    return "trainex-" + sha256_canonical(payload)[:24]


class SupervisedTrainingExample(StrictModel):
    """One training example: input + target + trace, identity self-validating."""

    schema_version: Literal[1] = 1
    training_example_id: str = Field(min_length=1)
    input: SupervisedTrainingInput
    target: SupervisedTrainingTarget
    trace: TrainingTraceMetadata

    @model_validator(mode="after")
    def _valid(self) -> SupervisedTrainingExample:
        expected = derive_training_example_id(
            source_example_id=self.trace.source_example_id,
            task_id=self.trace.task_id,
            training_data_policy_id=self.trace.training_data_policy_id,
            input_template_id=self.trace.input_template_id,
            target_template_id=self.trace.target_template_id,
            rendered_input=self.input.text, rendered_target=self.target.text,
        )
        if self.training_example_id != expected:
            raise ValueError("training_example_id does not match the example content")
        return self


# ---------------------------------------------------------------------------
# Training leakage audit
# ---------------------------------------------------------------------------


class TrainingLeakageCode(StrEnum):
    FORBIDDEN_INPUT_KEY = "forbidden_input_key"
    FORBIDDEN_INPUT_VALUE = "forbidden_input_value"
    UNAUTHORIZED_TARGET_FIELD = "unauthorized_target_field"
    MALFORMED_TARGET = "malformed_target"


class TrainingLeakageFinding(StrictModel):
    schema_version: Literal[1] = 1
    code: TrainingLeakageCode
    severity: LeakageSeverity
    detail: str = ""


class TrainingLeakageResult(StrictModel):
    schema_version: Literal[1] = 1
    passed: bool
    findings: tuple[TrainingLeakageFinding, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _fail_closed(self) -> TrainingLeakageResult:
        has_error = any(f.severity is LeakageSeverity.ERROR for f in self.findings)
        if self.passed and has_error:
            raise ValueError("training leakage audit cannot pass with ERROR findings")
        return self

    @property
    def errors(self) -> tuple[TrainingLeakageFinding, ...]:
        return tuple(f for f in self.findings if f.severity is LeakageSeverity.ERROR)


def _err(code: TrainingLeakageCode, detail: str) -> TrainingLeakageFinding:
    return TrainingLeakageFinding(code=code, severity=LeakageSeverity.ERROR, detail=detail)


def audit_training_example(example: SupervisedTrainingExample) -> TrainingLeakageResult:
    """Audit the ACTUAL serialized input and target payloads (fail closed).

    Structural + exact-value checks: forbidden key-like tokens anywhere in the
    input text; the example's own evaluator-only trace values (source example /
    group ids, policy ids) copied verbatim into the input; and a target that must
    be a JSON object carrying exactly the authorized keys with a diagnosis type.
    These checks do NOT prove absence of arbitrary semantic leakage — that is
    bounded by the feature allowlist and the templates, not by this audit.
    """
    findings: list[TrainingLeakageFinding] = []
    text = example.input.text

    for token in sorted(FORBIDDEN_INPUT_TOKENS):
        if token in text:
            findings.append(_err(
                TrainingLeakageCode.FORBIDDEN_INPUT_KEY,
                f"forbidden token {token!r} in model input"))

    trace = example.trace
    forbidden_values = {
        trace.source_example_id, trace.source_group_id,
        trace.training_data_policy_id, trace.feature_policy_id,
        trace.label_policy_id, trace.input_template_id, trace.target_template_id,
    }
    for value in sorted(forbidden_values):
        if value and value in text:
            findings.append(_err(
                TrainingLeakageCode.FORBIDDEN_INPUT_VALUE,
                "evaluator-only value present in model input"))

    try:
        target = json.loads(example.target.text)
    except (json.JSONDecodeError, ValueError):
        findings.append(_err(TrainingLeakageCode.MALFORMED_TARGET, "target is not JSON"))
    else:
        if not isinstance(target, dict):
            findings.append(_err(TrainingLeakageCode.MALFORMED_TARGET,
                                 "target is not a JSON object"))
        else:
            extra = set(target) - AUTHORIZED_TARGET_KEYS
            missing = AUTHORIZED_TARGET_KEYS - set(target)
            if extra:
                findings.append(_err(
                    TrainingLeakageCode.UNAUTHORIZED_TARGET_FIELD,
                    f"unauthorized target fields: {sorted(extra)}"))
            if missing:
                findings.append(_err(
                    TrainingLeakageCode.MALFORMED_TARGET,
                    f"missing target fields: {sorted(missing)}"))
            if target.get("prediction_type") != "diagnosis":
                findings.append(_err(
                    TrainingLeakageCode.MALFORMED_TARGET,
                    "target prediction_type must be 'diagnosis'"))

    has_error = any(f.severity is LeakageSeverity.ERROR for f in findings)
    return TrainingLeakageResult(passed=not has_error, findings=tuple(findings))


# ---------------------------------------------------------------------------
# Corpus identity + pure builder
# ---------------------------------------------------------------------------


def derive_training_corpus_id(
    *,
    task_id: str,
    training_data_policy_id: str,
    input_template_id: str,
    target_template_id: str,
    training_example_ids: tuple[str, ...],
) -> str:
    """Logical corpus identity: configuration + the ordered training examples.

    Deliberately EXCLUDES the source prepared digest so that changes confined to
    validation/test/abstention examples (which never enter training) leave the
    corpus identity unchanged — the partition-isolation guarantee. Provenance
    pins (prepared/dataset digests) live in the manifest.
    """
    payload = {
        "task_id": task_id,
        "training_data_policy_id": training_data_policy_id,
        "input_template_id": input_template_id,
        "target_template_id": target_template_id,
        "training_example_ids": list(training_example_ids),
    }
    return "traincorpus-" + sha256_canonical(payload)[:16]


class TrainingCorpus(StrictModel):
    """The in-memory supervised training corpus (immutable, self-validating)."""

    schema_version: Literal[1] = 1
    training_corpus_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    policy: TrainingDataPolicy
    input_template: TrainingInputTemplate
    target_template: TrainingTargetTemplate
    source_prepared_digest: str = Field(min_length=1)
    source_dataset_digest: str = Field(min_length=1)
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    examples: tuple[SupervisedTrainingExample, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _valid(self) -> TrainingCorpus:
        ids = [e.training_example_id for e in self.examples]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate training_example_id in corpus")
        sources = [e.trace.source_example_id for e in self.examples]
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate source example in corpus")
        if sources != sorted(sources):
            raise ValueError("examples must be ordered by source_example_id")
        for e in self.examples:
            if e.trace.task_id != self.task_id:
                raise ValueError("example task_id does not match corpus task_id")
            if e.trace.training_data_policy_id != self.policy.training_data_policy_id:
                raise ValueError("example policy id does not match corpus policy")
        expected = derive_training_corpus_id(
            task_id=self.task_id,
            training_data_policy_id=self.policy.training_data_policy_id,
            input_template_id=self.input_template.input_template_id,
            target_template_id=self.target_template.target_template_id,
            training_example_ids=tuple(ids),
        )
        if self.training_corpus_id != expected:
            raise ValueError("training_corpus_id does not match the corpus content")
        return self


def _eligible(prepared: LoadedPrepared) -> Iterator[SeparatedDatasetExample]:
    """Yield ONLY train-partition accepted examples (already example-id sorted)."""
    for example in prepared.examples:
        if example.trace.partition is not DatasetPartition.TRAIN:
            continue
        if example.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        yield example


def build_training_corpus(
    prepared: LoadedPrepared,
    *,
    training_data_policy: TrainingDataPolicy,
    input_template: TrainingInputTemplate,
    target_template: TrainingTargetTemplate,
) -> TrainingCorpus:
    """Build the supervised training corpus from the prepared corpus (pure)."""
    manifest = prepared.manifest

    # Policy / template coherence (fail closed on any mismatch).
    if training_data_policy.input_template_id != input_template.input_template_id:
        raise TrainingCorpusError("policy input_template_id does not match the template")
    if training_data_policy.target_template_id != target_template.target_template_id:
        raise TrainingCorpusError("policy target_template_id does not match the template")
    if input_template.task_id != training_data_policy.task_id:
        raise TrainingCorpusError("input template task_id does not match the policy")
    if target_template.task_id != training_data_policy.task_id:
        raise TrainingCorpusError("target template task_id does not match the policy")
    if input_template.feature_policy_id != manifest.feature_policy_id:
        raise TrainingCorpusError(
            "input template supports a different feature policy than the corpus")

    examples: list[SupervisedTrainingExample] = []
    seen_sources: set[str] = set()
    for source in _eligible(prepared):
        labels = source.labels
        if not isinstance(labels, AcceptedLabels):
            raise TrainingCorpusError(
                f"train-partition example {source.trace.example_id} lacks accepted labels")
        if source.trace.example_id in seen_sources:
            raise TrainingCorpusError(
                f"duplicate source example: {source.trace.example_id}")
        seen_sources.add(source.trace.example_id)

        rendered_input = input_template.render(source.features)
        rendered_target = target_template.render(labels.fault_family)
        trace = TrainingTraceMetadata(
            source_example_id=source.trace.example_id,
            source_group_id=source.trace.group_id,
            task_id=training_data_policy.task_id,
            training_data_policy_id=training_data_policy.training_data_policy_id,
            input_template_id=input_template.input_template_id,
            target_template_id=target_template.target_template_id,
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            source_schema_version=source.schema_version,
        )
        example = SupervisedTrainingExample(
            training_example_id=derive_training_example_id(
                source_example_id=trace.source_example_id,
                task_id=trace.task_id,
                training_data_policy_id=trace.training_data_policy_id,
                input_template_id=trace.input_template_id,
                target_template_id=trace.target_template_id,
                rendered_input=rendered_input, rendered_target=rendered_target,
            ),
            input=SupervisedTrainingInput(text=rendered_input),
            target=SupervisedTrainingTarget(text=rendered_target),
            trace=trace,
        )
        audit = audit_training_example(example)
        if not audit.passed:
            codes = ", ".join(sorted({f.code.value for f in audit.errors}))
            raise TrainingCorpusError(f"training leakage detected: {codes}")
        examples.append(example)

    ordered = tuple(sorted(examples, key=lambda e: e.trace.source_example_id))
    corpus_id = derive_training_corpus_id(
        task_id=training_data_policy.task_id,
        training_data_policy_id=training_data_policy.training_data_policy_id,
        input_template_id=input_template.input_template_id,
        target_template_id=target_template.target_template_id,
        training_example_ids=tuple(e.training_example_id for e in ordered),
    )
    return TrainingCorpus(
        training_corpus_id=corpus_id, task_id=training_data_policy.task_id,
        policy=training_data_policy, input_template=input_template,
        target_template=target_template,
        source_prepared_digest=manifest.prepared_digest,
        source_dataset_digest=manifest.source_dataset_digest,
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id, examples=ordered,
    )
