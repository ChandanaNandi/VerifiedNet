"""Identity-first coverage planning + evaluation readiness (Gate 14B).

Gate 14's central lesson: corpus v2 reached 22 eligible test EXAMPLES but
only 5 distinct held-out test IDENTITIES — repeated executions inflate row
counts without improving independent coverage. Gate 14B therefore plans by
STABLE SCENARIO IDENTITY first:

* ``PartitionIdentityCoverage`` counts distinct leakage groups per partition
  (the diversity facts row counts hide);
* ``IdentityCoveragePolicy`` freezes the mandatory identity minimums and the
  per-partition run-allocation rule (2-4 accepted runs per identity — repeats
  are for reproducibility, never for threshold padding);
* the identity-first planner selects candidates from a COMPLETE approved pool
  in an explicit deterministic priority order — missing test identity, then
  missing validation identity, then underrepresented family, then
  underrepresented topology, then missing parameter dimension, then rejected
  coverage, then reproducibility repeats — tie-broken by canonical stable
  identity (lexicographic ``group_id``);
* ``EvaluationReadinessAssessment`` renders the fail-closed verdict that
  governs whether a controlled experiment (Gate 15) may be authorised:
  ``ready_for_controlled_experiment`` requires BOTH the example thresholds
  AND the identity-diversity thresholds; meeting counts alone yields
  ``coverage_threshold_met_but_low_diversity``.

Split assignment stays the production splitter's alone: the planner PREDICTS
partitions with the exact production function over fully-defined identities
and can never move, force, or exclude an example by partition. No model
loads, no evaluation runs, no benchmark facts — structurally, there are no
fields to put them in.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import (
    DatasetFileHash,
    DatasetPartition,
    SplitPolicy,
)
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.datasets.splitting import assign_group_split, split_policy_id
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.corpusexpansion import (
    CAMPAIGN_INCOMPLETE_MARKER,
    MANIFEST_FILE,
    SUMMARY_FILE,
    CampaignVerificationResult,
    CandidateScenario,
    CorpusComparisonReport,
    CorpusDelta,
    CorpusExpansionError,
    EvaluationCorpusExpansionPolicy,
    ExpansionTargetResult,
    build_corpus_comparison,
    build_expansion_policy,
)
from verifiednet.evaluation.evalcorpus import (
    EvaluationCorpusManifest,
    LoadedEvaluationCorpus,
)
from verifiednet.schemas.base import StrictModel

IDENTITY_COVERAGE_GENERATOR = "verifiednet.evaluation.identitycoverage"

#: The explicit Gate 14B priority order (rank = index). Rejected coverage and
#: reproducibility repeats are the sixth and seventh priorities — they shape
#: ``planned_rejected_*`` and the per-identity run counts rather than adding
#: selection entries of their own.
IDENTITY_PRIORITY_RULES: tuple[str, ...] = (
    "missing_test_identity",
    "missing_validation_identity",
    "underrepresented_family",
    "underrepresented_topology",
    "missing_parameter_dimension",
)

PriorityRule = Literal[
    "missing_test_identity",
    "missing_validation_identity",
    "underrepresented_family",
    "underrepresented_topology",
    "missing_parameter_dimension",
]

ReadinessOutcome = Literal[
    "ready_for_controlled_experiment",
    "coverage_threshold_met_but_low_diversity",
    "underpowered",
    "quality_failed",
]


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


# ---------------------------------------------------------------------------
# Per-partition identity coverage (the facts row counts hide)
# ---------------------------------------------------------------------------


class PartitionIdentityCounts(StrictModel):
    """Distinct stable-identity (leakage-group) counts per partition."""

    schema_version: Literal[1] = 1
    train_identities: int = Field(ge=0)
    validation_identities: int = Field(ge=0)
    test_identities: int = Field(ge=0)
    abstention_identities: int = Field(ge=0)


class PartitionIdentityCoverage(StrictModel):
    """Distinct leakage groups per partition of ONE prepared corpus.

    Partitions must be disjoint at the group level — overlap is split
    leakage, which this model refuses to represent at all.
    """

    schema_version: Literal[1] = 1
    prepared_digest: str = Field(min_length=1)
    train_group_ids: tuple[str, ...] = Field(default_factory=tuple)
    validation_group_ids: tuple[str, ...] = Field(default_factory=tuple)
    test_group_ids: tuple[str, ...] = Field(default_factory=tuple)
    abstention_group_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _valid(self) -> PartitionIdentityCoverage:
        names = ("train_group_ids", "validation_group_ids",
                 "test_group_ids", "abstention_group_ids")
        seen: set[str] = set()
        for name in names:
            values = getattr(self, name)
            if list(values) != sorted(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
            overlap = seen & set(values)
            if overlap:
                raise ValueError(
                    "partition identity sets overlap (split leakage): "
                    + ",".join(sorted(overlap)[:5]))
            seen |= set(values)
        return self

    @property
    def counts(self) -> PartitionIdentityCounts:
        return PartitionIdentityCounts(
            train_identities=len(self.train_group_ids),
            validation_identities=len(self.validation_group_ids),
            test_identities=len(self.test_group_ids),
            abstention_identities=len(self.abstention_group_ids))


def compute_partition_identity_coverage(
    loaded: LoadedPrepared,
) -> PartitionIdentityCoverage:
    """Pure per-partition identity coverage from a verified prepared corpus."""
    groups: dict[str, set[str]] = {
        "train": set(), "validation": set(), "test": set(),
        "abstention": set()}
    for example in loaded.examples:
        groups[example.trace.partition.value].add(example.trace.group_id)
    return PartitionIdentityCoverage(
        prepared_digest=loaded.manifest.prepared_digest,
        train_group_ids=tuple(sorted(groups["train"])),
        validation_group_ids=tuple(sorted(groups["validation"])),
        test_group_ids=tuple(sorted(groups["test"])),
        abstention_group_ids=tuple(sorted(groups["abstention"])))


# ---------------------------------------------------------------------------
# Identity-coverage policy (frozen; mandatory identity minimums + run rule)
# ---------------------------------------------------------------------------


class IdentityCoveragePolicy(StrictModel):
    """Mandatory identity-diversity minimums + the run-allocation rule.

    Complements (never replaces) the example-count expansion policy it binds:
    the expansion policy gates row-count coverage; THIS policy gates
    independent held-out identity coverage and fixes how many reproducibility
    runs each selected identity receives per predicted partition. Every
    per-partition run count must sit inside the [min, max] accepted-runs
    bounds — repeats are reproducibility evidence, never threshold padding.
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    expansion_policy_id: str = Field(min_length=1)
    min_distinct_test_identities: int = Field(ge=1)
    min_distinct_validation_identities: int = Field(ge=1)
    min_topology_variants: int = Field(ge=1)
    min_runs_per_identity: int = Field(ge=1)
    max_runs_per_identity: int = Field(ge=1)
    runs_per_test_identity: int = Field(ge=1)
    runs_per_validation_identity: int = Field(ge=1)
    runs_per_train_identity: int = Field(ge=1)
    rejected_runs_per_identity: int = Field(ge=1)
    identity_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> IdentityCoveragePolicy:
        if self.max_runs_per_identity < self.min_runs_per_identity:
            raise ValueError("max_runs_per_identity < min_runs_per_identity")
        for name in ("runs_per_test_identity", "runs_per_validation_identity",
                     "runs_per_train_identity"):
            value = getattr(self, name)
            if not (self.min_runs_per_identity <= value
                    <= self.max_runs_per_identity):
                raise ValueError(
                    f"{name}={value} outside "
                    f"[{self.min_runs_per_identity}, "
                    f"{self.max_runs_per_identity}]")
        if self.identity_policy_id != derive_identity_policy_id(self):
            raise ValueError(
                "identity_policy_id does not match the policy content")
        return self

    def runs_for_partition(self, partition: DatasetPartition) -> int:
        rule = {
            DatasetPartition.TEST: self.runs_per_test_identity,
            DatasetPartition.VALIDATION: self.runs_per_validation_identity,
            DatasetPartition.TRAIN: self.runs_per_train_identity,
        }
        try:
            return rule[partition]
        except KeyError as exc:
            raise CorpusExpansionError(
                f"no accepted-run rule for partition {partition.value!r}",
            ) from exc


