"""Persisted, versioned evaluation corpus + corpus-quality verification (Gate 13).

Gate 12 exposed the measurement problem honestly: the evaluation corpus was a
transient fixture with zero eligible test examples, so no model-quality
conclusion was possible. Gate 13 gives the project a REGISTERED evaluation
corpus: a Gate 6 prepared corpus (unchanged — Gate 6 stays the only source of
truth) is bound into an immutable, content-addressed, VERSIONED
`evaluation-corpora/<evalcorpus-…>/` artifact carrying explicit provenance, a
frozen generation policy, deterministic coverage statistics (fault-family /
scenario / rejection / topology distributions, split balance, eligible test
count), and a fail-closed structural quality verification (duplicates, split
leakage, malformed examples, missing evidence — with imbalance REPORTED,
never silently normalized).

Nothing in Gate 6/7 changes: this layer only DESCRIBES and REGISTERS a
prepared corpus; it never edits examples, splits, features, or labels, and a
corpus that fails quality verification cannot be registered at all.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.features import AbstentionLabels, AcceptedLabels
from verifiednet.datasets.models import (
    DatasetExampleKind,
    DatasetFileHash,
    DatasetPartition,
    DatasetPartitionCounts,
)
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.comparison import CorpusProvenance
from verifiednet.evaluation.scoring import ratio_str
from verifiednet.schemas.base import StrictModel

EVALUATION_CORPUS_FORMAT_VERSION = 1
EVALUATION_CORPUS_GENERATOR = "verifiednet.evaluation.evalcorpus"
MANIFEST_FILE = "manifest.json"
COVERAGE_FILE = "coverage.json"
QUALITY_FILE = "quality.json"
EVALCORPUS_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPECTED_EVALCORPUS_FILES: frozenset[str] = frozenset(
    {COVERAGE_FILE, QUALITY_FILE})


class EvaluationCorpusError(VerifiedNetError):
    """An evaluation-corpus version could not be built, written, or read."""


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


# ---------------------------------------------------------------------------
# Generation policy (frozen, content-addressed)
# ---------------------------------------------------------------------------


class EvaluationCorpusGenerationPolicy(StrictModel):
    """How the corpus was produced — recorded, never inferred.

    ``source_kind`` is Literal-locked to verified run artifacts: an evaluation
    corpus can never claim any other origin (Gate 6 is the only source of
    truth). The generator string is descriptive provenance, not an identity
    escape hatch — the corpus identity binds the prepared digest itself.
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    source_kind: Literal["verified_run_artifacts"] = "verified_run_artifacts"
    generator: str = Field(min_length=1)
    split_policy_id: str = Field(min_length=1)
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    requested_accepted_runs: int = Field(ge=0)
    requested_rejected_runs: int = Field(ge=0)
    generation_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationCorpusGenerationPolicy:
        if self.generation_policy_id != derive_generation_policy_id(self):
            raise ValueError(
                "generation_policy_id does not match the policy content")
        return self


def derive_generation_policy_id(
    policy: EvaluationCorpusGenerationPolicy,
) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("generation_policy_id", None)
    return "ecgen-" + sha256_canonical(payload)[:16]


def build_generation_policy(
    *,
    generator: str,
    split_policy_id: str,
    feature_policy_id: str,
    label_policy_id: str,
    requested_accepted_runs: int,
    requested_rejected_runs: int,
) -> EvaluationCorpusGenerationPolicy:
    probe = EvaluationCorpusGenerationPolicy.model_construct(
        generator=generator, split_policy_id=split_policy_id,
        feature_policy_id=feature_policy_id, label_policy_id=label_policy_id,
        requested_accepted_runs=requested_accepted_runs,
        requested_rejected_runs=requested_rejected_runs)
    return EvaluationCorpusGenerationPolicy(
        generator=generator, split_policy_id=split_policy_id,
        feature_policy_id=feature_policy_id, label_policy_id=label_policy_id,
        requested_accepted_runs=requested_accepted_runs,
        requested_rejected_runs=requested_rejected_runs,
        generation_policy_id=derive_generation_policy_id(probe))


# ---------------------------------------------------------------------------
# Coverage statistics (pure; raw counts before every ratio)
# ---------------------------------------------------------------------------


class DistributionEntry(StrictModel):
    key: str = Field(min_length=1)
    count: int = Field(ge=1)


