"""Structured-output reliability measurement (Gate 13).

Gate 12's real benchmark failed for a MEASURABLE reason: neither the base
model nor the one-step-trained checkpoint produced strictly parseable JSON.
This module makes that failure mode first-class evidence — WITHOUT changing a
single parsing rule: the authoritative parser remains the shared Gate 8
`parse_backend_response`, prompts are untouched (compliance is MEASURED,
never optimized here), Gate 7 metrics are untouched, and Gate 9 ranking is
untouched. Everything below is deterministic, derived purely from persisted
evaluation records, and lands in a SEPARATE immutable report artifact keyed
to a benchmark — extending Gate 9's reporting without altering its stores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import DatasetFileHash
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.benchmark import BenchmarkResult
from verifiednet.evaluation.contract import NormalizationPolicy
from verifiednet.evaluation.engine import EvaluationRun
from verifiednet.evaluation.prediction import InvalidPrediction
from verifiednet.evaluation.scoring import ratio_str
from verifiednet.schemas.base import StrictModel

STRUCTURED_REPORT_FORMAT_VERSION = 1
STRUCTURED_REPORT_GENERATOR = "verifiednet.evaluation.structured"
MANIFEST_FILE = "manifest.json"
REPORT_FILE = "report.json"
STRUCTURED_REPORT_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPECTED_STRUCTURED_REPORT_FILES: frozenset[str] = frozenset({REPORT_FILE})

#: Backend-failure reason codes (no model text existed to parse).
_BACKEND_REASONS = frozenset(
    {"backend_unavailable", "inference_timeout", "backend_error"})
#: Reason codes whose output WAS valid JSON but failed the response schema.
_SCHEMA_REASONS = frozenset(
    {"not_an_object", "missing_fault_family", "unknown_fault_family",
     "unsupported_prediction_type"})


class StructuredReportError(VerifiedNetError):
    """A structured-output report could not be built, written, or read."""


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


# ---------------------------------------------------------------------------
# Invalid-output categorization (deterministic diagnostics)
# ---------------------------------------------------------------------------


class InvalidOutputCategory(StrEnum):
    BACKEND_FAILURE = "backend_failure"
    EMPTY_OUTPUT = "empty_output"
    DEGENERATE_REPETITION = "degenerate_repetition"
    TRUNCATED_JSON = "truncated_json"
    PROSE_WRAPPED_JSON = "prose_wrapped_json"
    MALFORMED_OTHER = "malformed_other"
    NON_OBJECT_JSON = "non_object_json"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    OUT_OF_SCHEMA_VALUE = "out_of_schema_value"
    UNSUPPORTED_PREDICTION_TYPE = "unsupported_prediction_type"


#: Categories whose raw output was NOT valid JSON at all.
MALFORMED_CATEGORIES: frozenset[InvalidOutputCategory] = frozenset({
    InvalidOutputCategory.EMPTY_OUTPUT,
    InvalidOutputCategory.DEGENERATE_REPETITION,
    InvalidOutputCategory.TRUNCATED_JSON,
    InvalidOutputCategory.PROSE_WRAPPED_JSON,
    InvalidOutputCategory.MALFORMED_OTHER,
})
#: Categories whose output parsed as JSON but violated the response schema.
SCHEMA_FAILURE_CATEGORIES: frozenset[InvalidOutputCategory] = frozenset({
    InvalidOutputCategory.NON_OBJECT_JSON,
    InvalidOutputCategory.MISSING_REQUIRED_FIELD,
    InvalidOutputCategory.OUT_OF_SCHEMA_VALUE,
    InvalidOutputCategory.UNSUPPORTED_PREDICTION_TYPE,
})


def _is_degenerate_repetition(text: str) -> bool:
    """A short token repeated from the start — the classic decode collapse."""
    for period in (1, 2, 3, 4):
        needed = 8 * period
        if len(text) >= needed and text[:needed] == text[:period] * 8:
            return True
    return False


def classify_invalid_output(
    *, reason_code: str, raw_excerpt: str,
) -> InvalidOutputCategory:
    """Deterministic diagnostic category for one invalid prediction.

    Diagnostics only: this NEVER changes how output is parsed or scored — the
    authoritative mapping stays `parse_backend_response` (Gate 8, unchanged).
    """
    if reason_code in _BACKEND_REASONS:
        return InvalidOutputCategory.BACKEND_FAILURE
    if reason_code == "not_an_object":
        return InvalidOutputCategory.NON_OBJECT_JSON
    if reason_code == "missing_fault_family":
        return InvalidOutputCategory.MISSING_REQUIRED_FIELD
    if reason_code == "unknown_fault_family":
        return InvalidOutputCategory.OUT_OF_SCHEMA_VALUE
    if reason_code == "unsupported_prediction_type":
        return InvalidOutputCategory.UNSUPPORTED_PREDICTION_TYPE
    stripped = raw_excerpt.strip()
    if not stripped:
        return InvalidOutputCategory.EMPTY_OUTPUT
    if _is_degenerate_repetition(stripped):
        return InvalidOutputCategory.DEGENERATE_REPETITION
    if stripped.startswith("{"):
        if stripped.count("{") > stripped.count("}"):
            return InvalidOutputCategory.TRUNCATED_JSON
        return InvalidOutputCategory.MALFORMED_OTHER
    if "{" in stripped:
        return InvalidOutputCategory.PROSE_WRAPPED_JSON
    return InvalidOutputCategory.MALFORMED_OTHER


class SchemaValidationResult(StrictModel):
    """Diagnostic-only strict schema validation of one raw output."""

    schema_version: Literal[1] = 1
    json_parsed: bool
    is_object: bool
    prediction_type_valid: bool
    fault_family_valid: bool | None = None
    schema_compliant: bool
    finding: str = ""


def validate_response_schema(
    text: str,
    *,
    candidate_families: tuple[str, ...],
    normalization: NormalizationPolicy | None = None,
) -> SchemaValidationResult:
    """Strict, deterministic schema check for the Gate 8 response contract.

    Diagnostics only — the authoritative parser is unchanged. Compliant means:
    one JSON object, ``prediction_type`` of ``diagnosis``/``abstention``, and
    for a diagnosis a ``fault_family`` inside the candidate class space.
    """
    norm = normalization or NormalizationPolicy()
    candidates = frozenset(norm.normalize(f) for f in candidate_families)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return SchemaValidationResult(
            json_parsed=False, is_object=False, prediction_type_valid=False,
            schema_compliant=False, finding="not valid JSON")
    if not isinstance(data, dict):
        return SchemaValidationResult(
            json_parsed=True, is_object=False, prediction_type_valid=False,
            schema_compliant=False, finding="JSON is not an object")
    ptype = data.get("prediction_type")
    if ptype == "abstention":
        return SchemaValidationResult(
            json_parsed=True, is_object=True, prediction_type_valid=True,
            schema_compliant=True)
    if ptype != "diagnosis":
        return SchemaValidationResult(
            json_parsed=True, is_object=True, prediction_type_valid=False,
            schema_compliant=False,
            finding=f"unsupported prediction_type: {ptype!r}")
    family = data.get("fault_family")
    family_valid = (isinstance(family, str)
                    and norm.normalize(family) in candidates)
    return SchemaValidationResult(
        json_parsed=True, is_object=True, prediction_type_valid=True,
        fault_family_valid=family_valid, schema_compliant=family_valid,
        finding="" if family_valid else f"fault_family out of schema: {family!r}")


# ---------------------------------------------------------------------------
# Parser statistics + prompt-compliance measurement (per evaluation run)
# ---------------------------------------------------------------------------


class ParserFailureCount(StrictModel):
    category: InvalidOutputCategory
    count: int = Field(ge=1)


class ParserStatistics(StrictModel):
    """Deterministic parser/compliance statistics for ONE evaluation run.

    Raw counts precede every rate; a rate is ``None`` when its denominator is
    zero. ``prompt_compliance_rate`` MEASURES compliance with the unchanged
    Gate 8 response contract — nothing here optimizes a prompt.
    """

    schema_version: Literal[1] = 1
    evaluation_id: str = Field(min_length=1)
    baseline_id: str = Field(min_length=1)
    total: int = Field(ge=0)
    valid_structured_predictions: int = Field(ge=0)
    invalid_predictions: int = Field(ge=0)
    json_valid_outputs: int = Field(ge=0)
    malformed_outputs: int = Field(ge=0)
    backend_failures: int = Field(ge=0)
    failure_categories: tuple[ParserFailureCount, ...] = Field(
        default_factory=tuple)
    json_validity_rate: str | None = None
    malformed_output_rate: str | None = None
    valid_structured_prediction_rate: str | None = None
    prompt_compliance_rate: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ParserStatistics:
        if self.valid_structured_predictions + self.invalid_predictions \
                != self.total:
            raise ValueError("valid + invalid must equal total")
        if (self.json_valid_outputs + self.malformed_outputs
                + self.backend_failures) != self.total:
            raise ValueError(
                "json-valid + malformed + backend must equal total")
        categories = [f.category for f in self.failure_categories]
        if categories != sorted(categories) or \
                len(categories) != len(set(categories)):
            raise ValueError("failure_categories must be sorted and unique")
        if sum(f.count for f in self.failure_categories) \
                != self.invalid_predictions:
            raise ValueError("failure category counts must sum to invalid")
        for rate, numerator in (
                (self.json_validity_rate, self.json_valid_outputs),
                (self.malformed_output_rate, self.malformed_outputs),
                (self.valid_structured_prediction_rate,
                 self.valid_structured_predictions),
                (self.prompt_compliance_rate,
                 self.valid_structured_predictions)):
            if rate != ratio_str(numerator, self.total):
                raise ValueError("a stored rate does not match its counts")
        return self


def compute_parser_statistics(run: EvaluationRun) -> ParserStatistics:
    """Pure statistics from persisted records (never re-runs a model)."""
    from collections import Counter

    categories: Counter[InvalidOutputCategory] = Counter()
    valid = 0
    for record in run.records:
        prediction = record.prediction
        if isinstance(prediction, InvalidPrediction):
            categories[classify_invalid_output(
                reason_code=prediction.reason_code,
                raw_excerpt=prediction.raw_excerpt)] += 1
        else:
            valid += 1
    invalid = sum(categories.values())
    total = valid + invalid
    malformed = sum(count for cat, count in categories.items()
                    if cat in MALFORMED_CATEGORIES)
    backend = categories.get(InvalidOutputCategory.BACKEND_FAILURE, 0)
    json_valid = total - malformed - backend
    return ParserStatistics(
        evaluation_id=run.evaluation_id,
        baseline_id=run.baseline_spec.baseline_id, total=total,
        valid_structured_predictions=valid, invalid_predictions=invalid,
        json_valid_outputs=json_valid, malformed_outputs=malformed,
        backend_failures=backend,
        failure_categories=tuple(
            ParserFailureCount(category=cat, count=count)
            for cat, count in sorted(categories.items())),
        json_validity_rate=ratio_str(json_valid, total),
        malformed_output_rate=ratio_str(malformed, total),
        valid_structured_prediction_rate=ratio_str(valid, total),
        prompt_compliance_rate=ratio_str(valid, total))


# ---------------------------------------------------------------------------
# Benchmark-level structured-output report (separate immutable artifact)
# ---------------------------------------------------------------------------


class StructuredOutputRow(StrictModel):
    """One predictor's accuracy + abstention + invalid + compliance view.

    This IS the Gate 13 paired-reporting row: filtering the report to the
    matched base and trained identifiers gives the paired compliance view
    without touching the Gate 12 comparison artifact.
    """

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
    statistics: ParserStatistics

    @model_validator(mode="after")
    def _consistent(self) -> StructuredOutputRow:
        if self.statistics.baseline_id != self.predictor_identifier:
            raise ValueError("statistics bind a different predictor")
        if self.statistics.evaluation_id != self.evaluation_id:
            raise ValueError("statistics bind a different evaluation")
        if self.statistics.invalid_predictions \
                != self.invalid_prediction_count:
            raise ValueError("invalid counts disagree")
        if self.exact_match_accuracy != ratio_str(
                self.accepted_correct, self.accepted_evaluated):
            raise ValueError("exact_match_accuracy does not match counts")
        if self.abstention_accuracy != ratio_str(
                self.abstention_correct, self.abstention_count):
            raise ValueError("abstention_accuracy does not match counts")
        return self


def derive_structured_report_id(
    *,
    benchmark_id: str,
    task_id: str,
    prepared_digest: str,
    predictor_identifiers: tuple[str, ...],
) -> str:
    payload = {
        "benchmark_id": benchmark_id,
        "task_id": task_id,
        "prepared_digest": prepared_digest,
        "predictor_identifiers": sorted(predictor_identifiers),
    }
    return "sor-" + sha256_canonical(payload)[:16]


class StructuredOutputReport(StrictModel):
    """The deterministic per-benchmark structured-output reliability report."""

    schema_version: Literal[1] = 1
    report_version: Literal[1] = 1
    benchmark_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    prepared_digest: str = Field(min_length=1)
    rows: tuple[StructuredOutputRow, ...] = Field(min_length=1)
    report_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> StructuredOutputReport:
        identifiers = [r.predictor_identifier for r in self.rows]
        if identifiers != sorted(identifiers) or \
                len(identifiers) != len(set(identifiers)):
            raise ValueError("rows must be identifier-sorted and unique")
        expected = derive_structured_report_id(
            benchmark_id=self.benchmark_id, task_id=self.task_id,
            prepared_digest=self.prepared_digest,
            predictor_identifiers=tuple(identifiers))
        if self.report_id != expected:
            raise ValueError("report_id does not match the report content")
        return self


def build_structured_output_report(
    result: BenchmarkResult,
) -> StructuredOutputReport:
    """Derive the reliability report for EVERY benchmarked predictor.

    Consumes the unchanged Gate 9 result; alters nothing in it. Rankings are
    untouched — reliability rates are reported, never ranked on.
    """
    rows: list[StructuredOutputRow] = []
    for run in result.evaluation_runs:
        statistics = compute_parser_statistics(run)
        accepted_evaluated = sum(
            m.evaluated for m in run.metrics.accepted_partitions)
        accepted_correct = sum(
            m.correct for m in run.metrics.accepted_partitions)
        rows.append(StructuredOutputRow(
            predictor_identifier=run.baseline_spec.baseline_id,
            evaluation_id=run.evaluation_id,
            accepted_evaluated=accepted_evaluated,
            accepted_correct=accepted_correct,
            exact_match_accuracy=ratio_str(
                accepted_correct, accepted_evaluated),
            abstention_count=run.metrics.abstention.count,
            abstention_correct=run.metrics.abstention.correct,
            abstention_accuracy=run.metrics.abstention.abstention_accuracy,
            invalid_prediction_count=statistics.invalid_predictions,
            statistics=statistics))
    ordered = tuple(sorted(rows, key=lambda r: r.predictor_identifier))
    return StructuredOutputReport(
        benchmark_id=result.spec.benchmark_id, task_id=result.spec.task_id,
        prepared_digest=result.spec.prepared_digest, rows=ordered,
        report_id=derive_structured_report_id(
            benchmark_id=result.spec.benchmark_id,
            task_id=result.spec.task_id,
            prepared_digest=result.spec.prepared_digest,
            predictor_identifiers=tuple(
                r.predictor_identifier for r in ordered)))


def compute_structured_report_digest(
    *,
    schema_version: int,
    report_format_version: int,
    report_id: str,
    benchmark_id: str,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    payload = {
        "schema_version": schema_version,
        "report_format_version": report_format_version,
        "report_id": report_id,
        "benchmark_id": benchmark_id,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256,
             "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "sordig-" + sha256_canonical(payload)[:24]


class StructuredReportManifest(StrictModel):
    schema_version: Literal[1] = 1
    report_format_version: Literal[1] = 1
    report_id: str = Field(min_length=1)
    benchmark_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    prepared_digest: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    report_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> StructuredReportManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        expected = compute_structured_report_digest(
            schema_version=self.schema_version,
            report_format_version=self.report_format_version,
            report_id=self.report_id, benchmark_id=self.benchmark_id,
            generated_by=self.generated_by, files=self.files)
        if self.report_digest != expected:
            raise ValueError("report_digest does not match the content")
        return self


@dataclass(frozen=True)
class WrittenStructuredReport:
    root: Path
    report_id: str
    report_digest: str


def write_structured_output_report(
    report: StructuredOutputReport, reports_root: str | Path,
) -> WrittenStructuredReport:
    """Write ``structured-output-reports/<report_id>/``; never overwrite."""
    report_payload = canonical_json_bytes(report)
    files = (DatasetFileHash(relative_path=REPORT_FILE,
                             sha256=sha256_bytes(report_payload),
                             size=len(report_payload)),)
    manifest = StructuredReportManifest(
        report_id=report.report_id, benchmark_id=report.benchmark_id,
        task_id=report.task_id, prepared_digest=report.prepared_digest,
        generated_by=STRUCTURED_REPORT_GENERATOR, files=files,
        report_digest=compute_structured_report_digest(
            schema_version=1,
            report_format_version=STRUCTURED_REPORT_FORMAT_VERSION,
            report_id=report.report_id, benchmark_id=report.benchmark_id,
            generated_by=STRUCTURED_REPORT_GENERATOR, files=files))
    root = Path(reports_root) / report.report_id
    if root.exists() and any(root.iterdir()):
        raise StructuredReportError(f"report already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / STRUCTURED_REPORT_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    atomic_write_bytes(root / REPORT_FILE, report_payload)
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(manifest))
    verification = verify_structured_output_report(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise StructuredReportError(
            f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenStructuredReport(
        root=root, report_id=report.report_id,
        report_digest=manifest.report_digest)


class StructuredReportVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    report_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def verify_structured_output_report(
    report_dir: str | Path,
) -> StructuredReportVerificationResult:
    """Verify artifact consistency; recompute id bindings; fail closed."""
    root = Path(report_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("report_dir_present", False, str(root)))
        return StructuredReportVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("report_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / STRUCTURED_REPORT_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return StructuredReportVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = StructuredReportManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return StructuredReportVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))

    on_disk = {str(p.relative_to(root)) for p in root.rglob("*")
               if p.is_file() and p.name != STRUCTURED_REPORT_INCOMPLETE_MARKER}
    allowed = EXPECTED_STRUCTURED_REPORT_FILES | {MANIFEST_FILE}
    checks.append(_c("no_missing_files", not sorted(allowed - on_disk)))
    checks.append(_c("no_unexpected_files", not sorted(on_disk - allowed)))

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

    report_ok = True
    if hash_ok:
        try:
            report = StructuredOutputReport.model_validate_json(
                (root / REPORT_FILE).read_bytes())
        except ValidationError:
            report_ok = False
        else:
            report_ok = (report.report_id == manifest.report_id
                         and report.benchmark_id == manifest.benchmark_id
                         and report.task_id == manifest.task_id
                         and report.prepared_digest
                         == manifest.prepared_digest)
    checks.append(_c("report_binds_manifest", report_ok))

    return StructuredReportVerificationResult(
        verified=all(c.passed for c in checks),
        report_digest=manifest.report_digest, checks=tuple(checks))


@dataclass(frozen=True)
class LoadedStructuredReport:
    manifest: StructuredReportManifest
    report: StructuredOutputReport


def read_structured_output_report(
    report_dir: str | Path,
) -> LoadedStructuredReport:
    """Verify then reconstruct a structured-output report; fail closed."""
    root = Path(report_dir)
    result = verify_structured_output_report(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise StructuredReportError(
            f"structured report failed verification: {detail}")
    return LoadedStructuredReport(
        manifest=StructuredReportManifest.model_validate_json(
            (root / MANIFEST_FILE).read_bytes()),
        report=StructuredOutputReport.model_validate_json(
            (root / REPORT_FILE).read_bytes()))
