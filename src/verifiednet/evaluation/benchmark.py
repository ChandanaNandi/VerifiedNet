"""Multi-predictor benchmark framework (Gate 9).

The benchmark COMPARES predictors; it never changes evaluation. Every predictor
runs under identical conditions — the same task, prompt/feature contract, scoring,
normalization, and feature policy — through the unchanged Gate 7 engine
(``evaluate_prepared_corpus``). The benchmark consumes the resulting evaluation
runs and emits deterministic, immutable comparison + ranking artifacts.

Determinism: predictors are evaluated in sorted-identifier order, comparison rows
and the benchmark id/digest are order-independent (predictor identifiers are
canonicalised — sorted — before hashing), and ranking is a pure, fully-tie-broken
function. Nothing here mutates a predictor, an evaluation artifact, or any earlier
stage; there are no timestamps, machine identifiers, or runtime durations in any
immutable artifact.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import DatasetFileHash
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.baseline import Baseline
from verifiednet.evaluation.contract import EvaluationTask
from verifiednet.evaluation.engine import EvaluationRun, evaluate_prepared_corpus
from verifiednet.evaluation.scoring import OutcomeCategory, ratio_str
from verifiednet.schemas.base import StrictModel

BENCHMARK_VERSION = 1
BENCHMARK_FORMAT_VERSION = 1
BENCHMARK_GENERATOR = "verifiednet.evaluation.benchmark"

MANIFEST_FILE = "manifest.json"
COMPARISON_FILE = "comparison.json"
RANKING_FILE = "ranking.json"
BENCHMARK_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPECTED_BENCHMARK_FILES: frozenset[str] = frozenset({COMPARISON_FILE, RANKING_FILE})
SUPPORTED_BENCHMARK_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_BENCHMARK_FORMAT: frozenset[int] = frozenset({1})


class BenchmarkError(VerifiedNetError):
    """A benchmark could not be built, written, or read."""


# ---------------------------------------------------------------------------
# Spec + comparison + ranking models
# ---------------------------------------------------------------------------


def derive_benchmark_id(
    *,
    benchmark_version: int,
    benchmark_name: str,
    task_id: str,
    prepared_digest: str,
    predictor_identifiers: tuple[str, ...],
    normalization_policy_id: str,
    scoring_policy_version: int,
) -> str:
    payload = {
        "benchmark_version": benchmark_version,
        "benchmark_name": benchmark_name,
        "task_id": task_id,
        "prepared_digest": prepared_digest,
        "predictor_identifiers": sorted(predictor_identifiers),
        "normalization_policy_id": normalization_policy_id,
        "scoring_policy_version": scoring_policy_version,
    }
    return "bench-" + sha256_canonical(payload)[:16]


class BenchmarkSpec(StrictModel):
    """The frozen, content-addressed definition of one benchmark."""

    schema_version: Literal[1] = 1
    benchmark_version: Literal[1] = 1
    benchmark_name: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    prepared_digest: str = Field(min_length=1)
    predictor_identifiers: tuple[str, ...] = Field(min_length=1)
    normalization_policy_id: str = Field(min_length=1)
    scoring_policy_version: int = Field(ge=1)
    benchmark_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> BenchmarkSpec:
        ids = list(self.predictor_identifiers)
        if ids != sorted(ids):
            raise ValueError("predictor_identifiers must be sorted")
        if len(ids) != len(set(ids)):
            raise ValueError("predictor_identifiers must be unique")
        expected = derive_benchmark_id(
            benchmark_version=self.benchmark_version, benchmark_name=self.benchmark_name,
            task_id=self.task_id, prepared_digest=self.prepared_digest,
            predictor_identifiers=self.predictor_identifiers,
            normalization_policy_id=self.normalization_policy_id,
            scoring_policy_version=self.scoring_policy_version,
        )
        if self.benchmark_id != expected:
            raise ValueError("benchmark_id does not match the benchmark specification")
        return self


class ComparisonRow(StrictModel):
    """One predictor's comparable, deterministic metrics."""

    schema_version: Literal[1] = 1
    predictor_identifier: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    accepted_evaluated: int = Field(ge=0)
    accepted_correct: int = Field(ge=0)
    exact_match_accuracy: str | None = None
    abstention_count: int = Field(ge=0)
    abstention_correct: int = Field(ge=0)
    abstention_accuracy: str | None = None
    invalid_prediction_count: int = Field(ge=0)
    evaluation_count: int = Field(ge=0)