class PartitionClassCount(StrictModel):
    partition: DatasetPartition
    fault_family: str = Field(min_length=1)
    count: int = Field(ge=1)


class CorpusCoverageStats(StrictModel):
    """Deterministic coverage report for one prepared corpus."""

    schema_version: Literal[1] = 1
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    abstention: int = Field(ge=0)
    partition_counts: DatasetPartitionCounts
    eligible_test_examples: int = Field(ge=0)
    fault_family_distribution: tuple[DistributionEntry, ...] = Field(
        default_factory=tuple)
    scenario_distribution: tuple[DistributionEntry, ...] = Field(
        default_factory=tuple)
    rejection_distribution: tuple[DistributionEntry, ...] = Field(
        default_factory=tuple)
    topology_distribution: tuple[DistributionEntry, ...] = Field(
        default_factory=tuple)
    split_balance: tuple[PartitionClassCount, ...] = Field(
        default_factory=tuple)
    duplicate_feature_content_groups: int = Field(ge=0)
    #: max/min family count as a 6-place decimal string; None with <2 classes.
    class_imbalance_ratio: str | None = None
    topology_imbalance_ratio: str | None = None

    @model_validator(mode="after")
    def _sorted(self) -> CorpusCoverageStats:
        for name in ("fault_family_distribution", "scenario_distribution",
                     "rejection_distribution", "topology_distribution"):
            entries = getattr(self, name)
            keys = [e.key for e in entries]
            if keys != sorted(keys) or len(keys) != len(set(keys)):
                raise ValueError(f"{name} must be key-sorted and unique")
        return self


def _distribution(counter: Counter[str]) -> tuple[DistributionEntry, ...]:
    return tuple(DistributionEntry(key=key, count=count)
                 for key, count in sorted(counter.items()))


def _imbalance(counter: Counter[str]) -> str | None:
    if len(counter) < 2:
        return None
    return ratio_str(max(counter.values()), min(counter.values()))


def compute_corpus_coverage(loaded: LoadedPrepared) -> CorpusCoverageStats:
    """Pure coverage statistics from the prepared corpus (no mutation)."""
    families: Counter[str] = Counter()
    scenarios: Counter[str] = Counter()
    rejections: Counter[str] = Counter()
    topologies: Counter[str] = Counter()
    split: Counter[tuple[str, str]] = Counter()
    content: Counter[str] = Counter()
    accepted = abstention = 0
    for example in loaded.examples:
        topologies[example.features.topology_hash] += 1
        if isinstance(example.labels, AcceptedLabels):
            accepted += 1
            families[example.labels.fault_family] += 1
            scenarios[example.labels.scenario_id] += 1
            split[(example.trace.partition.value,
                   example.labels.fault_family)] += 1
            target = example.labels.fault_family
        else:
            abstention += 1
            rejections[example.labels.rejection_code] += 1
            target = "abstain"
        content[sha256_canonical({
            "features": example.features.model_dump(mode="json"),
            "target": target})] += 1
    duplicates = sum(1 for count in content.values() if count > 1)
    return CorpusCoverageStats(
        total=len(loaded.examples), accepted=accepted, abstention=abstention,
        partition_counts=loaded.manifest.partition_counts,
        eligible_test_examples=loaded.manifest.partition_counts.test,
        fault_family_distribution=_distribution(families),
        scenario_distribution=_distribution(scenarios),
        rejection_distribution=_distribution(rejections),
        topology_distribution=_distribution(topologies),
        split_balance=tuple(
            PartitionClassCount(partition=DatasetPartition(part),
                                fault_family=family, count=count)
            for (part, family), count in sorted(split.items())),
        duplicate_feature_content_groups=duplicates,
        class_imbalance_ratio=_imbalance(families),
        topology_imbalance_ratio=_imbalance(topologies))


# ---------------------------------------------------------------------------
# Structural quality verification (fail-closed; imbalance is REPORTED)
# ---------------------------------------------------------------------------