def derive_identity_policy_id(policy: IdentityCoveragePolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("identity_policy_id", None)
    return "icpol-" + sha256_canonical(payload)[:16]


def build_identity_coverage_policy(
    *,
    expansion_policy_id: str,
    min_distinct_test_identities: int = 8,
    min_distinct_validation_identities: int = 6,
    min_topology_variants: int = 4,
    min_runs_per_identity: int = 2,
    max_runs_per_identity: int = 4,
    runs_per_test_identity: int = 3,
    runs_per_validation_identity: int = 3,
    runs_per_train_identity: int = 4,
    rejected_runs_per_identity: int = 2,
) -> IdentityCoveragePolicy:
    probe = IdentityCoveragePolicy.model_construct(
        expansion_policy_id=expansion_policy_id,
        min_distinct_test_identities=min_distinct_test_identities,
        min_distinct_validation_identities=min_distinct_validation_identities,
        min_topology_variants=min_topology_variants,
        min_runs_per_identity=min_runs_per_identity,
        max_runs_per_identity=max_runs_per_identity,
        runs_per_test_identity=runs_per_test_identity,
        runs_per_validation_identity=runs_per_validation_identity,
        runs_per_train_identity=runs_per_train_identity,
        rejected_runs_per_identity=rejected_runs_per_identity)
    return IdentityCoveragePolicy(
        expansion_policy_id=expansion_policy_id,
        min_distinct_test_identities=min_distinct_test_identities,
        min_distinct_validation_identities=min_distinct_validation_identities,
        min_topology_variants=min_topology_variants,
        min_runs_per_identity=min_runs_per_identity,
        max_runs_per_identity=max_runs_per_identity,
        runs_per_test_identity=runs_per_test_identity,
        runs_per_validation_identity=runs_per_validation_identity,
        runs_per_train_identity=runs_per_train_identity,
        rejected_runs_per_identity=rejected_runs_per_identity,
        identity_policy_id=derive_identity_policy_id(probe))


def build_expansion_policy_v3(
    *,
    source_corpus_id: str,
    source_corpus_digest: str,
) -> EvaluationCorpusExpansionPolicy:
    """The Gate 14B corpus-v3 example-count targets (all MANDATORY)."""
    return build_expansion_policy(
        source_corpus_id=source_corpus_id,
        source_corpus_digest=source_corpus_digest,
        min_total_examples=220,
        min_accepted_examples=196,
        min_abstention_examples=16,
        min_validation_accepted=24,
        min_test_accepted=30,
        min_examples_per_family=15,
        min_identities_per_family=4,
        max_class_imbalance_ratio="1.500000",
        required_rejection_codes=("precondition_failed",),
        advisory_min_topology_variants=4,
        advisory_max_duplicate_content_ratio="0.200000")


# ---------------------------------------------------------------------------
# Identity-target assessment (merged into the same fail-closed binding gate)
# ---------------------------------------------------------------------------


def assess_identity_coverage(
    coverage: PartitionIdentityCoverage,
    *,
    topology_variants: int,
    policy: IdentityCoveragePolicy,
) -> ExpansionTargetResult:
    """Deterministic identity-diversity verdict for a candidate corpus."""
    counts = coverage.counts
    checks = (
        _c("min_distinct_test_identities",
           counts.test_identities >= policy.min_distinct_test_identities,
           str(counts.test_identities)),
        _c("min_distinct_validation_identities",
           counts.validation_identities
           >= policy.min_distinct_validation_identities,
           str(counts.validation_identities)),
        _c("min_topology_variants",
           topology_variants >= policy.min_topology_variants,
           str(topology_variants)),
    )
    advisory = (
        f"distinct_train_identities={counts.train_identities}",
        f"distinct_abstention_identities={counts.abstention_identities}",
    )
    return ExpansionTargetResult(
        satisfied=all(c.passed for c in checks), checks=checks,
        advisory_findings=advisory)


def combine_target_results(
    first: ExpansionTargetResult, second: ExpansionTargetResult,
) -> ExpansionTargetResult:
    """Merge two target verdicts into ONE fail-closed gate; rules must not
    collide (a silently shadowed rule would weaken the gate)."""
    rules = [c.rule for c in first.checks] + [c.rule for c in second.checks]
    if len(rules) != len(set(rules)):
        raise CorpusExpansionError(
            "target results share rule names; refusing to merge")
    return ExpansionTargetResult(
        satisfied=first.satisfied and second.satisfied,
        checks=first.checks + second.checks,
        advisory_findings=first.advisory_findings + second.advisory_findings)


# ---------------------------------------------------------------------------
# The identity-first planner (explicit deterministic priority order)
# ---------------------------------------------------------------------------


class SelectionEntry(StrictModel):
    """One selected identity: WHY it was selected and how many runs it gets."""

    schema_version: Literal[1] = 1
    candidate: CandidateScenario
    predicted_partition: DatasetPartition
    priority_rule: PriorityRule


class IdentityFirstSelection(StrictModel):
    """The frozen, content-addressed output of the identity-first planner.

    Entries are ordered by (priority rank, canonical stable identity) — the
    order IS the audit trail of the priority rules. Rejected coverage is the
    sixth priority (planned identities x runs, recorded here); the
    per-identity run counts are the seventh (reproducibility repeats).
    """

    schema_version: Literal[1] = 1
    selection_version: Literal[1] = 1
    expansion_policy_id: str = Field(min_length=1)
    identity_policy_id: str = Field(min_length=1)
    split_policy_id: str = Field(min_length=1)
    pool_size: int = Field(ge=1)
    entries: tuple[SelectionEntry, ...] = Field(min_length=1)
    planned_rejected_identities: int = Field(ge=0)
    planned_rejected_runs: int = Field(ge=0)
    selection_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> IdentityFirstSelection:
        rank = {rule: i for i, rule in enumerate(IDENTITY_PRIORITY_RULES)}
        keys = [(rank[e.priority_rule], e.candidate.group_id)
                for e in self.entries]
        if keys != sorted(keys):
            raise ValueError(
                "entries must be ordered by (priority rank, group_id)")
        groups = [e.candidate.group_id for e in self.entries]
        if len(groups) != len(set(groups)):
            raise ValueError("selected identities must be unique")
        if len(self.entries) > self.pool_size:
            raise ValueError("selection cannot exceed the candidate pool")
        if self.selection_id != derive_identity_selection_id(self):
            raise ValueError("selection_id does not match the content")
        return self

    @property
    def identity_counts(self) -> PartitionIdentityCounts:
        counter: Counter[str] = Counter(
            e.predicted_partition.value for e in self.entries)
        return PartitionIdentityCounts(
            train_identities=counter.get("train", 0),
            validation_identities=counter.get("validation", 0),
            test_identities=counter.get("test", 0),
            abstention_identities=self.planned_rejected_identities)

    @property
    def planned_accepted_runs(self) -> int:
        return sum(e.candidate.planned_runs for e in self.entries)


def derive_identity_selection_id(selection: IdentityFirstSelection) -> str:
    payload = selection.model_dump(mode="json")
    payload.pop("selection_id", None)
    return "icsel-" + sha256_canonical(payload)[:16]


def plan_identity_first_selection(
    pool: tuple[CandidateScenario, ...],
    *,
    expansion_policy: EvaluationCorpusExpansionPolicy,
    identity_policy: IdentityCoveragePolicy,
    split_policy: SplitPolicy,
    planned_rejected_identities: int,
) -> IdentityFirstSelection:
    """Select identities from a COMPLETE approved pool, identity-first.

    Deterministic priority order (tie-break: lexicographic ``group_id``):

    1. missing_test_identity — every pool identity the production splitter
       assigns to the held-out test partition (the selection starts empty, so
       each is a missing independent test identity);
    2. missing_validation_identity — likewise for validation;
    3. underrepresented_family — remaining candidates of every family whose
       projected accepted examples sit strictly below the current maximum
       family projection (added while still below it);
    4. underrepresented_topology — a canonically-first candidate for any
       approved topology context still absent from the selection;
    5. missing_parameter_dimension — a canonically-first candidate for any
       approved parameter combination (case id) still absent;
    6. rejected coverage — ``planned_rejected_identities`` at the policy's
       rejected-run count (recorded, not entry-producing);
    7. reproducibility repeats — the per-partition accepted-run counts.

    Input pool order cannot matter (everything is keyed and sorted by
    ``group_id``); pool ``planned_runs`` values are ignored — run allocation
    comes only from the frozen identity policy. The planner PREDICTS
    partitions with the exact production splitter; it has no way to assign,
    move, or exclude an example by partition.
    """
    if identity_policy.expansion_policy_id \
            != expansion_policy.expansion_policy_id:
        raise CorpusExpansionError(
            "identity policy binds a different expansion policy")
    by_group: dict[str, CandidateScenario] = {}
    for candidate in pool:
        existing = by_group.get(candidate.group_id)
        if existing is not None and existing.identity != candidate.identity:
            raise CorpusExpansionError(
                f"pool contains conflicting candidates for group "
                f"{candidate.group_id}")
        by_group[candidate.group_id] = candidate
    if not by_group:
        raise CorpusExpansionError("candidate pool is empty")
    ordered_groups = tuple(sorted(by_group))
    partition: dict[str, DatasetPartition] = {
        group: assign_group_split(group_id=group, policy=split_policy)
        for group in ordered_groups}

    selected: dict[str, PriorityRule] = {}

    # P1 + P2: every held-out identity the pool can contribute.
    for group in ordered_groups:
        if partition[group] is DatasetPartition.TEST:
            selected[group] = "missing_test_identity"
    for group in ordered_groups:
        if group not in selected \
                and partition[group] is DatasetPartition.VALIDATION:
            selected[group] = "missing_validation_identity"

    # P3: families strictly below the current maximum family projection.
    projection: Counter[str] = Counter()
    for candidate in by_group.values():
        projection.setdefault(candidate.fault_family, 0)
    for group, _rule in selected.items():
        projection[by_group[group].fault_family] += \
            identity_policy.runs_for_partition(partition[group])
    maximum = max(projection.values())
    for group in ordered_groups:
        if group in selected:
            continue
        family = by_group[group].fault_family
        if projection[family] < maximum:
            selected[group] = "underrepresented_family"
            projection[family] += \
                identity_policy.runs_for_partition(partition[group])

    # P4: approved topology contexts still absent from the selection.
    covered_topologies = {
        by_group[g].identity.topology_hash for g in selected}
    pool_topologies = sorted(
        {c.identity.topology_hash for c in by_group.values()})
    for topology_hash in pool_topologies:
        if topology_hash in covered_topologies:
            continue
        for group in ordered_groups:
            if group not in selected \
                    and by_group[group].identity.topology_hash \
                    == topology_hash:
                selected[group] = "underrepresented_topology"
                covered_topologies.add(topology_hash)
                break

    # P5: approved parameter combinations (case ids) still absent.
    covered_cases = {by_group[g].case_id for g in selected}
    pool_cases = sorted({c.case_id for c in by_group.values()})
    for case_id in pool_cases:
        if case_id in covered_cases:
            continue
        for group in ordered_groups:
            if group not in selected \
                    and by_group[group].case_id == case_id:
                selected[group] = "missing_parameter_dimension"
                covered_cases.add(case_id)
                break

    rank = {rule: i for i, rule in enumerate(IDENTITY_PRIORITY_RULES)}
    entries: list[SelectionEntry] = []
    for group, rule in selected.items():
        candidate = by_group[group]
        runs = identity_policy.runs_for_partition(partition[group])
        entries.append(SelectionEntry(
            candidate=CandidateScenario(
                case_id=candidate.case_id,
                fault_family=candidate.fault_family,
                identity=candidate.identity, planned_runs=runs),
            predicted_partition=partition[group],
            priority_rule=rule))
    entries.sort(key=lambda e: (rank[e.priority_rule], e.candidate.group_id))

    rejected_runs = planned_rejected_identities \
        * identity_policy.rejected_runs_per_identity
    probe = IdentityFirstSelection.model_construct(
        expansion_policy_id=expansion_policy.expansion_policy_id,
        identity_policy_id=identity_policy.identity_policy_id,
        split_policy_id=split_policy_id(split_policy),
        pool_size=len(by_group), entries=tuple(entries),
        planned_rejected_identities=planned_rejected_identities,
        planned_rejected_runs=rejected_runs)
    return IdentityFirstSelection(
        expansion_policy_id=expansion_policy.expansion_policy_id,
        identity_policy_id=identity_policy.identity_policy_id,
        split_policy_id=split_policy_id(split_policy),
        pool_size=len(by_group), entries=tuple(entries),
        planned_rejected_identities=planned_rejected_identities,
        planned_rejected_runs=rejected_runs,
        selection_id=derive_identity_selection_id(probe))


# ---------------------------------------------------------------------------
# Immutable selection store: identity-selections/<icsel-…>/
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrittenIdentitySelection:
    root: Path
    selection_id: str
    selection_digest: str


class IdentitySelectionManifest(StrictModel):
    schema_version: Literal[1] = 1
    selection_id: str = Field(min_length=1)
    expansion_policy_id: str = Field(min_length=1)
    identity_policy_id: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    selection_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> IdentitySelectionManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        if self.selection_digest != _selection_digest(
                selection_id=self.selection_id,
                expansion_policy_id=self.expansion_policy_id,
                identity_policy_id=self.identity_policy_id,
                generated_by=self.generated_by, files=self.files):
            raise ValueError("selection_digest does not match the content")
        return self


def _selection_digest(
    *,
    selection_id: str,
    expansion_policy_id: str,
    identity_policy_id: str,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    payload = {
        "selection_id": selection_id,
        "expansion_policy_id": expansion_policy_id,
        "identity_policy_id": identity_policy_id,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256,
             "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)],
    }
    return "icseldig-" + sha256_canonical(payload)[:24]


def write_identity_selection(
    selection: IdentityFirstSelection, selections_root: str | Path,
) -> WrittenIdentitySelection:
    """Write ``identity-selections/<selection_id>/``; never overwrite."""
    summary_payload = canonical_json_bytes(selection)
    files = (DatasetFileHash(relative_path=SUMMARY_FILE,
                             sha256=sha256_bytes(summary_payload),
                             size=len(summary_payload)),)
    manifest = IdentitySelectionManifest(
        selection_id=selection.selection_id,
        expansion_policy_id=selection.expansion_policy_id,
        identity_policy_id=selection.identity_policy_id,
        generated_by=IDENTITY_COVERAGE_GENERATOR, files=files,
        selection_digest=_selection_digest(
            selection_id=selection.selection_id,
            expansion_policy_id=selection.expansion_policy_id,
            identity_policy_id=selection.identity_policy_id,
            generated_by=IDENTITY_COVERAGE_GENERATOR, files=files))
    root = Path(selections_root) / selection.selection_id
    if root.exists() and any(root.iterdir()):
        raise CorpusExpansionError(f"selection already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / CAMPAIGN_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    atomic_write_bytes(root / SUMMARY_FILE, summary_payload)
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(manifest))
    verification = verify_identity_selection(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise CorpusExpansionError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenIdentitySelection(
        root=root, selection_id=selection.selection_id,
        selection_digest=manifest.selection_digest)


def verify_identity_selection(
    selection_dir: str | Path,
) -> CampaignVerificationResult:
    """Verify an identity-selection artifact; fail closed."""
    root = Path(selection_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("selection_dir_present", False, str(root)))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("selection_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / CAMPAIGN_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = IdentitySelectionManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    hash_ok = True
    for fh in manifest.files:
        path = root / fh.relative_path
        raw = path.read_bytes() if path.is_file() else None
        if raw is None or len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok = False
            break
    checks.append(_c("file_hashes_match", hash_ok))
    summary_ok = True
    if hash_ok:
        try:
            selection = IdentityFirstSelection.model_validate_json(
                (root / SUMMARY_FILE).read_bytes())
        except ValidationError:
            summary_ok = False
        else:
            summary_ok = (
                selection.selection_id == manifest.selection_id
                and selection.expansion_policy_id
                == manifest.expansion_policy_id
                and selection.identity_policy_id
                == manifest.identity_policy_id)
    checks.append(_c("summary_binds_manifest", summary_ok))
    return CampaignVerificationResult(
        verified=all(c.passed for c in checks),
        campaign_digest=manifest.selection_digest, checks=tuple(checks))


# ---------------------------------------------------------------------------
# v2-versus-v3 comparison with identity deltas
# ---------------------------------------------------------------------------


def build_corpus_comparison_with_identity_deltas(
    parent_manifest: EvaluationCorpusManifest,
    descendant_manifest: EvaluationCorpusManifest,
    *,
    parent_identities: PartitionIdentityCoverage,
    descendant_identities: PartitionIdentityCoverage,
) -> CorpusComparisonReport:
    """The Gate 14 comparison EXTENDED with per-partition identity deltas.

    Fails closed unless each identity coverage was computed from the exact
    prepared corpus the corresponding registration binds.
    """
    base = build_corpus_comparison(parent_manifest, descendant_manifest)
    if parent_identities.prepared_digest != parent_manifest.prepared_digest:
        raise CorpusExpansionError(
            "parent identity coverage does not match the parent corpus")
    if descendant_identities.prepared_digest \
            != descendant_manifest.prepared_digest:
        raise CorpusExpansionError(
            "descendant identity coverage does not match the descendant "
            "corpus")
    before, after = parent_identities.counts, descendant_identities.counts
    identity_deltas = (
        CorpusDelta(metric="distinct_abstention_identities",
                    before=before.abstention_identities,
                    after=after.abstention_identities),
        CorpusDelta(metric="distinct_identities_total",
                    before=before.train_identities
                    + before.validation_identities
                    + before.test_identities
                    + before.abstention_identities,
                    after=after.train_identities
                    + after.validation_identities
                    + after.test_identities
                    + after.abstention_identities),
        CorpusDelta(metric="distinct_test_identities",
                    before=before.test_identities,
                    after=after.test_identities),
        CorpusDelta(metric="distinct_train_identities",
                    before=before.train_identities,
                    after=after.train_identities),
        CorpusDelta(metric="distinct_validation_identities",
                    before=before.validation_identities,
                    after=after.validation_identities),
    )
    deltas = tuple(sorted((*base.deltas, *identity_deltas),
                          key=lambda d: d.metric))
    return CorpusComparisonReport(
        parent_corpus_id=base.parent_corpus_id,
        parent_corpus_digest=base.parent_corpus_digest,
        descendant_corpus_id=base.descendant_corpus_id,
        descendant_corpus_digest=base.descendant_corpus_digest,
        deltas=deltas,
        class_imbalance_before=base.class_imbalance_before,
        class_imbalance_after=base.class_imbalance_after,
        targets_met=base.targets_met, targets_unmet=base.targets_unmet,
        advisory_findings=base.advisory_findings,
        comparison_id=base.comparison_id)


# ---------------------------------------------------------------------------
# Evaluation readiness assessment (governs Gate 15 authorisation)
# ---------------------------------------------------------------------------


def derive_readiness_outcome(
    *,
    quality_verified: bool,
    eligible_test_examples: int,
    validation_accepted: int,
    distinct_test_identities: int,
    distinct_validation_identities: int,
    topology_variants: int,
    min_test_accepted: int,
    min_validation_accepted: int,
    min_distinct_test_identities: int,
    min_distinct_validation_identities: int,
    min_topology_variants: int,
) -> ReadinessOutcome:
    """The deterministic outcome rule — identity diversity gates readiness.

    Example thresholds alone can NEVER authorise an experiment: a corpus that
    meets every row-count target with too few independent held-out identities
    is ``coverage_threshold_met_but_low_diversity`` (the Gate 14 v2 verdict).
    """
    if not quality_verified:
        return "quality_failed"
    if eligible_test_examples < min_test_accepted \
            or validation_accepted < min_validation_accepted:
        return "underpowered"
    if distinct_test_identities < min_distinct_test_identities \
            or distinct_validation_identities \
            < min_distinct_validation_identities \
            or topology_variants < min_topology_variants:
        return "coverage_threshold_met_but_low_diversity"
    return "ready_for_controlled_experiment"


def _readiness_checks(
    assessment: EvaluationReadinessAssessment,
) -> tuple[DatasetCheck, ...]:
    return (
        _c("quality_verified", assessment.quality_verified),
        _c("min_test_accepted",
           assessment.eligible_test_examples >= assessment.min_test_accepted,
           str(assessment.eligible_test_examples)),
        _c("min_validation_accepted",
           assessment.validation_accepted
           >= assessment.min_validation_accepted,
           str(assessment.validation_accepted)),
        _c("min_distinct_test_identities",
           assessment.distinct_test_identities
           >= assessment.min_distinct_test_identities,
           str(assessment.distinct_test_identities)),
        _c("min_distinct_validation_identities",
           assessment.distinct_validation_identities
           >= assessment.min_distinct_validation_identities,
           str(assessment.distinct_validation_identities)),
        _c("min_topology_variants",
           assessment.topology_variants >= assessment.min_topology_variants,
           str(assessment.topology_variants)),
    )


class EvaluationReadinessAssessment(StrictModel):
    """Whether the registered corpus can power a controlled experiment.

    Self-validating: the outcome and every check are RE-DERIVED from the
    recorded facts and thresholds — an assessment claiming readiness its own
    numbers do not support is unrepresentable.
    """

    schema_version: Literal[1] = 1
    assessment_version: Literal[1] = 1
    corpus_id: str = Field(min_length=1)
    corpus_digest: str = Field(min_length=1)
    expansion_policy_id: str = Field(min_length=1)
    identity_policy_id: str = Field(min_length=1)
    quality_verified: bool
    eligible_test_examples: int = Field(ge=0)
    validation_accepted: int = Field(ge=0)
    distinct_test_identities: int = Field(ge=0)
    distinct_validation_identities: int = Field(ge=0)
    distinct_train_identities: int = Field(ge=0)
    distinct_abstention_identities: int = Field(ge=0)
    topology_variants: int = Field(ge=0)
    min_test_accepted: int = Field(ge=0)
    min_validation_accepted: int = Field(ge=0)
    min_distinct_test_identities: int = Field(ge=1)
    min_distinct_validation_identities: int = Field(ge=1)
    min_topology_variants: int = Field(ge=1)
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)
    outcome: ReadinessOutcome
    assessment_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationReadinessAssessment:
        if self.checks != _readiness_checks(self):
            raise ValueError("checks do not match the recorded facts")
        expected = derive_readiness_outcome(
            quality_verified=self.quality_verified,
            eligible_test_examples=self.eligible_test_examples,
            validation_accepted=self.validation_accepted,
            distinct_test_identities=self.distinct_test_identities,
            distinct_validation_identities=self.distinct_validation_identities,
            topology_variants=self.topology_variants,
            min_test_accepted=self.min_test_accepted,
            min_validation_accepted=self.min_validation_accepted,
            min_distinct_test_identities=self.min_distinct_test_identities,
            min_distinct_validation_identities=(
                self.min_distinct_validation_identities),
            min_topology_variants=self.min_topology_variants)
        if self.outcome != expected:
            raise ValueError(
                f"outcome {self.outcome!r} does not follow from the recorded "
                f"facts (expected {expected!r})")
        if self.assessment_id != derive_readiness_assessment_id(self):
            raise ValueError("assessment_id does not match the content")
        return self


def derive_readiness_assessment_id(
    assessment: EvaluationReadinessAssessment,
) -> str:
    payload = assessment.model_dump(mode="json")
    payload.pop("assessment_id", None)
    return "ready-" + sha256_canonical(payload)[:16]


def assess_evaluation_readiness(
    *,
    corpus: LoadedEvaluationCorpus,
    identity_coverage: PartitionIdentityCoverage,
    expansion_policy: EvaluationCorpusExpansionPolicy,
    identity_policy: IdentityCoveragePolicy,
) -> EvaluationReadinessAssessment:
    """Deterministic readiness verdict for ONE registered corpus version."""
    if identity_coverage.prepared_digest != corpus.manifest.prepared_digest:
        raise CorpusExpansionError(
            "identity coverage was computed from a different prepared corpus")
    if identity_policy.expansion_policy_id \
            != expansion_policy.expansion_policy_id:
        raise CorpusExpansionError(
            "identity policy binds a different expansion policy")
    counts = identity_coverage.counts
    coverage = corpus.coverage
    outcome = derive_readiness_outcome(
        quality_verified=corpus.quality.verified,
        eligible_test_examples=coverage.eligible_test_examples,
        validation_accepted=coverage.partition_counts.validation,
        distinct_test_identities=counts.test_identities,
        distinct_validation_identities=counts.validation_identities,
        topology_variants=len(coverage.topology_distribution),
        min_test_accepted=expansion_policy.min_test_accepted,
        min_validation_accepted=expansion_policy.min_validation_accepted,
        min_distinct_test_identities=(
            identity_policy.min_distinct_test_identities),
        min_distinct_validation_identities=(
            identity_policy.min_distinct_validation_identities),
        min_topology_variants=identity_policy.min_topology_variants)
    probe = EvaluationReadinessAssessment.model_construct(
        corpus_id=corpus.manifest.evaluation_corpus_id,
        corpus_digest=corpus.manifest.corpus_digest,
        expansion_policy_id=expansion_policy.expansion_policy_id,
        identity_policy_id=identity_policy.identity_policy_id,
        quality_verified=corpus.quality.verified,
        eligible_test_examples=coverage.eligible_test_examples,
        validation_accepted=coverage.partition_counts.validation,
        distinct_test_identities=counts.test_identities,
        distinct_validation_identities=counts.validation_identities,
        distinct_train_identities=counts.train_identities,
        distinct_abstention_identities=counts.abstention_identities,
        topology_variants=len(coverage.topology_distribution),
        min_test_accepted=expansion_policy.min_test_accepted,
        min_validation_accepted=expansion_policy.min_validation_accepted,
        min_distinct_test_identities=(
            identity_policy.min_distinct_test_identities),
        min_distinct_validation_identities=(
            identity_policy.min_distinct_validation_identities),
        min_topology_variants=identity_policy.min_topology_variants,
        outcome=outcome)
    checks = _readiness_checks(probe)
    probe_with_checks = EvaluationReadinessAssessment.model_construct(
        corpus_id=corpus.manifest.evaluation_corpus_id,
        corpus_digest=corpus.manifest.corpus_digest,
        expansion_policy_id=expansion_policy.expansion_policy_id,
        identity_policy_id=identity_policy.identity_policy_id,
        quality_verified=corpus.quality.verified,
        eligible_test_examples=coverage.eligible_test_examples,
        validation_accepted=coverage.partition_counts.validation,
        distinct_test_identities=counts.test_identities,
        distinct_validation_identities=counts.validation_identities,
        distinct_train_identities=counts.train_identities,
        distinct_abstention_identities=counts.abstention_identities,
        topology_variants=len(coverage.topology_distribution),
        min_test_accepted=expansion_policy.min_test_accepted,
        min_validation_accepted=expansion_policy.min_validation_accepted,
        min_distinct_test_identities=(
            identity_policy.min_distinct_test_identities),
        min_distinct_validation_identities=(
            identity_policy.min_distinct_validation_identities),
        min_topology_variants=identity_policy.min_topology_variants,
        checks=checks, outcome=outcome)
    return EvaluationReadinessAssessment(
        corpus_id=corpus.manifest.evaluation_corpus_id,
        corpus_digest=corpus.manifest.corpus_digest,
        expansion_policy_id=expansion_policy.expansion_policy_id,
        identity_policy_id=identity_policy.identity_policy_id,
        quality_verified=corpus.quality.verified,
        eligible_test_examples=coverage.eligible_test_examples,
        validation_accepted=coverage.partition_counts.validation,
        distinct_test_identities=counts.test_identities,
        distinct_validation_identities=counts.validation_identities,
        distinct_train_identities=counts.train_identities,
        distinct_abstention_identities=counts.abstention_identities,
        topology_variants=len(coverage.topology_distribution),
        min_test_accepted=expansion_policy.min_test_accepted,
        min_validation_accepted=expansion_policy.min_validation_accepted,
        min_distinct_test_identities=(
            identity_policy.min_distinct_test_identities),
        min_distinct_validation_identities=(
            identity_policy.min_distinct_validation_identities),
        min_topology_variants=identity_policy.min_topology_variants,
        checks=checks, outcome=outcome,
        assessment_id=derive_readiness_assessment_id(probe_with_checks))


# ---------------------------------------------------------------------------
# Immutable readiness store: readiness-assessments/<ready-…>/
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrittenReadinessAssessment:
    root: Path
    assessment_id: str
    assessment_digest: str


class ReadinessAssessmentManifest(StrictModel):
    schema_version: Literal[1] = 1
    assessment_id: str = Field(min_length=1)
    corpus_id: str = Field(min_length=1)
    outcome: ReadinessOutcome
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    assessment_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ReadinessAssessmentManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        if self.assessment_digest != _readiness_digest(
                assessment_id=self.assessment_id, corpus_id=self.corpus_id,
                outcome=self.outcome, generated_by=self.generated_by,
                files=self.files):
            raise ValueError("assessment_digest does not match the content")
        return self


def _readiness_digest(
    *,
    assessment_id: str,
    corpus_id: str,
    outcome: str,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    payload = {
        "assessment_id": assessment_id,
        "corpus_id": corpus_id,
        "outcome": outcome,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256,
             "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)],
    }
    return "readydig-" + sha256_canonical(payload)[:24]


def write_readiness_assessment(
    assessment: EvaluationReadinessAssessment,
    assessments_root: str | Path,
) -> WrittenReadinessAssessment:
    """Write ``readiness-assessments/<assessment_id>/``; never overwrite."""
    summary_payload = canonical_json_bytes(assessment)
    files = (DatasetFileHash(relative_path=SUMMARY_FILE,
                             sha256=sha256_bytes(summary_payload),
                             size=len(summary_payload)),)
    manifest = ReadinessAssessmentManifest(
        assessment_id=assessment.assessment_id,
        corpus_id=assessment.corpus_id, outcome=assessment.outcome,
        generated_by=IDENTITY_COVERAGE_GENERATOR, files=files,
        assessment_digest=_readiness_digest(
            assessment_id=assessment.assessment_id,
            corpus_id=assessment.corpus_id, outcome=assessment.outcome,
            generated_by=IDENTITY_COVERAGE_GENERATOR, files=files))
    root = Path(assessments_root) / assessment.assessment_id
    if root.exists() and any(root.iterdir()):
        raise CorpusExpansionError(f"assessment already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / CAMPAIGN_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    atomic_write_bytes(root / SUMMARY_FILE, summary_payload)
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(manifest))
    verification = verify_readiness_assessment(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise CorpusExpansionError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenReadinessAssessment(
        root=root, assessment_id=assessment.assessment_id,
        assessment_digest=manifest.assessment_digest)


def verify_readiness_assessment(
    assessment_dir: str | Path,
) -> CampaignVerificationResult:
    """Verify a readiness-assessment artifact; fail closed."""
    root = Path(assessment_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("assessment_dir_present", False, str(root)))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("assessment_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / CAMPAIGN_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = ReadinessAssessmentManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    hash_ok = True
    for fh in manifest.files:
        path = root / fh.relative_path
        raw = path.read_bytes() if path.is_file() else None
        if raw is None or len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok = False
            break
    checks.append(_c("file_hashes_match", hash_ok))
    summary_ok = True
    if hash_ok:
        try:
            assessment = EvaluationReadinessAssessment.model_validate_json(
                (root / SUMMARY_FILE).read_bytes())
        except ValidationError:
            summary_ok = False
        else:
            summary_ok = (
                assessment.assessment_id == manifest.assessment_id
                and assessment.corpus_id == manifest.corpus_id
                and assessment.outcome == manifest.outcome)
    checks.append(_c("summary_binds_manifest", summary_ok))
    return CampaignVerificationResult(
        verified=all(c.passed for c in checks),
        campaign_digest=manifest.assessment_digest, checks=tuple(checks))