class RankingEntry(StrictModel):
    """One predictor's rank (1-based) under the documented tie-break."""

    schema_version: Literal[1] = 1
    rank: int = Field(ge=1)
    predictor_identifier: str = Field(min_length=1)
    exact_match_accuracy: str | None = None
    abstention_accuracy: str | None = None
    invalid_prediction_count: int = Field(ge=0)


def _acc(value: str | None) -> Decimal:
    """Ordering key for an accuracy string; ``None`` sorts lowest."""
    return Decimal("-1") if value is None else Decimal(value)


def compute_comparison_row(run: EvaluationRun) -> ComparisonRow:
    """Derive one predictor's comparison row from its evaluation run."""
    accepted_evaluated = sum(m.evaluated for m in run.metrics.accepted_partitions)
    accepted_correct = sum(m.correct for m in run.metrics.accepted_partitions)
    invalid = sum(1 for r in run.records
                  if r.outcome_category is OutcomeCategory.INVALID_PREDICTION)
    return ComparisonRow(
        predictor_identifier=run.baseline_spec.baseline_id,
        evaluation_id=run.evaluation_id,
        accepted_evaluated=accepted_evaluated, accepted_correct=accepted_correct,
        exact_match_accuracy=ratio_str(accepted_correct, accepted_evaluated),
        abstention_count=run.metrics.abstention.count,
        abstention_correct=run.metrics.abstention.correct,
        abstention_accuracy=run.metrics.abstention.abstention_accuracy,
        invalid_prediction_count=invalid, evaluation_count=len(run.records),
    )


def compute_ranking(comparison: tuple[ComparisonRow, ...]) -> tuple[RankingEntry, ...]:
    """Deterministic ranking.

    Tie-break order (each a strict comparison, so ranks form a total order):
    1. accepted diagnosis accuracy, descending (``None`` lowest);
    2. abstention accuracy, descending (``None`` lowest);
    3. invalid-prediction count, ascending (fewer is better);
    4. predictor identifier, ascending (stable final tie-break).
    """
    ordered = sorted(
        comparison,
        key=lambda r: (
            -_acc(r.exact_match_accuracy), -_acc(r.abstention_accuracy),
            r.invalid_prediction_count, r.predictor_identifier,
        ),
    )
    return tuple(
        RankingEntry(
            rank=i + 1, predictor_identifier=row.predictor_identifier,
            exact_match_accuracy=row.exact_match_accuracy,
            abstention_accuracy=row.abstention_accuracy,
            invalid_prediction_count=row.invalid_prediction_count,
        )
        for i, row in enumerate(ordered)
    )


@dataclass(frozen=True)
class BenchmarkResult:
    """The in-memory outcome of one benchmark (evaluations + comparison + ranking)."""

    spec: BenchmarkSpec
    evaluation_runs: tuple[EvaluationRun, ...]
    comparison: tuple[ComparisonRow, ...]
    ranking: tuple[RankingEntry, ...]