class CorpusQualityResult(StrictModel):
    """Fail-closed structural verdict + explicit imbalance reports."""

    schema_version: Literal[1] = 1
    verified: bool
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)
    imbalance_reports: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def verify_corpus_quality(loaded: LoadedPrepared) -> CorpusQualityResult:
    """Structural corpus quality: duplicates, leakage, malformed, evidence.

    Fail-closed rules refuse a structurally broken corpus. Class and topology
    imbalance are REPORTS (never silent rebalancing, never a mutation): the
    interpretation layer and humans decide what imbalance means.
    """
    checks: list[DatasetCheck] = []
    examples = loaded.examples

    ids = [e.trace.example_id for e in examples]
    checks.append(_c("unique_example_ids", len(ids) == len(set(ids))))

    group_partitions: dict[str, set[DatasetPartition]] = {}
    for e in examples:
        group_partitions.setdefault(e.trace.group_id, set()).add(
            e.trace.partition)
    leaking = sorted(g for g, parts in group_partitions.items()
                     if len(parts) > 1)
    checks.append(_c("no_split_leakage", not leaking,
                     ",".join(leaking[:5])))

    malformed = []
    missing_evidence = []
    for e in examples:
        accepted_kind = e.trace.example_kind is DatasetExampleKind.ACCEPTED_FAULT
        if accepted_kind != isinstance(e.labels, AcceptedLabels):
            malformed.append(e.trace.example_id)
        if accepted_kind and e.trace.partition is DatasetPartition.ABSTENTION:
            malformed.append(e.trace.example_id)
        if (isinstance(e.labels, AbstentionLabels)
                and e.trace.partition is not DatasetPartition.ABSTENTION):
            malformed.append(e.trace.example_id)
        if not e.features.baseline_evidence.relative_path:
            missing_evidence.append(e.trace.example_id)
        if accepted_kind and e.features.onset_evidence is None:
            missing_evidence.append(e.trace.example_id)
    checks.append(_c("no_malformed_examples", not malformed,
                     ",".join(sorted(set(malformed))[:5])))
    checks.append(_c("no_missing_evidence", not missing_evidence,
                     ",".join(sorted(set(missing_evidence))[:5])))

    policies = {e.features.feature_policy_id for e in examples}
    checks.append(_c("uniform_feature_policy", len(policies) <= 1))
    label_policies = {e.labels.label_policy_id for e in examples}
    checks.append(_c("uniform_label_policy", len(label_policies) <= 1))

    coverage = compute_corpus_coverage(loaded)
    reports = [
        f"duplicate_feature_content_groups={coverage.duplicate_feature_content_groups}",
        f"class_imbalance_ratio={coverage.class_imbalance_ratio}",
        f"topology_imbalance_ratio={coverage.topology_imbalance_ratio}",
        f"eligible_test_examples={coverage.eligible_test_examples}",
    ]
    return CorpusQualityResult(
        verified=all(c.passed for c in checks), checks=tuple(checks),
        imbalance_reports=tuple(reports))


# ---------------------------------------------------------------------------
# Versioned registration: evaluation-corpora/<evalcorpus-…>/
# ---------------------------------------------------------------------------


def derive_evaluation_corpus_id(
    *,
    corpus_version: int,
    prepared_digest: str,
    generation_policy_id: str,
    provenance: CorpusProvenance,
) -> str:
    payload = {
        "corpus_version": corpus_version,
        "prepared_digest": prepared_digest,
        "generation_policy_id": generation_policy_id,
        "provenance": provenance.value,
    }
    return "evalcorpus-" + sha256_canonical(payload)[:16]


