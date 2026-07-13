"""Immutable evaluation output: manifest, digest, writer, reader, verifier (Gate 7).

An evaluation is persisted into ``evaluations/<evaluation_id>/`` — separate from
the verified runs, the Part 3 export, and the Part 4 prepared corpus — as:

    manifest.json     # EvaluationManifest (+ embedded task & baseline_spec, digest)
    records.jsonl     # one EvaluationRecord per line, ordered by example_id
    metrics.json      # AggregateMetrics + partition summaries
    confusion.json    # accepted-side confusion counts

Everything is canonical and path-sorted; the writer is atomic under a
``.INCOMPLETE`` marker and refuses to overwrite an existing evaluation. The
verifier RE-COMPUTES metrics/confusion/ids from the records (it never trusts the
stored derived values) and fails closed; the reader verifies before returning.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import DatasetFileHash, DatasetPartitionCounts
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.baseline import BaselineSpec
from verifiednet.evaluation.contract import EvaluationTask
from verifiednet.evaluation.engine import (
    EvaluationRun,
    audit_evaluation_run,
    compute_corpus_counts,
)
from verifiednet.evaluation.scoring import (
    AggregateMetrics,
    ConfusionCount,
    EvaluationRecord,
    PartitionSummary,
)
from verifiednet.schemas.base import StrictModel

EVALUATION_FORMAT_VERSION = 1
EVALUATION_GENERATOR = "verifiednet.evaluation.engine"

MANIFEST_FILE = "manifest.json"
RECORDS_FILE = "records.jsonl"
METRICS_FILE = "metrics.json"
CONFUSION_FILE = "confusion.json"
EVALUATION_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPECTED_EVALUATION_FILES: frozenset[str] = frozenset(
    {RECORDS_FILE, METRICS_FILE, CONFUSION_FILE}
)
SUPPORTED_EVALUATION_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_EVALUATION_FORMAT: frozenset[int] = frozenset({1})


class EvaluationStoreError(VerifiedNetError):
    """Writing/reading/verifying an evaluation directory failed."""


class MetricsBundle(StrictModel):
    """The ``metrics.json`` payload: aggregate metrics + partition summaries."""

    schema_version: Literal[1] = 1
    metrics: AggregateMetrics
    partition_summaries: tuple[PartitionSummary, ...] = Field(default_factory=tuple)


class ConfusionFile(StrictModel):
    """The ``confusion.json`` payload."""

    schema_version: Literal[1] = 1
    confusion: tuple[ConfusionCount, ...] = Field(default_factory=tuple)


def compute_evaluation_digest(
    *,
    schema_version: int,
    evaluation_format_version: int,
    evaluation_id: str,
    task_id: str,
    baseline_id: str,
    prepared_digest: str,
    dataset_digest: str | None,
    feature_policy_id: str,
    label_policy_id: str,
    generated_by: str,
    record_count: int,
    partition_counts: DatasetPartitionCounts,
    files: tuple[DatasetFileHash, ...],
) -> str:
    """Non-recursive digest over the evaluation content + deterministic config."""
    payload = {
        "schema_version": schema_version,
        "evaluation_format_version": evaluation_format_version,
        "evaluation_id": evaluation_id,
        "task_id": task_id,
        "baseline_id": baseline_id,
        "prepared_digest": prepared_digest,
        "dataset_digest": dataset_digest,
        "feature_policy_id": feature_policy_id,
        "label_policy_id": label_policy_id,
        "generated_by": generated_by,
        "record_count": record_count,
        "partition_counts": {
            "train": partition_counts.train,
            "validation": partition_counts.validation,
            "test": partition_counts.test,
            "abstention": partition_counts.abstention,
        },
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "evaldig-" + sha256_canonical(payload)[:24]


class EvaluationManifest(StrictModel):
    """The immutable manifest of a persisted evaluation (self-validating digest)."""

    schema_version: Literal[1] = 1
    evaluation_format_version: Literal[1] = 1
    evaluation_id: str = Field(min_length=1)
    task: EvaluationTask
    baseline_spec: BaselineSpec
    task_id: str = Field(min_length=1)
    baseline_id: str = Field(min_length=1)
    prepared_digest: str
    dataset_digest: str | None = None
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    record_count: int = Field(ge=0)
    partition_counts: DatasetPartitionCounts
    files: tuple[DatasetFileHash, ...] = Field(default_factory=tuple)
    evaluation_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> EvaluationManifest:
        if self.task.task_id != self.task_id:
            raise ValueError("task_id does not match embedded task")
        if self.baseline_spec.baseline_id != self.baseline_id:
            raise ValueError("baseline_id does not match embedded baseline_spec")
        if self.baseline_spec.task_id != self.task_id:
            raise ValueError("baseline_spec.task_id does not match task_id")
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("manifest files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("manifest files must be unique by path")
        expected = compute_evaluation_digest(
            schema_version=self.schema_version,
            evaluation_format_version=self.evaluation_format_version,
            evaluation_id=self.evaluation_id, task_id=self.task_id,
            baseline_id=self.baseline_id, prepared_digest=self.prepared_digest,
            dataset_digest=self.dataset_digest, feature_policy_id=self.feature_policy_id,
            label_policy_id=self.label_policy_id, generated_by=self.generated_by,
            record_count=self.record_count, partition_counts=self.partition_counts,
            files=self.files,
        )
        if self.evaluation_digest != expected:
            raise ValueError("evaluation_digest does not match manifest content")
        return self


@dataclass(frozen=True)
class EvaluationExport:
    manifest: EvaluationManifest
    content_files: tuple[tuple[str, bytes], ...]

    @property
    def manifest_bytes(self) -> bytes:
        return canonical_json_bytes(self.manifest)

    def output_files(self) -> tuple[tuple[str, bytes], ...]:
        files = list(self.content_files)
        files.append((MANIFEST_FILE, self.manifest_bytes))
        return tuple(sorted(files, key=lambda kv: kv[0]))


def _records_bytes(records: tuple[EvaluationRecord, ...]) -> bytes:
    return b"".join(canonical_json_bytes(r) + b"\n" for r in records)


def _partition_counts(run: EvaluationRun) -> DatasetPartitionCounts:
    c = compute_corpus_counts(run.records)
    return DatasetPartitionCounts(
        train=c.train, validation=c.validation, test=c.test,
        abstention=c.abstention_partition,
    )


def build_evaluation_export(run: EvaluationRun) -> EvaluationExport:
    """Build the immutable on-disk bytes for one evaluation run (pure)."""
    records_payload = _records_bytes(run.records)
    metrics_payload = canonical_json_bytes(MetricsBundle(
        metrics=run.metrics, partition_summaries=run.partition_summaries))
    confusion_payload = canonical_json_bytes(ConfusionFile(confusion=run.confusion))

    content = {
        RECORDS_FILE: records_payload,
        METRICS_FILE: metrics_payload,
        CONFUSION_FILE: confusion_payload,
    }
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path,
    ))
    counts = _partition_counts(run)
    digest = compute_evaluation_digest(
        schema_version=1, evaluation_format_version=EVALUATION_FORMAT_VERSION,
        evaluation_id=run.evaluation_id, task_id=run.task.task_id,
        baseline_id=run.baseline_spec.baseline_id, prepared_digest=run.prepared_digest,
        dataset_digest=run.dataset_digest, feature_policy_id=run.feature_policy_id,
        label_policy_id=run.label_policy_id, generated_by=EVALUATION_GENERATOR,
        record_count=len(run.records), partition_counts=counts, files=files,
    )
    manifest = EvaluationManifest(
        evaluation_id=run.evaluation_id, task=run.task, baseline_spec=run.baseline_spec,
        task_id=run.task.task_id, baseline_id=run.baseline_spec.baseline_id,
        prepared_digest=run.prepared_digest, dataset_digest=run.dataset_digest,
        feature_policy_id=run.feature_policy_id, label_policy_id=run.label_policy_id,
        generated_by=EVALUATION_GENERATOR, record_count=len(run.records),
        partition_counts=counts, files=files, evaluation_digest=digest,
    )
    return EvaluationExport(
        manifest=manifest,
        content_files=tuple(sorted(content.items(), key=lambda kv: kv[0])),
    )


@dataclass(frozen=True)
class WrittenEvaluation:
    root: Path
    evaluation_id: str
    evaluation_digest: str
    file_count: int


def write_evaluation(
    run: EvaluationRun, evaluations_root: str | Path
) -> WrittenEvaluation:
    """Write ``evaluations/<evaluation_id>/`` deterministically; never overwrite."""
    export = build_evaluation_export(run)
    root = Path(evaluations_root) / run.evaluation_id
    if root.exists() and any(root.iterdir()):
        raise EvaluationStoreError(f"evaluation already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / EVALUATION_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    try:
        for rel, payload in export.output_files():
            atomic_write_bytes(root / rel, payload)
        result = verify_evaluation(root)
        hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
        if hard:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
            raise EvaluationStoreError(f"post-write verification failed: {detail}")
    except Exception:
        raise
    marker.unlink()
    fsync_dir(root)
    file_count = sum(1 for p in root.rglob("*") if p.is_file())
    return WrittenEvaluation(
        root=root, evaluation_id=run.evaluation_id,
        evaluation_digest=export.manifest.evaluation_digest, file_count=file_count,
    )


class EvaluationVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    evaluation_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def _parse_records(data: bytes) -> tuple[EvaluationRecord, ...]:
    if data == b"":
        return ()
    if not data.endswith(b"\n"):
        raise EvaluationStoreError("records file must end with a newline")
    out = []
    for line in data[:-1].split(b"\n"):
        if not line:
            raise EvaluationStoreError("blank line in records file")
        out.append(EvaluationRecord.model_validate_json(line))
    return tuple(out)


def _reconstruct_run(root: Path, manifest: EvaluationManifest) -> EvaluationRun:
    records = _parse_records((root / RECORDS_FILE).read_bytes())
    bundle = MetricsBundle.model_validate_json((root / METRICS_FILE).read_bytes())
    confusion = ConfusionFile.model_validate_json((root / CONFUSION_FILE).read_bytes())
    return EvaluationRun(
        task=manifest.task, baseline_spec=manifest.baseline_spec,
        prepared_digest=manifest.prepared_digest, dataset_digest=manifest.dataset_digest,
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id, evaluation_id=manifest.evaluation_id,
        records=records, metrics=bundle.metrics, confusion=confusion.confusion,
        partition_summaries=bundle.partition_summaries,
    )


def verify_evaluation(evaluation_dir: str | Path) -> EvaluationVerificationResult:
    """Verify an evaluation directory; recompute derived values; fail closed."""
    root = Path(evaluation_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("evaluation_dir_present", False, f"not a directory: {root}"))
        return EvaluationVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("evaluation_dir_present", True))

    marker_absent = not (root / EVALUATION_INCOMPLETE_MARKER).exists()
    checks.append(_c("incomplete_marker_absent", marker_absent))

    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return EvaluationVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = EvaluationManifest.model_validate_json(manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return EvaluationVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.evaluation_digest

    checks.append(_c("schema_supported",
                    manifest.schema_version in SUPPORTED_EVALUATION_SCHEMA))
    checks.append(_c("format_supported",
                    manifest.evaluation_format_version in SUPPORTED_EVALUATION_FORMAT))

    listed = {f.relative_path for f in manifest.files}
    checks.append(_c("manifest_lists_expected_files",
                    listed == EXPECTED_EVALUATION_FILES,
                    "" if listed == EXPECTED_EVALUATION_FILES else f"listed={sorted(listed)}"))

    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != EVALUATION_INCOMPLETE_MARKER
    }
    allowed = EXPECTED_EVALUATION_FILES | {MANIFEST_FILE}
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

    recomputed = compute_evaluation_digest(
        schema_version=manifest.schema_version,
        evaluation_format_version=manifest.evaluation_format_version,
        evaluation_id=manifest.evaluation_id, task_id=manifest.task_id,
        baseline_id=manifest.baseline_id, prepared_digest=manifest.prepared_digest,
        dataset_digest=manifest.dataset_digest, feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id, generated_by=manifest.generated_by,
        record_count=manifest.record_count, partition_counts=manifest.partition_counts,
        files=manifest.files,
    )
    checks.append(_c("evaluation_digest_matches", recomputed == manifest.evaluation_digest))

    # Reconstruct the run (its validator recomputes evaluation_id + ordering),
    # then run the integrity audit (recomputes metrics/confusion/correctness).
    run_ok, integrity_ok, count_ok = True, True, True
    detail = ""
    if hash_ok:
        try:
            run = _reconstruct_run(root, manifest)
        except (VerifiedNetError, ValidationError) as exc:
            run_ok = False
            detail = str(exc).splitlines()[0]
        else:
            integrity = audit_evaluation_run(run)
            integrity_ok = integrity.passed
            counts = _partition_counts(run)
            count_ok = (
                manifest.record_count == len(run.records)
                and manifest.partition_counts == counts
            )
    else:
        run_ok = False
    checks.append(_c("run_reconstructs", run_ok, detail))
    checks.append(_c("integrity_audit_passes", integrity_ok))
    checks.append(_c("counts_match_manifest", count_ok))

    return EvaluationVerificationResult(
        verified=all(c.passed for c in checks), evaluation_digest=digest,
        checks=tuple(checks),
    )


def read_evaluation(evaluation_dir: str | Path) -> EvaluationRun:
    """Verify then reconstruct an evaluation run; fail closed on any failure."""
    root = Path(evaluation_dir)
    result = verify_evaluation(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise EvaluationStoreError(f"evaluation failed verification: {detail}")
    manifest = EvaluationManifest.model_validate_json(
        (root / MANIFEST_FILE).read_bytes())
    return _reconstruct_run(root, manifest)