def run_benchmark(
    prepared: LoadedPrepared,
    *,
    task: EvaluationTask,
    predictors: Sequence[Baseline],
    benchmark_name: str = "multi_predictor_diagnosis",
) -> BenchmarkResult:
    """Evaluate every predictor under identical conditions; compare and rank.

    Fails closed on an empty predictor set, a duplicate predictor identifier, or a
    predictor built for a different task. Predictor execution order does not affect
    the result (evaluation is sorted by identifier).
    """
    if not predictors:
        raise BenchmarkError("a benchmark needs at least one predictor")
    ordered = sorted(predictors, key=lambda p: p.spec.baseline_id)
    identifiers = [p.spec.baseline_id for p in ordered]
    if len(identifiers) != len(set(identifiers)):
        raise BenchmarkError("duplicate predictor identifier in the benchmark set")
    for predictor in ordered:
        if predictor.spec.task_id != task.task_id:
            raise BenchmarkError(
                f"predictor {predictor.spec.baseline_id} was built for a different task"
            )

    runs = tuple(evaluate_prepared_corpus(prepared, p, task) for p in ordered)
    comparison = tuple(sorted(
        (compute_comparison_row(r) for r in runs),
        key=lambda row: row.predictor_identifier,
    ))
    ranking = compute_ranking(comparison)

    spec = BenchmarkSpec(
        benchmark_name=benchmark_name, task_id=task.task_id,
        prepared_digest=prepared.manifest.prepared_digest,
        predictor_identifiers=tuple(sorted(identifiers)),
        normalization_policy_id=task.normalization.policy_id,
        scoring_policy_version=task.scoring_policy_version,
        benchmark_id=derive_benchmark_id(
            benchmark_version=BENCHMARK_VERSION, benchmark_name=benchmark_name,
            task_id=task.task_id, prepared_digest=prepared.manifest.prepared_digest,
            predictor_identifiers=tuple(identifiers),
            normalization_policy_id=task.normalization.policy_id,
            scoring_policy_version=task.scoring_policy_version,
        ),
    )
    return BenchmarkResult(
        spec=spec, evaluation_runs=runs, comparison=comparison, ranking=ranking
    )


# ---------------------------------------------------------------------------
# Immutable persistence
# ---------------------------------------------------------------------------


class ComparisonFile(StrictModel):
    schema_version: Literal[1] = 1
    comparison: tuple[ComparisonRow, ...] = Field(default_factory=tuple)


class RankingFile(StrictModel):
    schema_version: Literal[1] = 1
    ranking: tuple[RankingEntry, ...] = Field(default_factory=tuple)