def compute_evaluation_corpus_digest(
    *,
    schema_version: int,
    corpus_format_version: int,
    evaluation_corpus_id: str,
    corpus_version: int,
    prepared_digest: str,
    generation_policy_id: str,
    provenance: str,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    payload = {
        "schema_version": schema_version,
        "corpus_format_version": corpus_format_version,
        "evaluation_corpus_id": evaluation_corpus_id,
        "corpus_version": corpus_version,
        "prepared_digest": prepared_digest,
        "generation_policy_id": generation_policy_id,
        "provenance": provenance,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256,
             "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "ecdig-" + sha256_canonical(payload)[:24]


class EvaluationCorpusManifest(StrictModel):
    """The immutable registration of ONE prepared corpus as a project version."""

    schema_version: Literal[1] = 1
    corpus_format_version: Literal[1] = 1
    corpus_version: int = Field(ge=1)
    provenance: CorpusProvenance
    prepared_digest: str = Field(min_length=1)
    source_dataset_digest: str | None = None
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    generation_policy: EvaluationCorpusGenerationPolicy
    coverage: CorpusCoverageStats
    quality_verified: Literal[True] = True
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    evaluation_corpus_id: str = Field(min_length=1)
    corpus_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationCorpusManifest:
        if self.generation_policy.feature_policy_id != self.feature_policy_id:
            raise ValueError("generation policy binds a different feature policy")
        if self.generation_policy.label_policy_id != self.label_policy_id:
            raise ValueError("generation policy binds a different label policy")
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        expected_id = derive_evaluation_corpus_id(
            corpus_version=self.corpus_version,
            prepared_digest=self.prepared_digest,
            generation_policy_id=self.generation_policy.generation_policy_id,
            provenance=self.provenance)
        if self.evaluation_corpus_id != expected_id:
            raise ValueError("evaluation_corpus_id does not match the content")
        expected_digest = compute_evaluation_corpus_digest(
            schema_version=self.schema_version,
            corpus_format_version=self.corpus_format_version,
            evaluation_corpus_id=self.evaluation_corpus_id,
            corpus_version=self.corpus_version,
            prepared_digest=self.prepared_digest,
            generation_policy_id=self.generation_policy.generation_policy_id,
            provenance=self.provenance.value, generated_by=self.generated_by,
            files=self.files)
        if self.corpus_digest != expected_digest:
            raise ValueError("corpus_digest does not match the content")
        return self


@dataclass(frozen=True)
class WrittenEvaluationCorpus:
    root: Path
    evaluation_corpus_id: str
    corpus_digest: str
    corpus_version: int


def register_evaluation_corpus(
    loaded: LoadedPrepared,
    *,
    corpus_version: int,
    provenance: CorpusProvenance,
    generation_policy: EvaluationCorpusGenerationPolicy,
    corpora_root: str | Path,
) -> WrittenEvaluationCorpus:
    """Register a VERIFIED, quality-passing prepared corpus as a version.

    Fail-closed: a corpus failing structural quality verification cannot be
    registered; policy/prepared mismatches refuse; existing registrations are
    never overwritten. The prepared corpus itself is never touched.
    """
    manifest = loaded.manifest
    if generation_policy.feature_policy_id != manifest.feature_policy_id:
        raise EvaluationCorpusError(
            "generation policy feature_policy_id does not match the corpus")
    if generation_policy.label_policy_id != manifest.label_policy_id:
        raise EvaluationCorpusError(
            "generation policy label_policy_id does not match the corpus")
    quality = verify_corpus_quality(loaded)
    if not quality.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in quality.failures)
        raise EvaluationCorpusError(
            f"corpus failed structural quality verification: {detail}")
    coverage = compute_corpus_coverage(loaded)

    coverage_payload = canonical_json_bytes(coverage)
    quality_payload = canonical_json_bytes(quality)
    content = {COVERAGE_FILE: coverage_payload, QUALITY_FILE: quality_payload}
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path))
    corpus_id = derive_evaluation_corpus_id(
        corpus_version=corpus_version, prepared_digest=manifest.prepared_digest,
        generation_policy_id=generation_policy.generation_policy_id,
        provenance=provenance)
    registration = EvaluationCorpusManifest(
        corpus_version=corpus_version, provenance=provenance,
        prepared_digest=manifest.prepared_digest,
        source_dataset_digest=manifest.source_dataset_digest,
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        generation_policy=generation_policy, coverage=coverage,
        generated_by=EVALUATION_CORPUS_GENERATOR, files=files,
        evaluation_corpus_id=corpus_id,
        corpus_digest=compute_evaluation_corpus_digest(
            schema_version=1,
            corpus_format_version=EVALUATION_CORPUS_FORMAT_VERSION,
            evaluation_corpus_id=corpus_id, corpus_version=corpus_version,
            prepared_digest=manifest.prepared_digest,
            generation_policy_id=generation_policy.generation_policy_id,
            provenance=provenance.value,
            generated_by=EVALUATION_CORPUS_GENERATOR, files=files))

    root = Path(corpora_root) / corpus_id
    if root.exists() and any(root.iterdir()):
        raise EvaluationCorpusError(f"corpus version already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / EVALCORPUS_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    for rel, payload in content.items():
        atomic_write_bytes(root / rel, payload)
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(registration))
    verification = verify_evaluation_corpus(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise EvaluationCorpusError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenEvaluationCorpus(
        root=root, evaluation_corpus_id=corpus_id,
        corpus_digest=registration.corpus_digest,
        corpus_version=corpus_version)


class EvaluationCorpusVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    corpus_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def verify_evaluation_corpus(
    corpus_dir: str | Path,
) -> EvaluationCorpusVerificationResult:
    """Verify the REGISTRATION artifact (hashes, digest, embed-consistency)."""
    root = Path(corpus_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("corpus_dir_present", False, str(root)))
        return EvaluationCorpusVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("corpus_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / EVALCORPUS_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return EvaluationCorpusVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = EvaluationCorpusManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return EvaluationCorpusVerificationResult(
            verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))

    on_disk = {str(p.relative_to(root)) for p in root.rglob("*")
               if p.is_file() and p.name != EVALCORPUS_INCOMPLETE_MARKER}
    allowed = EXPECTED_EVALCORPUS_FILES | {MANIFEST_FILE}
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

    embed_ok = True
    if hash_ok:
        try:
            coverage = CorpusCoverageStats.model_validate_json(
                (root / COVERAGE_FILE).read_bytes())
            quality = CorpusQualityResult.model_validate_json(
                (root / QUALITY_FILE).read_bytes())
        except ValidationError:
            embed_ok = False
        else:
            embed_ok = (coverage == manifest.coverage
                        and quality.verified is True)
    checks.append(_c("embedded_reports_consistent", embed_ok))

    return EvaluationCorpusVerificationResult(
        verified=all(c.passed for c in checks),
        corpus_digest=manifest.corpus_digest, checks=tuple(checks))


