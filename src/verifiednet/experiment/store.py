"""The immutable controlled-experiment artifact (Gate 15).

Layout (one directory per experiment)::

    controlled-experiments/<experiment_id>/
      experiment-spec.json      # persisted FIRST (preregistration)
      training-binding.json
      checkpoint-binding.json
      evaluation-bindings.json
      benchmark-binding.json
      paired-summary.json
      reliability-summary.json
      interpretation.json       # the ControlledTrainingExperimentResult
      manifest.json

``preregister_experiment`` writes the spec (plus an ``.INCOMPLETE`` marker
that stays until finalization); ``write_experiment_result`` refuses unless a
byte-identical preregistered spec is already on disk, cross-checks every
binding against the spec and the result, writes the remaining files and the
manifest, and verifies before removing the marker. Large artifacts
(checkpoints, evaluations, benchmarks, comparisons, reliability reports) are
bound by id + digest, never duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import DatasetFileHash
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.comparison import PairedComparisonCounts
from verifiednet.evaluation.structured import ParserStatistics
from verifiednet.experiment.analysis import (
    ExperimentOutcome,
    ExperimentPrimaryMetrics,
    FamilyPairedCounts,
    classify_experiment_outcome,
    success_policy_checks,
)
from verifiednet.experiment.spec import (
    EXPERIMENT_PHASE_SEQUENCE,
    ControlledExperimentError,
    ControlledTrainingExperimentSpec,
    ExperimentPhase,
    ExperimentSuccessPolicy,
)
from verifiednet.schemas.base import StrictModel

EXPERIMENT_GENERATOR = "verifiednet.experiment.store"
SPEC_FILE = "experiment-spec.json"
TRAINING_BINDING_FILE = "training-binding.json"
CHECKPOINT_BINDING_FILE = "checkpoint-binding.json"
EVALUATION_BINDINGS_FILE = "evaluation-bindings.json"
BENCHMARK_BINDING_FILE = "benchmark-binding.json"
PAIRED_SUMMARY_FILE = "paired-summary.json"
RELIABILITY_SUMMARY_FILE = "reliability-summary.json"
INTERPRETATION_FILE = "interpretation.json"
MANIFEST_FILE = "manifest.json"
EXPERIMENT_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPERIMENT_CONTENT_FILES: tuple[str, ...] = (
    BENCHMARK_BINDING_FILE, CHECKPOINT_BINDING_FILE,
    EVALUATION_BINDINGS_FILE, SPEC_FILE, INTERPRETATION_FILE,
    PAIRED_SUMMARY_FILE, RELIABILITY_SUMMARY_FILE, TRAINING_BINDING_FILE)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


# ---------------------------------------------------------------------------
# Phase bindings (small, canonical, id-referencing)
# ---------------------------------------------------------------------------


class TrainingPhaseBinding(StrictModel):
    """What the training phase actually produced, bound by id + digest.

    ``final_state`` is Literal ``completed``: a finalized experiment artifact
    cannot bind a failed or partial run (a failed run preserves its verified
    failed execution and the experiment STOPS as ``experiment_failed`` with
    no finalized store). Loss values are runtime evidence, never quality.
    """

    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    corpus_slice_id: str = Field(min_length=1)
    training_spec_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    training_plan_digest: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    authorization_digest: str = Field(min_length=1)
    bounded_model_policy_id: str = Field(min_length=1)
    objective_policy_id: str = Field(min_length=1)
    real_execution_policy_id: str = Field(min_length=1)
    model_approval_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    execution_digest: str = Field(min_length=1)
    final_state: Literal["completed"] = "completed"
    completed_optimizer_steps: int = Field(ge=1)
    completed_epochs: int = Field(ge=1)
    observed_loss_count: int = Field(ge=1)
    first_observed_loss: str = Field(min_length=1)
    last_observed_loss: str = Field(min_length=1)


class CheckpointBinding(StrictModel):
    """The ONE treatment checkpoint, lineage-checked fail-closed."""

    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    checkpoint_digest: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    real_execution_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    lineage_checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CheckpointBinding:
        if not all(c.passed for c in self.lineage_checks):
            raise ValueError(
                "a checkpoint binding requires every lineage check to pass")
        return self


class EvaluationBindings(StrictModel):
    """All four evaluations of the experiment, bound by id (+ digest for the
    two model predictors whose comparison carries the conclusion)."""

    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    fixed_prior_evaluation_id: str = Field(min_length=1)
    evidence_rule_evaluation_id: str = Field(min_length=1)
    base_baseline_id: str = Field(min_length=1)
    base_evaluation_id: str = Field(min_length=1)
    base_evaluation_digest: str = Field(min_length=1)
    trained_baseline_id: str = Field(min_length=1)
    trained_evaluation_id: str = Field(min_length=1)
    trained_evaluation_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationBindings:
        ids = [self.fixed_prior_evaluation_id,
               self.evidence_rule_evaluation_id, self.base_evaluation_id,
               self.trained_evaluation_id]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation ids must be distinct")
        if self.base_baseline_id == self.trained_baseline_id:
            raise ValueError("base and trained predictors must be distinct")
        return self


class BenchmarkRankingRow(StrictModel):
    schema_version: Literal[1] = 1
    predictor_identifier: str = Field(min_length=1)
    rank: int = Field(ge=1)


class BenchmarkBinding(StrictModel):
    """The unchanged Gate 9 benchmark, bound and explicitly descriptive."""

    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    benchmark_id: str = Field(min_length=1)
    benchmark_digest: str = Field(min_length=1)
    ranking: tuple[BenchmarkRankingRow, ...] = Field(min_length=1)
    descriptive_only: Literal[True] = True

    @model_validator(mode="after")
    def _valid(self) -> BenchmarkBinding:
        ranks = [row.rank for row in self.ranking]
        if ranks != sorted(ranks):
            raise ValueError("ranking rows must be rank-ordered")
        identifiers = [row.predictor_identifier for row in self.ranking]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ranking identifiers must be unique")
        return self


class PairedSummary(StrictModel):
    """Paired evidence: all-partition, non-train, test-only, per-family."""

    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    comparison_id: str = Field(min_length=1)
    comparison_digest: str = Field(min_length=1)
    interpretation_conclusion: str = Field(min_length=1)
    counts_all: PairedComparisonCounts
    counts_non_train: PairedComparisonCounts
    counts_test: PairedComparisonCounts
    family_test_counts: tuple[FamilyPairedCounts, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> PairedSummary:
        families = [f.fault_family for f in self.family_test_counts]
        if families != sorted(families) or len(families) != len(set(families)):
            raise ValueError("family counts must be family-sorted and unique")
        return self


class ReliabilitySummary(StrictModel):
    """Gate 13 structured-output reliability for BOTH model predictors —
    diagnostics only, never ranked on."""

    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    report_id: str = Field(min_length=1)
    report_digest: str = Field(min_length=1)
    base: ParserStatistics
    trained: ParserStatistics

    @model_validator(mode="after")
    def _valid(self) -> ReliabilitySummary:
        if self.base.baseline_id == self.trained.baseline_id:
            raise ValueError("base and trained statistics must be distinct")
        return self


# ---------------------------------------------------------------------------
# The experiment result (outcome unrepresentable unless the counts support it)
# ---------------------------------------------------------------------------


class ControlledTrainingExperimentResult(StrictModel):
    """One authoritative, self-validating result for ONE experiment.

    The outcome and every success check are RE-DERIVED from the recorded raw
    counts under the embedded frozen success policy — a result claiming
    ``improved`` without satisfying every criterion is unrepresentable.
    The phase log must be the COMPLETE canonical sequence.
    """

    schema_version: Literal[1] = 1
    result_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    training_plan_digest: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    authorization_digest: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    execution_digest: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    checkpoint_digest: str = Field(min_length=1)
    base_evaluation_id: str = Field(min_length=1)
    base_evaluation_digest: str = Field(min_length=1)
    trained_evaluation_id: str = Field(min_length=1)
    trained_evaluation_digest: str = Field(min_length=1)
    benchmark_id: str = Field(min_length=1)
    benchmark_digest: str = Field(min_length=1)
    comparison_id: str = Field(min_length=1)
    comparison_digest: str = Field(min_length=1)
    reliability_report_id: str = Field(min_length=1)
    success_policy: ExperimentSuccessPolicy
    metrics: ExperimentPrimaryMetrics
    success_checks: tuple[DatasetCheck, ...] = Field(min_length=1)
    outcome: ExperimentOutcome
    qualifiers: tuple[str, ...] = Field(default_factory=tuple)
    phases: tuple[ExperimentPhase, ...] = Field(min_length=1)
    experiment_result_id: str = Field(min_length=1)
    experiment_result_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ControlledTrainingExperimentResult:
        if self.phases != EXPERIMENT_PHASE_SEQUENCE:
            raise ValueError(
                "a finalized result requires the complete canonical phase "
                "sequence")
        if self.success_checks != success_policy_checks(
                self.metrics, self.success_policy):
            raise ValueError(
                "success checks do not match the recorded counts")
        expected_outcome, _reasons = classify_experiment_outcome(
            self.metrics, self.success_policy)
        if self.outcome != expected_outcome:
            raise ValueError(
                f"outcome {self.outcome!r} does not follow from the recorded "
                f"counts (expected {expected_outcome!r})")
        if list(self.qualifiers) != sorted(set(self.qualifiers)):
            raise ValueError("qualifiers must be sorted and unique")
        if self.experiment_result_id != derive_experiment_result_id(self):
            raise ValueError("experiment_result_id does not match the result")
        if self.experiment_result_digest != \
                compute_experiment_result_digest(self):
            raise ValueError(
                "experiment_result_digest does not match the result")
        return self


def derive_experiment_result_id(
    result: ControlledTrainingExperimentResult,
) -> str:
    payload = result.model_dump(mode="json")
    payload.pop("experiment_result_id", None)
    payload.pop("experiment_result_digest", None)
    return "expres-" + sha256_canonical(payload)[:16]


def compute_experiment_result_digest(
    result: ControlledTrainingExperimentResult,
) -> str:
    payload = result.model_dump(mode="json")
    payload.pop("experiment_result_digest", None)
    return "expresdig-" + sha256_canonical(payload)[:24]


def build_experiment_result(
    *,
    spec: ControlledTrainingExperimentSpec,
    training: TrainingPhaseBinding,
    checkpoint: CheckpointBinding,
    evaluations: EvaluationBindings,
    benchmark: BenchmarkBinding,
    paired: PairedSummary,
    reliability: ReliabilitySummary,
    metrics: ExperimentPrimaryMetrics,
    qualifiers: tuple[str, ...] = (),
) -> ControlledTrainingExperimentResult:
    """Assemble the result from the phase bindings (explicit kwargs; the
    validators re-derive checks, outcome, id, and digest)."""
    outcome, _reasons = classify_experiment_outcome(
        metrics, spec.success_policy)
    checks = success_policy_checks(metrics, spec.success_policy)
    fields: dict[str, object] = {
        "experiment_id": spec.experiment_id,
        "training_corpus_id": training.training_corpus_id,
        "training_corpus_digest": training.training_corpus_digest,
        "training_plan_id": training.training_plan_id,
        "training_plan_digest": training.training_plan_digest,
        "authorization_id": training.authorization_id,
        "authorization_digest": training.authorization_digest,
        "execution_id": training.execution_id,
        "execution_digest": training.execution_digest,
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_digest": checkpoint.checkpoint_digest,
        "base_evaluation_id": evaluations.base_evaluation_id,
        "base_evaluation_digest": evaluations.base_evaluation_digest,
        "trained_evaluation_id": evaluations.trained_evaluation_id,
        "trained_evaluation_digest": evaluations.trained_evaluation_digest,
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_digest": benchmark.benchmark_digest,
        "comparison_id": paired.comparison_id,
        "comparison_digest": paired.comparison_digest,
        "reliability_report_id": reliability.report_id,
        "success_policy": spec.success_policy,
        "metrics": metrics,
        "success_checks": checks,
        "outcome": outcome,
        "qualifiers": tuple(sorted(set(qualifiers))),
        "phases": EXPERIMENT_PHASE_SEQUENCE,
    }
    probe = ControlledTrainingExperimentResult.model_construct(**fields)  # type: ignore[arg-type]
    result_id = derive_experiment_result_id(probe)
    probe_with_id = ControlledTrainingExperimentResult.model_construct(
        **fields, experiment_result_id=result_id)  # type: ignore[arg-type]
    return ControlledTrainingExperimentResult(
        **fields,  # type: ignore[arg-type]
        experiment_result_id=result_id,
        experiment_result_digest=compute_experiment_result_digest(
            probe_with_id))


# ---------------------------------------------------------------------------
# Manifest + store
# ---------------------------------------------------------------------------


def _experiment_digest(
    *,
    experiment_id: str,
    outcome: str,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    payload = {
        "experiment_id": experiment_id,
        "outcome": outcome,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256,
             "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)],
    }
    return "expdig-" + sha256_canonical(payload)[:24]


class ControlledExperimentManifest(StrictModel):
    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    outcome: ExperimentOutcome
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    experiment_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ControlledExperimentManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        if set(paths) != set(EXPERIMENT_CONTENT_FILES):
            raise ValueError("manifest files do not match the declared layout")
        if self.experiment_digest != _experiment_digest(
                experiment_id=self.experiment_id, outcome=self.outcome,
                generated_by=self.generated_by, files=self.files):
            raise ValueError("experiment_digest does not match the content")
        return self


@dataclass(frozen=True)
class WrittenPreregistration:
    root: Path
    experiment_id: str
    spec_sha256: str


@dataclass(frozen=True)
class WrittenControlledExperiment:
    root: Path
    experiment_id: str
    experiment_result_id: str
    experiment_digest: str
    outcome: str


def preregister_experiment(
    spec: ControlledTrainingExperimentSpec, experiments_root: str | Path,
) -> WrittenPreregistration:
    """Persist the spec BEFORE any training exists; never overwrite."""
    root = Path(experiments_root) / spec.experiment_id
    if root.exists() and any(root.iterdir()):
        raise ControlledExperimentError(
            f"experiment already preregistered: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / EXPERIMENT_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    payload = canonical_json_bytes(spec)
    atomic_write_bytes(root / SPEC_FILE, payload)
    fsync_dir(root)
    return WrittenPreregistration(
        root=root, experiment_id=spec.experiment_id,
        spec_sha256=sha256_bytes(payload))


def _cross_checks(
    spec: ControlledTrainingExperimentSpec,
    training: TrainingPhaseBinding,
    checkpoint: CheckpointBinding,
    evaluations: EvaluationBindings,
    benchmark: BenchmarkBinding,
    paired: PairedSummary,
    reliability: ReliabilitySummary,
    result: ControlledTrainingExperimentResult,
) -> tuple[DatasetCheck, ...]:
    """Every binding agrees with the spec and the result — fail closed."""
    checks: list[DatasetCheck] = []
    for name, binding_id in (
            ("training", training.experiment_id),
            ("checkpoint", checkpoint.experiment_id),
            ("evaluations", evaluations.experiment_id),
            ("benchmark", benchmark.experiment_id),
            ("paired", paired.experiment_id),
            ("reliability", reliability.experiment_id),
            ("result", result.experiment_id)):
        checks.append(_c(f"{name}_binds_experiment",
                         binding_id == spec.experiment_id, binding_id))
    checks.append(_c(
        "training_matches_preregistration",
        training.training_corpus_id == spec.training_corpus_id
        and training.training_corpus_digest == spec.training_corpus_digest
        and training.training_spec_id == spec.training_spec_id
        and training.training_plan_id == spec.training_plan_id
        and training.training_plan_digest == spec.training_plan_digest
        and training.bounded_model_policy_id == spec.bounded_model_policy_id
        and training.objective_policy_id == spec.objective_policy_id
        and training.model_approval_id == spec.model_approval_id))
    checks.append(_c(
        "training_within_runtime_envelope",
        training.completed_optimizer_steps
        <= spec.runtime_envelope.max_optimizer_steps
        and training.completed_epochs <= spec.runtime_envelope.max_epochs))
    checks.append(_c(
        "checkpoint_binds_this_training",
        checkpoint.real_execution_id == training.execution_id
        and checkpoint.training_plan_id == training.training_plan_id
        and checkpoint.training_corpus_id == training.training_corpus_id))
    checks.append(_c(
        "result_binds_the_bindings",
        result.training_corpus_id == training.training_corpus_id
        and result.execution_id == training.execution_id
        and result.execution_digest == training.execution_digest
        and result.checkpoint_id == checkpoint.checkpoint_id
        and result.checkpoint_digest == checkpoint.checkpoint_digest
        and result.base_evaluation_id == evaluations.base_evaluation_id
        and result.trained_evaluation_id == evaluations.trained_evaluation_id
        and result.benchmark_id == benchmark.benchmark_id
        and result.benchmark_digest == benchmark.benchmark_digest
        and result.comparison_id == paired.comparison_id
        and result.comparison_digest == paired.comparison_digest
        and result.reliability_report_id == reliability.report_id))
    checks.append(_c(
        "result_policy_matches_preregistration",
        result.success_policy == spec.success_policy))
    checks.append(_c(
        "paired_counts_match_result_metrics",
        paired.counts_test.base_incorrect_trained_correct
        == result.metrics.test_base_incorrect_trained_correct
        and paired.counts_test.base_correct_trained_incorrect
        == result.metrics.test_base_correct_trained_incorrect
        and paired.counts_test.predictions_differed
        == result.metrics.test_predictions_differed))
    checks.append(_c(
        "reliability_counts_match_result_metrics",
        reliability.base.invalid_predictions
        == result.metrics.base_invalid_predictions
        and reliability.trained.invalid_predictions
        == result.metrics.trained_invalid_predictions
        and reliability.base.evaluation_id
        == evaluations.base_evaluation_id
        and reliability.trained.evaluation_id
        == evaluations.trained_evaluation_id))
    checks.append(_c(
        "benchmark_covers_both_model_predictors",
        {evaluations.base_baseline_id, evaluations.trained_baseline_id}
        <= {row.predictor_identifier for row in benchmark.ranking}))
    return tuple(checks)


def write_experiment_result(
    *,
    spec: ControlledTrainingExperimentSpec,
    training: TrainingPhaseBinding,
    checkpoint: CheckpointBinding,
    evaluations: EvaluationBindings,
    benchmark: BenchmarkBinding,
    paired: PairedSummary,
    reliability: ReliabilitySummary,
    result: ControlledTrainingExperimentResult,
    experiments_root: str | Path,
) -> WrittenControlledExperiment:
    """Finalize ONE preregistered experiment; fail closed everywhere.

    Refuses when: the experiment was never preregistered; the persisted spec
    bytes differ from the given spec (post-hoc modification); the experiment
    was already finalized; or any cross-check between spec, bindings, and
    result fails.
    """
    root = Path(experiments_root) / spec.experiment_id
    spec_path = root / SPEC_FILE
    if not spec_path.is_file():
        raise ControlledExperimentError(
            "experiment was never preregistered; refusing to finalize")
    if spec_path.read_bytes() != canonical_json_bytes(spec):
        raise ControlledExperimentError(
            "the persisted preregistration differs from the given "
            "specification; a spec is never modified after persistence")
    if (root / MANIFEST_FILE).exists():
        raise ControlledExperimentError(
            f"experiment already finalized: {root}")
    failures = [c for c in _cross_checks(
        spec, training, checkpoint, evaluations, benchmark, paired,
        reliability, result) if not c.passed]
    if failures:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in failures)
        raise ControlledExperimentError(
            f"experiment cross-checks failed: {detail}")

    content: dict[str, bytes] = {
        TRAINING_BINDING_FILE: canonical_json_bytes(training),
        CHECKPOINT_BINDING_FILE: canonical_json_bytes(checkpoint),
        EVALUATION_BINDINGS_FILE: canonical_json_bytes(evaluations),
        BENCHMARK_BINDING_FILE: canonical_json_bytes(benchmark),
        PAIRED_SUMMARY_FILE: canonical_json_bytes(paired),
        RELIABILITY_SUMMARY_FILE: canonical_json_bytes(reliability),
        INTERPRETATION_FILE: canonical_json_bytes(result),
    }
    marker = root / EXPERIMENT_INCOMPLETE_MARKER
    if not marker.exists():
        marker.write_bytes(b"incomplete\n")
        fsync_dir(root)
    for name, payload in content.items():
        atomic_write_bytes(root / name, payload)
    spec_payload = spec_path.read_bytes()
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in
         {**content, SPEC_FILE: spec_payload}.items()),
        key=lambda f: f.relative_path))
    manifest = ControlledExperimentManifest(
        experiment_id=spec.experiment_id, outcome=result.outcome,
        generated_by=EXPERIMENT_GENERATOR, files=files,
        experiment_digest=_experiment_digest(
            experiment_id=spec.experiment_id, outcome=result.outcome,
            generated_by=EXPERIMENT_GENERATOR, files=files))
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(manifest))
    verification = verify_controlled_experiment(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise ControlledExperimentError(
            f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenControlledExperiment(
        root=root, experiment_id=spec.experiment_id,
        experiment_result_id=result.experiment_result_id,
        experiment_digest=manifest.experiment_digest,
        outcome=result.outcome)


class ExperimentVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    experiment_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def verify_controlled_experiment(
    experiment_dir: str | Path,
) -> ExperimentVerificationResult:
    """Verify the finalized experiment artifact; fail closed."""
    root = Path(experiment_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("experiment_dir_present", False, str(root)))
        return ExperimentVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("experiment_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / EXPERIMENT_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return ExperimentVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = ControlledExperimentManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return ExperimentVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))

    on_disk = {str(p.relative_to(root)) for p in root.rglob("*")
               if p.is_file() and p.name != EXPERIMENT_INCOMPLETE_MARKER}
    allowed = set(EXPERIMENT_CONTENT_FILES) | {MANIFEST_FILE}
    checks.append(_c("no_missing_files", not sorted(allowed - on_disk),
                     ",".join(sorted(allowed - on_disk))))
    checks.append(_c("no_unexpected_files", not sorted(on_disk - allowed),
                     ",".join(sorted(on_disk - allowed))))

    hash_ok, detail = True, ""
    for fh in manifest.files:
        path = root / fh.relative_path
        if not path.is_file():
            hash_ok, detail = False, f"missing {fh.relative_path}"
            break
        raw = path.read_bytes()
        if len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok, detail = False, f"mismatch for {fh.relative_path}"
            break
    checks.append(_c("file_hashes_match", hash_ok, detail))
    if not hash_ok:
        return ExperimentVerificationResult(
            verified=False, experiment_digest=manifest.experiment_digest,
            checks=tuple(checks))

    parse_ok = True
    try:
        spec = ControlledTrainingExperimentSpec.model_validate_json(
            (root / SPEC_FILE).read_bytes())
        training = TrainingPhaseBinding.model_validate_json(
            (root / TRAINING_BINDING_FILE).read_bytes())
        ckpt = CheckpointBinding.model_validate_json(
            (root / CHECKPOINT_BINDING_FILE).read_bytes())
        evaluations = EvaluationBindings.model_validate_json(
            (root / EVALUATION_BINDINGS_FILE).read_bytes())
        benchmark = BenchmarkBinding.model_validate_json(
            (root / BENCHMARK_BINDING_FILE).read_bytes())
        paired = PairedSummary.model_validate_json(
            (root / PAIRED_SUMMARY_FILE).read_bytes())
        reliability = ReliabilitySummary.model_validate_json(
            (root / RELIABILITY_SUMMARY_FILE).read_bytes())
        result = ControlledTrainingExperimentResult.model_validate_json(
            (root / INTERPRETATION_FILE).read_bytes())
    except ValidationError as exc:
        parse_ok = False
        checks.append(_c("content_parses", False, str(exc).splitlines()[0]))
    if not parse_ok:
        return ExperimentVerificationResult(
            verified=False, experiment_digest=manifest.experiment_digest,
            checks=tuple(checks))
    checks.append(_c("content_parses", True))
    checks.append(_c("manifest_binds_experiment",
                     manifest.experiment_id == spec.experiment_id
                     and manifest.outcome == result.outcome))
    checks.append(_c("directory_matches_experiment_id",
                     root.name == spec.experiment_id))
    checks.extend(_cross_checks(spec, training, ckpt, evaluations, benchmark,
                                paired, reliability, result))
    return ExperimentVerificationResult(
        verified=all(c.passed for c in checks),
        experiment_digest=manifest.experiment_digest, checks=tuple(checks))


@dataclass(frozen=True)
class LoadedControlledExperiment:
    spec: ControlledTrainingExperimentSpec
    training: TrainingPhaseBinding
    checkpoint: CheckpointBinding
    evaluations: EvaluationBindings
    benchmark: BenchmarkBinding
    paired: PairedSummary
    reliability: ReliabilitySummary
    result: ControlledTrainingExperimentResult
    manifest: ControlledExperimentManifest


def read_controlled_experiment(
    experiment_dir: str | Path,
) -> LoadedControlledExperiment:
    """Verify then reconstruct a finalized experiment; fail closed."""
    root = Path(experiment_dir)
    verification = verify_controlled_experiment(root)
    if not verification.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}"
                           for c in verification.failures)
        raise ControlledExperimentError(
            f"controlled experiment failed verification: {detail}")
    return LoadedControlledExperiment(
        spec=ControlledTrainingExperimentSpec.model_validate_json(
            (root / SPEC_FILE).read_bytes()),
        training=TrainingPhaseBinding.model_validate_json(
            (root / TRAINING_BINDING_FILE).read_bytes()),
        checkpoint=CheckpointBinding.model_validate_json(
            (root / CHECKPOINT_BINDING_FILE).read_bytes()),
        evaluations=EvaluationBindings.model_validate_json(
            (root / EVALUATION_BINDINGS_FILE).read_bytes()),
        benchmark=BenchmarkBinding.model_validate_json(
            (root / BENCHMARK_BINDING_FILE).read_bytes()),
        paired=PairedSummary.model_validate_json(
            (root / PAIRED_SUMMARY_FILE).read_bytes()),
        reliability=ReliabilitySummary.model_validate_json(
            (root / RELIABILITY_SUMMARY_FILE).read_bytes()),
        result=ControlledTrainingExperimentResult.model_validate_json(
            (root / INTERPRETATION_FILE).read_bytes()),
        manifest=ControlledExperimentManifest.model_validate_json(
            (root / MANIFEST_FILE).read_bytes()))