def compute_benchmark_digest(
    *,
    schema_version: int,
    benchmark_format_version: int,
    benchmark_id: str,
    task_id: str,
    prepared_digest: str,
    predictor_identifiers: tuple[str, ...],
    evaluation_identifiers: tuple[str, ...],
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    """Non-recursive digest over the benchmark spec + eval ids + content files."""
    payload = {
        "schema_version": schema_version,
        "benchmark_format_version": benchmark_format_version,
        "benchmark_id": benchmark_id,
        "task_id": task_id,
        "prepared_digest": prepared_digest,
        "predictor_identifiers": sorted(predictor_identifiers),
        "evaluation_identifiers": sorted(evaluation_identifiers),
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "benchdig-" + sha256_canonical(payload)[:24]


class BenchmarkManifest(StrictModel):
    """The immutable manifest of a persisted benchmark (self-validating digest)."""

    schema_version: Literal[1] = 1
    benchmark_format_version: Literal[1] = 1
    benchmark_id: str = Field(min_length=1)
    spec: BenchmarkSpec
    task_id: str = Field(min_length=1)
    prepared_digest: str = Field(min_length=1)
    predictor_identifiers: tuple[str, ...] = Field(min_length=1)
    evaluation_identifiers: tuple[str, ...] = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(default_factory=tuple)
    benchmark_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> BenchmarkManifest:
        if self.spec.benchmark_id != self.benchmark_id:
            raise ValueError("benchmark_id does not match embedded spec")
        if self.spec.task_id != self.task_id:
            raise ValueError("task_id does not match embedded spec")
        if self.spec.prepared_digest != self.prepared_digest:
            raise ValueError("prepared_digest does not match embedded spec")
        if tuple(self.spec.predictor_identifiers) != tuple(self.predictor_identifiers):
            raise ValueError("predictor_identifiers do not match embedded spec")
        if len(self.evaluation_identifiers) != len(self.predictor_identifiers):
            raise ValueError("one evaluation identifier per predictor is required")
        if len(set(self.evaluation_identifiers)) != len(self.evaluation_identifiers):
            raise ValueError("evaluation_identifiers must be unique")
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("manifest files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("manifest files must be unique by path")
        expected = compute_benchmark_digest(
            schema_version=self.schema_version,
            benchmark_format_version=self.benchmark_format_version,
            benchmark_id=self.benchmark_id, task_id=self.task_id,
            prepared_digest=self.prepared_digest,
            predictor_identifiers=self.predictor_identifiers,
            evaluation_identifiers=self.evaluation_identifiers,
            generated_by=self.generated_by, files=self.files,
        )
        if self.benchmark_digest != expected:
            raise ValueError("benchmark_digest does not match manifest content")
        return self


@dataclass(frozen=True)
class BenchmarkExport:
    manifest: BenchmarkManifest
    content_files: tuple[tuple[str, bytes], ...]

    @property
    def manifest_bytes(self) -> bytes:
        return canonical_json_bytes(self.manifest)

    def output_files(self) -> tuple[tuple[str, bytes], ...]:
        files = list(self.content_files)
        files.append((MANIFEST_FILE, self.manifest_bytes))
        return tuple(sorted(files, key=lambda kv: kv[0]))


def build_benchmark_export(result: BenchmarkResult) -> BenchmarkExport:
    """Build the immutable on-disk bytes for a benchmark result (pure)."""
    comparison_payload = canonical_json_bytes(ComparisonFile(comparison=result.comparison))
    ranking_payload = canonical_json_bytes(RankingFile(ranking=result.ranking))
    content = {COMPARISON_FILE: comparison_payload, RANKING_FILE: ranking_payload}
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path,
    ))
    evaluation_ids = tuple(sorted(r.evaluation_id for r in result.evaluation_runs))
    digest = compute_benchmark_digest(
        schema_version=1, benchmark_format_version=BENCHMARK_FORMAT_VERSION,
        benchmark_id=result.spec.benchmark_id, task_id=result.spec.task_id,
        prepared_digest=result.spec.prepared_digest,
        predictor_identifiers=result.spec.predictor_identifiers,
        evaluation_identifiers=evaluation_ids, generated_by=BENCHMARK_GENERATOR,
        files=files,
    )
    manifest = BenchmarkManifest(
        benchmark_id=result.spec.benchmark_id, spec=result.spec,
        task_id=result.spec.task_id, prepared_digest=result.spec.prepared_digest,
        predictor_identifiers=result.spec.predictor_identifiers,
        evaluation_identifiers=evaluation_ids, generated_by=BENCHMARK_GENERATOR,
        files=files, benchmark_digest=digest,
    )
    return BenchmarkExport(
        manifest=manifest,
        content_files=tuple(sorted(content.items(), key=lambda kv: kv[0])),
    )


@dataclass(frozen=True)
class WrittenBenchmark:
    root: Path
    benchmark_id: str
    benchmark_digest: str
    file_count: int


def write_benchmark(result: BenchmarkResult, benchmarks_root: str | Path) -> WrittenBenchmark:
    """Write ``benchmarks/<benchmark_id>/`` deterministically; never overwrite."""
    export = build_benchmark_export(result)
    root = Path(benchmarks_root) / result.spec.benchmark_id
    if root.exists() and any(root.iterdir()):
        raise BenchmarkError(f"benchmark already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / BENCHMARK_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    try:
        for rel, payload in export.output_files():
            atomic_write_bytes(root / rel, payload)
        result_check = verify_benchmark(root)
        hard = [c for c in result_check.failures if c.rule != "incomplete_marker_absent"]
        if hard:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
            raise BenchmarkError(f"post-write verification failed: {detail}")
    except Exception:
        raise
    marker.unlink()
    fsync_dir(root)
    file_count = sum(1 for p in root.rglob("*") if p.is_file())
    return WrittenBenchmark(
        root=root, benchmark_id=result.spec.benchmark_id,
        benchmark_digest=export.manifest.benchmark_digest, file_count=file_count,
    )


class BenchmarkVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    benchmark_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def verify_benchmark(benchmark_dir: str | Path) -> BenchmarkVerificationResult:
    """Verify a benchmark directory; recompute ranking + digest; fail closed."""
    root = Path(benchmark_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("benchmark_dir_present", False, f"not a directory: {root}"))
        return BenchmarkVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("benchmark_dir_present", True))

    marker_absent = not (root / BENCHMARK_INCOMPLETE_MARKER).exists()
    checks.append(_c("incomplete_marker_absent", marker_absent))

    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return BenchmarkVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = BenchmarkManifest.model_validate_json(manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return BenchmarkVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.benchmark_digest

    checks.append(_c("schema_supported",
                    manifest.schema_version in SUPPORTED_BENCHMARK_SCHEMA))
    checks.append(_c("format_supported",
                    manifest.benchmark_format_version in SUPPORTED_BENCHMARK_FORMAT))

    listed = {f.relative_path for f in manifest.files}
    checks.append(_c("manifest_lists_expected_files",
                    listed == EXPECTED_BENCHMARK_FILES,
                    "" if listed == EXPECTED_BENCHMARK_FILES else f"listed={sorted(listed)}"))

    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != BENCHMARK_INCOMPLETE_MARKER
    }
    allowed = EXPECTED_BENCHMARK_FILES | {MANIFEST_FILE}
    missing = sorted(allowed - on_disk)
    unexpected = sorted(on_disk - allowed)
    checks.append(_c("no_missing_files", not missing,
                    "" if not missing else f"missing={missing}"))
    checks.append(_c("no_unexpected_files", not unexpected,
                    "" if not unexpected else f"unexpected={unexpected}"))

    hash_ok = True
    hash_detail = ""
    for fh in manifest.files:
        fpath = root / fh.relative_path
        if not fpath.is_file():
            hash_ok, hash_detail = False, f"missing {fh.relative_path}"
            break
        raw = fpath.read_bytes()
        if len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok, hash_detail = False, f"hash/size mismatch for {fh.relative_path}"
            break
    checks.append(_c("file_hashes_match", hash_ok, hash_detail))

    recomputed = compute_benchmark_digest(
        schema_version=manifest.schema_version,
        benchmark_format_version=manifest.benchmark_format_version,
        benchmark_id=manifest.benchmark_id, task_id=manifest.task_id,
        prepared_digest=manifest.prepared_digest,
        predictor_identifiers=manifest.predictor_identifiers,
        evaluation_identifiers=manifest.evaluation_identifiers,
        generated_by=manifest.generated_by, files=manifest.files,
    )
    checks.append(_c("benchmark_digest_matches", recomputed == manifest.benchmark_digest))

    # Recompute ranking from the stored comparison; confirm consistency + coverage.
    ranking_ok, coverage_ok = True, True
    detail = ""
    if hash_ok:
        try:
            comparison = ComparisonFile.model_validate_json(
                (root / COMPARISON_FILE).read_bytes()).comparison
            ranking = RankingFile.model_validate_json(
                (root / RANKING_FILE).read_bytes()).ranking
        except ValidationError as exc:
            ranking_ok = False
            detail = str(exc).splitlines()[0]
        else:
            if compute_ranking(comparison) != ranking:
                ranking_ok = False
                detail = "ranking does not match a recomputation from comparison"
            compared_ids = {row.predictor_identifier for row in comparison}
            compared_eids = {row.evaluation_id for row in comparison}
            coverage_ok = (
                compared_ids == set(manifest.predictor_identifiers)
                and compared_eids == set(manifest.evaluation_identifiers)
                and len(comparison) == len(manifest.predictor_identifiers)
            )
    else:
        ranking_ok = False
    checks.append(_c("ranking_matches_comparison", ranking_ok, detail))
    checks.append(_c("comparison_covers_all_predictors", coverage_ok))

    return BenchmarkVerificationResult(
        verified=all(c.passed for c in checks), benchmark_digest=digest,
        checks=tuple(checks),
    )


@dataclass(frozen=True)
class LoadedBenchmark:
    manifest: BenchmarkManifest
    comparison: tuple[ComparisonRow, ...]
    ranking: tuple[RankingEntry, ...]


def read_benchmark(benchmark_dir: str | Path) -> LoadedBenchmark:
    """Verify then reconstruct a benchmark; fail closed on any failure."""
    root = Path(benchmark_dir)
    result = verify_benchmark(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise BenchmarkError(f"benchmark failed verification: {detail}")
    manifest = BenchmarkManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    comparison = ComparisonFile.model_validate_json(
        (root / COMPARISON_FILE).read_bytes()).comparison
    ranking = RankingFile.model_validate_json((root / RANKING_FILE).read_bytes()).ranking
    return LoadedBenchmark(manifest=manifest, comparison=comparison, ranking=ranking)