@dataclass(frozen=True)
class LoadedEvaluationCorpus:
    manifest: EvaluationCorpusManifest
    coverage: CorpusCoverageStats
    quality: CorpusQualityResult


def read_evaluation_corpus(corpus_dir: str | Path) -> LoadedEvaluationCorpus:
    """Verify then reconstruct a corpus registration; fail closed."""
    root = Path(corpus_dir)
    result = verify_evaluation_corpus(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise EvaluationCorpusError(
            f"evaluation corpus failed verification: {detail}")
    return LoadedEvaluationCorpus(
        manifest=EvaluationCorpusManifest.model_validate_json(
            (root / MANIFEST_FILE).read_bytes()),
        coverage=CorpusCoverageStats.model_validate_json(
            (root / COVERAGE_FILE).read_bytes()),
        quality=CorpusQualityResult.model_validate_json(
            (root / QUALITY_FILE).read_bytes()))


def audit_evaluation_corpus(
    corpus_dir: str | Path, loaded: LoadedPrepared,
) -> tuple[bool, tuple[DatasetCheck, ...]]:
    """Recompute EVERYTHING against the actual prepared corpus; fail closed.

    The registration verifier above checks the artifact alone; this audit
    additionally proves the registration still describes the given prepared
    corpus: prepared digest binding, freshly recomputed coverage equality, and
    freshly recomputed quality verification.
    """
    registration = read_evaluation_corpus(corpus_dir)
    checks: list[DatasetCheck] = []
    checks.append(_c(
        "prepared_digest_matches",
        registration.manifest.prepared_digest
        == loaded.manifest.prepared_digest))
    checks.append(_c("coverage_recomputes",
                     compute_corpus_coverage(loaded) == registration.coverage))
    fresh_quality = verify_corpus_quality(loaded)
    checks.append(_c("quality_recomputes",
                     fresh_quality.verified
                     and fresh_quality.checks == registration.quality.checks))
    return all(c.passed for c in checks), tuple(checks)


def _read_manifest_only(path: Path) -> EvaluationCorpusManifest | None:
    try:
        return EvaluationCorpusManifest.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def list_evaluation_corpus_versions(
    corpora_root: str | Path,
) -> tuple[EvaluationCorpusManifest, ...]:
    """Deterministically list VERIFIED registrations, ordered by version, id."""
    root = Path(corpora_root)
    if not root.is_dir():
        return ()
    out: list[EvaluationCorpusManifest] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not verify_evaluation_corpus(child).verified:
            continue
        manifest = _read_manifest_only(child / MANIFEST_FILE)
        if manifest is not None:
            out.append(manifest)
    return tuple(sorted(
        out, key=lambda m: (m.corpus_version, m.evaluation_corpus_id)))
