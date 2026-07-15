"""Corpus expansion to a descendant version: policy, plan, campaign,
comparison (Gate 14).

Gate 13's project corpus v1 has 2 eligible test examples — far below the
ADR-0029 directional threshold of 30. Gate 14 grows COVERAGE, not models:
a frozen expansion policy states the minimum coverage a descendant version
must reach; a deterministic scenario-coverage matrix names what the current
corpus lacks; a pure planner turns policy + candidate identities into an
immutable plan (predicting splits ONLY with the production splitter over
fully-defined stable identities — never overriding it); a generation
campaign records exactly which verified runs were produced; and a corpus
comparison report measures v1→v2 improvement in counts and diversity — never
in model metrics. No model loads, no evaluation runs, no benchmark runs, no
training artifact changes. Coverage targets may drive NEW verified scenario
generation; they may NEVER move an example between splits (ADR-0031).
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
from verifiednet.datasets.features import AcceptedLabels
from verifiednet.datasets.models import (
    DatasetFileHash,
    DatasetPartition,
    SplitPolicy,
    StableScenarioIdentity,
)
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.datasets.projection import group_id_for_identity
from verifiednet.datasets.splitting import assign_group_split
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.evalcorpus import (
    CorpusCoverageStats,
    CorpusExpansionBinding,
    EvaluationCorpusManifest,
)
from verifiednet.evaluation.scoring import ratio_str
from verifiednet.schemas.base import StrictModel

CAMPAIGN_FORMAT_VERSION = 1
CAMPAIGN_GENERATOR = "verifiednet.evaluation.corpusexpansion"
MANIFEST_FILE = "manifest.json"
PLANNED_SCENARIOS_FILE = "planned-scenarios.json"
VERIFIED_RUNS_FILE = "verified-runs.json"
SUMMARY_FILE = "summary.json"
CAMPAIGN_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPECTED_CAMPAIGN_FILES: frozenset[str] = frozenset(
    {PLANNED_SCENARIOS_FILE, VERIFIED_RUNS_FILE})
EXPECTED_CORPUS_COMPARISON_FILES: frozenset[str] = frozenset({SUMMARY_FILE})


class CorpusExpansionError(VerifiedNetError):
    """A corpus-expansion artifact could not be built, written, or read."""


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


# ---------------------------------------------------------------------------
# Expansion policy (frozen; mandatory versus advisory is EXPLICIT)
# ---------------------------------------------------------------------------


class EvaluationCorpusExpansionPolicy(StrictModel):
    """Minimum coverage a descendant corpus version must reach (Gate 14).

    Mandatory minimums gate registration; ADVISORY fields are reported, never
    silently ignored and never registration-blocking (they cover dimensions
    the scenario system may not support yet — e.g. rejection-phase variety is
    structurally bounded by the Gate 6 rejected-projection contract). The
    policy guides GENERATION only: it has no access to, and no effect on,
    deterministic split assignment.
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    source_corpus_id: str = Field(min_length=1)
    source_corpus_digest: str = Field(min_length=1)
    min_total_examples: int = Field(ge=1)
    min_accepted_examples: int = Field(ge=1)
    min_abstention_examples: int = Field(ge=1)
    min_validation_accepted: int = Field(ge=0)
    min_test_accepted: int = Field(ge=0)
    min_examples_per_family: int = Field(ge=1)
    min_identities_per_family: int = Field(ge=1)
    max_class_imbalance_ratio: str = Field(min_length=1)
    required_rejection_codes: tuple[str, ...] = Field(min_length=1)
    advisory_min_topology_variants: int = Field(ge=1)
    advisory_max_duplicate_content_ratio: str = Field(min_length=1)
    expansion_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationCorpusExpansionPolicy:
        if sorted(self.required_rejection_codes) != list(
                self.required_rejection_codes):
            raise ValueError("required_rejection_codes must be sorted")
        if self.expansion_policy_id != derive_expansion_policy_id(self):
            raise ValueError(
                "expansion_policy_id does not match the policy content")
        return self


def derive_expansion_policy_id(
    policy: EvaluationCorpusExpansionPolicy,
) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("expansion_policy_id", None)
    return "ecexp-" + sha256_canonical(payload)[:16]


def build_expansion_policy(
    *,
    source_corpus_id: str,
    source_corpus_digest: str,
    min_total_examples: int = 80,
    min_accepted_examples: int = 64,
    min_abstention_examples: int = 12,
    min_validation_accepted: int = 12,
    min_test_accepted: int = 20,
    min_examples_per_family: int = 12,
    min_identities_per_family: int = 3,
    max_class_imbalance_ratio: str = "1.500000",
    required_rejection_codes: tuple[str, ...] = ("precondition_failed",),
    advisory_min_topology_variants: int = 3,
    advisory_max_duplicate_content_ratio: str = "0.200000",
) -> EvaluationCorpusExpansionPolicy:
    codes = tuple(sorted(required_rejection_codes))
    probe = EvaluationCorpusExpansionPolicy.model_construct(
        source_corpus_id=source_corpus_id,
        source_corpus_digest=source_corpus_digest,
        min_total_examples=min_total_examples,
        min_accepted_examples=min_accepted_examples,
        min_abstention_examples=min_abstention_examples,
        min_validation_accepted=min_validation_accepted,
        min_test_accepted=min_test_accepted,
        min_examples_per_family=min_examples_per_family,
        min_identities_per_family=min_identities_per_family,
        max_class_imbalance_ratio=max_class_imbalance_ratio,
        required_rejection_codes=codes,
        advisory_min_topology_variants=advisory_min_topology_variants,
        advisory_max_duplicate_content_ratio=advisory_max_duplicate_content_ratio)
    return EvaluationCorpusExpansionPolicy(
        source_corpus_id=source_corpus_id,
        source_corpus_digest=source_corpus_digest,
        min_total_examples=min_total_examples,
        min_accepted_examples=min_accepted_examples,
        min_abstention_examples=min_abstention_examples,
        min_validation_accepted=min_validation_accepted,
        min_test_accepted=min_test_accepted,
        min_examples_per_family=min_examples_per_family,
        min_identities_per_family=min_identities_per_family,
        max_class_imbalance_ratio=max_class_imbalance_ratio,
        required_rejection_codes=codes,
        advisory_min_topology_variants=advisory_min_topology_variants,
        advisory_max_duplicate_content_ratio=advisory_max_duplicate_content_ratio,
        expansion_policy_id=derive_expansion_policy_id(probe))


# ---------------------------------------------------------------------------
# Scenario coverage matrix (from the prepared corpus; NO model facts)
# ---------------------------------------------------------------------------


class FamilyCoverage(StrictModel):
    """Coverage of one fault family — identities, contexts, partitions."""

    schema_version: Literal[1] = 1
    fault_family: str = Field(min_length=1)
    scenario_ids: tuple[str, ...] = Field(default_factory=tuple)
    identity_group_ids: tuple[str, ...] = Field(default_factory=tuple)
    topology_hashes: tuple[str, ...] = Field(default_factory=tuple)
    backends: tuple[str, ...] = Field(default_factory=tuple)
    accepted_examples: int = Field(ge=0)
    train_examples: int = Field(ge=0)
    validation_examples: int = Field(ge=0)
    test_examples: int = Field(ge=0)

    @model_validator(mode="after")
    def _sorted(self) -> FamilyCoverage:
        for name in ("scenario_ids", "identity_group_ids",
                     "topology_hashes", "backends"):
            values = getattr(self, name)
            if list(values) != sorted(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        if (self.train_examples + self.validation_examples
                + self.test_examples) != self.accepted_examples:
            raise ValueError("partition counts must sum to accepted count")
        return self


class ScenarioCoverageMatrix(StrictModel):
    """The deterministic what-do-we-have view used to find deficits.

    Contains NO model predictions, no correctness, no benchmark facts —
    structurally: there are no fields to put them in.
    """

    schema_version: Literal[1] = 1
    prepared_digest: str = Field(min_length=1)
    families: tuple[FamilyCoverage, ...] = Field(min_length=1)
    abstention_examples: int = Field(ge=0)
    abstention_group_ids: tuple[str, ...] = Field(default_factory=tuple)
    rejection_codes: tuple[str, ...] = Field(default_factory=tuple)
    topology_hashes: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _sorted(self) -> ScenarioCoverageMatrix:
        names = [f.fault_family for f in self.families]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("families must be sorted and unique")
        return self


def build_scenario_coverage_matrix(
    loaded: LoadedPrepared,
) -> ScenarioCoverageMatrix:
    """Pure coverage-matrix construction from a verified prepared corpus."""
    per_family: dict[str, dict[str, set[str]]] = {}
    counts: Counter[tuple[str, str]] = Counter()
    abstention_groups: set[str] = set()
    rejection_codes: set[str] = set()
    topologies: set[str] = set()
    abstention = 0
    for example in loaded.examples:
        topologies.add(example.features.topology_hash)
        if not isinstance(example.labels, AcceptedLabels):
            abstention += 1
            abstention_groups.add(example.trace.group_id)
            rejection_codes.add(example.labels.rejection_code)
            continue
        family = example.labels.fault_family
        sets = per_family.setdefault(family, {
            "scenario_ids": set(), "groups": set(), "topologies": set(),
            "backends": set()})
        sets["scenario_ids"].add(example.labels.scenario_id)
        sets["groups"].add(example.trace.group_id)
        sets["topologies"].add(example.features.topology_hash)
        sets["backends"].add(example.features.backend)
        counts[(family, example.trace.partition.value)] += 1
    families = tuple(
        FamilyCoverage(
            fault_family=family,
            scenario_ids=tuple(sorted(sets["scenario_ids"])),
            identity_group_ids=tuple(sorted(sets["groups"])),
            topology_hashes=tuple(sorted(sets["topologies"])),
            backends=tuple(sorted(sets["backends"])),
            accepted_examples=sum(
                counts[(family, p)] for p in ("train", "validation", "test")),
            train_examples=counts[(family, "train")],
            validation_examples=counts[(family, "validation")],
            test_examples=counts[(family, "test")])
        for family, sets in sorted(per_family.items()))
    return ScenarioCoverageMatrix(
        prepared_digest=loaded.manifest.prepared_digest, families=families,
        abstention_examples=abstention,
        abstention_group_ids=tuple(sorted(abstention_groups)),
        rejection_codes=tuple(sorted(rejection_codes)),
        topology_hashes=tuple(sorted(topologies)))


# ---------------------------------------------------------------------------
# Candidates + pure expansion planning (production splitter ONLY)
# ---------------------------------------------------------------------------


class CandidateScenario(StrictModel):
    """One fully-defined stable identity a campaign intends to run.

    The identity is complete BEFORE execution, so the deterministic split of
    its group is a pure function of production code — predicted here with the
    exact production splitter and verified again after projection.
    """

    schema_version: Literal[1] = 1
    case_id: str = Field(min_length=1)
    fault_family: str = Field(min_length=1)
    identity: StableScenarioIdentity
    planned_runs: int = Field(ge=1)

    @property
    def group_id(self) -> str:
        return group_id_for_identity(self.identity)


def predict_candidate_partition(
    candidate: CandidateScenario, *, split_policy: SplitPolicy,
) -> DatasetPartition:
    """EXACT production split for a fully-defined candidate identity."""
    return assign_group_split(
        group_id=candidate.group_id, policy=split_policy)


class PredictedSplitCounts(StrictModel):
    schema_version: Literal[1] = 1
    train_examples: int = Field(ge=0)
    validation_examples: int = Field(ge=0)
    test_examples: int = Field(ge=0)


class EvaluationCorpusExpansionPlan(StrictModel):
    """The frozen plan: deficits + the complete candidate matrix + prediction.

    The plan NEVER assigns a split — ``predicted_split`` is the production
    splitter's own deterministic answer for the fixed candidate set, recorded
    so the post-projection verification can prove the prediction exact.
    """

    schema_version: Literal[1] = 1
    plan_version: Literal[1] = 1
    expansion_policy_id: str = Field(min_length=1)
    source_corpus_id: str = Field(min_length=1)
    coverage_deficits: tuple[str, ...] = Field(default_factory=tuple)
    candidates: tuple[CandidateScenario, ...] = Field(min_length=1)
    planned_rejected_runs: int = Field(ge=0)
    predicted_split: PredictedSplitCounts
    expansion_plan_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationCorpusExpansionPlan:
        keys = [(c.fault_family, c.case_id, c.identity.topology_hash)
                for c in self.candidates]
        if keys != sorted(keys):
            raise ValueError("candidates must be deterministically ordered")
        if len({(c.group_id) for c in self.candidates}) != len(self.candidates):
            raise ValueError("candidate identities must be unique")
        if self.expansion_plan_id != derive_expansion_plan_id(self):
            raise ValueError("expansion_plan_id does not match the plan")
        return self


def derive_expansion_plan_id(plan: EvaluationCorpusExpansionPlan) -> str:
    payload = plan.model_dump(mode="json")
    payload.pop("expansion_plan_id", None)
    return "ecplan-" + sha256_canonical(payload)[:16]


def _coverage_deficits(
    coverage: CorpusCoverageStats,
    policy: EvaluationCorpusExpansionPolicy,
) -> tuple[str, ...]:
    families = {e.key: e.count for e in coverage.fault_family_distribution}
    deficits: list[str] = []
    if coverage.total < policy.min_total_examples:
        deficits.append(f"total_examples {coverage.total}"
                        f"<{policy.min_total_examples}")
    if coverage.accepted < policy.min_accepted_examples:
        deficits.append(f"accepted_examples {coverage.accepted}"
                        f"<{policy.min_accepted_examples}")
    if coverage.abstention < policy.min_abstention_examples:
        deficits.append(f"abstention_examples {coverage.abstention}"
                        f"<{policy.min_abstention_examples}")
    if coverage.partition_counts.validation < policy.min_validation_accepted:
        deficits.append(
            f"validation_accepted {coverage.partition_counts.validation}"
            f"<{policy.min_validation_accepted}")
    if coverage.eligible_test_examples < policy.min_test_accepted:
        deficits.append(f"test_accepted {coverage.eligible_test_examples}"
                        f"<{policy.min_test_accepted}")
    for family, count in sorted(families.items()):
        if count < policy.min_examples_per_family:
            deficits.append(f"family {family} examples {count}"
                            f"<{policy.min_examples_per_family}")
    if len(coverage.topology_distribution) \
            < policy.advisory_min_topology_variants:
        deficits.append(
            f"advisory: topology_variants "
            f"{len(coverage.topology_distribution)}"
            f"<{policy.advisory_min_topology_variants}")
    return tuple(deficits)


def plan_evaluation_corpus_expansion(
    current_coverage: CorpusCoverageStats,
    candidates: tuple[CandidateScenario, ...],
    *,
    policy: EvaluationCorpusExpansionPolicy,
    split_policy: SplitPolicy,
    planned_rejected_runs: int,
) -> EvaluationCorpusExpansionPlan:
    """Pure planning: deficits + fixed candidate matrix + exact prediction.

    Candidate order is canonicalised, so input order cannot matter. The split
    prediction uses ONLY the production splitter over each fully-defined
    identity — the planner cannot move, force, or exclude anything by
    partition (there is no parameter through which it could).
    """
    ordered = tuple(sorted(
        candidates,
        key=lambda c: (c.fault_family, c.case_id, c.identity.topology_hash)))
    counts = {"train": 0, "validation": 0, "test": 0}
    for candidate in ordered:
        partition = predict_candidate_partition(
            candidate, split_policy=split_policy)
        counts[partition.value] += candidate.planned_runs
    deficits = _coverage_deficits(current_coverage, policy)
    predicted = PredictedSplitCounts(
        train_examples=counts["train"],
        validation_examples=counts["validation"],
        test_examples=counts["test"])
    probe = EvaluationCorpusExpansionPlan.model_construct(
        expansion_policy_id=policy.expansion_policy_id,
        source_corpus_id=policy.source_corpus_id,
        coverage_deficits=deficits, candidates=ordered,
        planned_rejected_runs=planned_rejected_runs,
        predicted_split=predicted)
    return EvaluationCorpusExpansionPlan(
        expansion_policy_id=policy.expansion_policy_id,
        source_corpus_id=policy.source_corpus_id,
        coverage_deficits=deficits, candidates=ordered,
        planned_rejected_runs=planned_rejected_runs,
        predicted_split=predicted,
        expansion_plan_id=derive_expansion_plan_id(probe))


# ---------------------------------------------------------------------------
# Mandatory-target assessment (gates the v2 registration)
# ---------------------------------------------------------------------------


class ExpansionTargetResult(StrictModel):
    """Mandatory checks (fail-closed) + advisory findings (always visible)."""

    schema_version: Literal[1] = 1
    satisfied: bool
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)
    advisory_findings: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def assess_expansion_targets(
    coverage: CorpusCoverageStats,
    matrix: ScenarioCoverageMatrix,
    policy: EvaluationCorpusExpansionPolicy,
) -> ExpansionTargetResult:
    """Deterministic target verdict for a candidate v2 corpus."""
    checks: list[DatasetCheck] = []
    checks.append(_c("min_total_examples",
                     coverage.total >= policy.min_total_examples,
                     str(coverage.total)))
    checks.append(_c("min_accepted_examples",
                     coverage.accepted >= policy.min_accepted_examples,
                     str(coverage.accepted)))
    checks.append(_c("min_abstention_examples",
                     coverage.abstention >= policy.min_abstention_examples,
                     str(coverage.abstention)))
    checks.append(_c(
        "min_validation_accepted",
        coverage.partition_counts.validation >= policy.min_validation_accepted,
        str(coverage.partition_counts.validation)))
    checks.append(_c("min_test_accepted",
                     coverage.eligible_test_examples >= policy.min_test_accepted,
                     str(coverage.eligible_test_examples)))
    families = {e.key: e.count for e in coverage.fault_family_distribution}
    checks.append(_c(
        "min_examples_per_family",
        bool(families) and min(families.values())
        >= policy.min_examples_per_family,
        str(sorted(families.items()))))
    identities = {f.fault_family: len(f.identity_group_ids)
                  for f in matrix.families}
    checks.append(_c(
        "min_identities_per_family",
        bool(identities) and min(identities.values())
        >= policy.min_identities_per_family,
        str(sorted(identities.items()))))
    imbalance_ok = (coverage.class_imbalance_ratio is None
                    or float(coverage.class_imbalance_ratio)
                    <= float(policy.max_class_imbalance_ratio))
    checks.append(_c("max_class_imbalance_ratio", imbalance_ok,
                     str(coverage.class_imbalance_ratio)))
    covered_codes = {e.key for e in coverage.rejection_distribution}
    checks.append(_c(
        "required_rejection_codes",
        set(policy.required_rejection_codes) <= covered_codes,
        str(sorted(covered_codes))))

    duplicate_ratio = ratio_str(
        coverage.duplicate_feature_content_groups, coverage.total)
    advisory = (
        f"topology_variants={len(coverage.topology_distribution)} "
        f"(advisory_min={policy.advisory_min_topology_variants})",
        f"duplicate_content_groups_ratio={duplicate_ratio} "
        f"(advisory_max={policy.advisory_max_duplicate_content_ratio}; the "
        "feature allowlist intentionally withholds distinguishing content, "
        "so identical model-visible features across identities are expected)",
        "rejection_phase_coverage=precondition_only (the Gate 6 rejected "
        "projection supports precondition-phase rejections only)",
    )
    return ExpansionTargetResult(
        satisfied=all(c.passed for c in checks), checks=tuple(checks),
        advisory_findings=advisory)


def build_expansion_binding(
    *,
    parent: EvaluationCorpusManifest,
    policy: EvaluationCorpusExpansionPolicy,
    plan: EvaluationCorpusExpansionPlan,
    campaign_id: str,
    target_result: ExpansionTargetResult,
) -> CorpusExpansionBinding:
    """Fail-closed bridge from assessment to registration."""
    if policy.source_corpus_id != parent.evaluation_corpus_id \
            or policy.source_corpus_digest != parent.corpus_digest:
        raise CorpusExpansionError(
            "expansion policy does not bind the given parent corpus")
    if plan.expansion_policy_id != policy.expansion_policy_id:
        raise CorpusExpansionError("plan was built for a different policy")
    if not target_result.satisfied:
        detail = "; ".join(
            f"{c.rule}: {c.detail}" for c in target_result.failures)
        raise CorpusExpansionError(
            f"mandatory expansion targets unmet: {detail}")
    return CorpusExpansionBinding(
        parent_corpus_id=parent.evaluation_corpus_id,
        parent_corpus_digest=parent.corpus_digest,
        expansion_policy_id=policy.expansion_policy_id,
        expansion_plan_id=plan.expansion_plan_id,
        campaign_id=campaign_id,
        target_checks=target_result.checks,
        advisory_findings=target_result.advisory_findings)


# ---------------------------------------------------------------------------
# Generation campaign (immutable record of what was actually produced)
# ---------------------------------------------------------------------------


def derive_campaign_id(
    *,
    expansion_plan_id: str,
    intended_group_ids: tuple[str, ...],
    expected_run_count: int,
    backend_policy: str,
    execution_policy: str,
) -> str:
    payload = {
        "expansion_plan_id": expansion_plan_id,
        "intended_group_ids": sorted(intended_group_ids),
        "expected_run_count": expected_run_count,
        "backend_policy": backend_policy,
        "execution_policy": execution_policy,
    }
    return "campaign-" + sha256_canonical(payload)[:16]


class VerifiedRunGenerationCampaign(StrictModel):
    """What a campaign INTENDED and what it actually PRODUCED — immutable.

    No timestamps, no host facts. Every produced run id is listed; a missing
    or unexpected run is a validation error, never a silent drop.
    """

    schema_version: Literal[1] = 1
    campaign_version: Literal[1] = 1
    expansion_plan_id: str = Field(min_length=1)
    backend_policy: str = Field(min_length=1)
    execution_policy: str = Field(min_length=1)
    intended_group_ids: tuple[str, ...] = Field(min_length=1)
    expected_run_count: int = Field(ge=1)
    verified_run_ids: tuple[str, ...] = Field(min_length=1)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    campaign_id: str = Field(min_length=1)
    campaign_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> VerifiedRunGenerationCampaign:
        if list(self.intended_group_ids) != sorted(self.intended_group_ids):
            raise ValueError("intended_group_ids must be sorted")
        ids = list(self.verified_run_ids)
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("verified_run_ids must be sorted and unique")
        if len(ids) != self.accepted_count + self.rejected_count:
            raise ValueError("verified run ids must equal accepted + rejected")
        if len(ids) + self.failed_count != self.expected_run_count:
            raise ValueError(
                "verified + failed runs must equal the expected count")
        expected = derive_campaign_id(
            expansion_plan_id=self.expansion_plan_id,
            intended_group_ids=self.intended_group_ids,
            expected_run_count=self.expected_run_count,
            backend_policy=self.backend_policy,
            execution_policy=self.execution_policy)
        if self.campaign_id != expected:
            raise ValueError("campaign_id does not match the campaign content")
        expected_digest = compute_campaign_digest(self)
        if self.campaign_digest != expected_digest:
            raise ValueError("campaign_digest does not match the content")
        return self


def compute_campaign_digest(campaign: VerifiedRunGenerationCampaign) -> str:
    payload = campaign.model_dump(mode="json")
    payload.pop("campaign_digest", None)
    return "campdig-" + sha256_canonical(payload)[:24]


def build_generation_campaign(
    *,
    plan: EvaluationCorpusExpansionPlan,
    backend_policy: str,
    execution_policy: str,
    verified_run_ids: tuple[str, ...],
    accepted_count: int,
    rejected_count: int,
    failed_count: int = 0,
) -> VerifiedRunGenerationCampaign:
    intended = tuple(sorted(c.group_id for c in plan.candidates))
    expected = sum(c.planned_runs for c in plan.candidates) \
        + plan.planned_rejected_runs
    run_ids = tuple(sorted(verified_run_ids))
    campaign_id = derive_campaign_id(
        expansion_plan_id=plan.expansion_plan_id,
        intended_group_ids=intended, expected_run_count=expected,
        backend_policy=backend_policy, execution_policy=execution_policy)
    probe = VerifiedRunGenerationCampaign.model_construct(
        expansion_plan_id=plan.expansion_plan_id,
        backend_policy=backend_policy, execution_policy=execution_policy,
        intended_group_ids=intended, expected_run_count=expected,
        verified_run_ids=run_ids, accepted_count=accepted_count,
        rejected_count=rejected_count, failed_count=failed_count,
        campaign_id=campaign_id)
    return VerifiedRunGenerationCampaign(
        expansion_plan_id=plan.expansion_plan_id,
        backend_policy=backend_policy, execution_policy=execution_policy,
        intended_group_ids=intended, expected_run_count=expected,
        verified_run_ids=run_ids, accepted_count=accepted_count,
        rejected_count=rejected_count, failed_count=failed_count,
        campaign_id=campaign_id,
        campaign_digest=compute_campaign_digest(probe))


@dataclass(frozen=True)
class WrittenCampaign:
    root: Path
    campaign_id: str
    campaign_digest: str


def write_generation_campaign(
    campaign: VerifiedRunGenerationCampaign,
    plan: EvaluationCorpusExpansionPlan,
    campaigns_root: str | Path,
) -> WrittenCampaign:
    """Write ``generation-campaigns/<campaign_id>/``; never overwrite."""
    if plan.expansion_plan_id != campaign.expansion_plan_id:
        raise CorpusExpansionError("campaign does not bind the given plan")
    root = Path(campaigns_root) / campaign.campaign_id
    if root.exists() and any(root.iterdir()):
        raise CorpusExpansionError(f"campaign already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / CAMPAIGN_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    atomic_write_bytes(root / PLANNED_SCENARIOS_FILE,
                       canonical_json_bytes(plan))
    atomic_write_bytes(
        root / VERIFIED_RUNS_FILE,
        canonical_json_bytes({"schema_version": 1,
                              "verified_run_ids":
                                  list(campaign.verified_run_ids)}))
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(campaign))
    verification = verify_generation_campaign(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise CorpusExpansionError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenCampaign(root=root, campaign_id=campaign.campaign_id,
                           campaign_digest=campaign.campaign_digest)


class CampaignVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    campaign_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def verify_generation_campaign(
    campaign_dir: str | Path,
) -> CampaignVerificationResult:
    """Verify the campaign artifact; recompute bindings; fail closed."""
    import json

    root = Path(campaign_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("campaign_dir_present", False, str(root)))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("campaign_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / CAMPAIGN_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        campaign = VerifiedRunGenerationCampaign.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))

    on_disk = {str(p.relative_to(root)) for p in root.rglob("*")
               if p.is_file() and p.name != CAMPAIGN_INCOMPLETE_MARKER}
    allowed = EXPECTED_CAMPAIGN_FILES | {MANIFEST_FILE}
    checks.append(_c("no_missing_files", not sorted(allowed - on_disk)))
    checks.append(_c("no_unexpected_files", not sorted(on_disk - allowed)))

    plan_ok, runs_ok = True, True
    try:
        plan = EvaluationCorpusExpansionPlan.model_validate_json(
            (root / PLANNED_SCENARIOS_FILE).read_bytes())
        runs_payload = json.loads((root / VERIFIED_RUNS_FILE).read_bytes())
    except (OSError, ValidationError, ValueError):
        plan_ok = runs_ok = False
    else:
        plan_ok = plan.expansion_plan_id == campaign.expansion_plan_id
        runs_ok = (runs_payload.get("verified_run_ids")
                   == list(campaign.verified_run_ids))
    checks.append(_c("plan_binds_campaign", plan_ok))
    checks.append(_c("verified_runs_match_manifest", runs_ok))
    return CampaignVerificationResult(
        verified=all(c.passed for c in checks),
        campaign_digest=campaign.campaign_digest, checks=tuple(checks))


# ---------------------------------------------------------------------------
# v1-versus-v2 corpus comparison (counts and diversity ONLY; no model facts)
# ---------------------------------------------------------------------------


class CorpusDelta(StrictModel):
    metric: str = Field(min_length=1)
    before: int = Field(ge=0)
    after: int = Field(ge=0)

    @property
    def delta(self) -> int:
        return self.after - self.before


def derive_corpus_comparison_id(
    *, parent_corpus_id: str, descendant_corpus_id: str,
) -> str:
    payload = {"parent_corpus_id": parent_corpus_id,
               "descendant_corpus_id": descendant_corpus_id}
    return "ccmp-" + sha256_canonical(payload)[:16]


class CorpusComparisonReport(StrictModel):
    """Deterministic v1→v2 corpus improvement report. No model metrics —
    structurally: there is nowhere to put one."""

    schema_version: Literal[1] = 1
    parent_corpus_id: str = Field(min_length=1)
    parent_corpus_digest: str = Field(min_length=1)
    descendant_corpus_id: str = Field(min_length=1)
    descendant_corpus_digest: str = Field(min_length=1)
    deltas: tuple[CorpusDelta, ...] = Field(min_length=1)
    class_imbalance_before: str | None = None
    class_imbalance_after: str | None = None
    targets_met: tuple[str, ...] = Field(default_factory=tuple)
    targets_unmet: tuple[str, ...] = Field(default_factory=tuple)
    advisory_findings: tuple[str, ...] = Field(default_factory=tuple)
    comparison_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CorpusComparisonReport:
        metrics = [d.metric for d in self.deltas]
        if metrics != sorted(metrics) or len(metrics) != len(set(metrics)):
            raise ValueError("deltas must be metric-sorted and unique")
        expected = derive_corpus_comparison_id(
            parent_corpus_id=self.parent_corpus_id,
            descendant_corpus_id=self.descendant_corpus_id)
        if self.comparison_id != expected:
            raise ValueError("comparison_id does not match the content")
        return self


def _family_counts(coverage: CorpusCoverageStats) -> dict[str, int]:
    return {e.key: e.count for e in coverage.fault_family_distribution}


def build_corpus_comparison(
    parent: EvaluationCorpusManifest,
    descendant: EvaluationCorpusManifest,
) -> CorpusComparisonReport:
    """Pure comparison of two registrations; fail closed on wrong lineage."""
    if descendant.expansion is None:
        raise CorpusExpansionError(
            "descendant corpus carries no expansion binding")
    if descendant.expansion.parent_corpus_id != parent.evaluation_corpus_id \
            or descendant.expansion.parent_corpus_digest \
            != parent.corpus_digest:
        raise CorpusExpansionError(
            "descendant corpus does not descend from the given parent")
    before, after = parent.coverage, descendant.coverage
    deltas = [
        CorpusDelta(metric="abstention_examples",
                    before=before.abstention, after=after.abstention),
        CorpusDelta(metric="accepted_examples",
                    before=before.accepted, after=after.accepted),
        CorpusDelta(metric="distinct_rejection_codes",
                    before=len(before.rejection_distribution),
                    after=len(after.rejection_distribution)),
        CorpusDelta(metric="distinct_scenarios",
                    before=len(before.scenario_distribution),
                    after=len(after.scenario_distribution)),
        CorpusDelta(metric="distinct_topologies",
                    before=len(before.topology_distribution),
                    after=len(after.topology_distribution)),
        CorpusDelta(metric="duplicate_content_groups",
                    before=before.duplicate_feature_content_groups,
                    after=after.duplicate_feature_content_groups),
        CorpusDelta(metric="eligible_test_examples",
                    before=before.eligible_test_examples,
                    after=after.eligible_test_examples),
        CorpusDelta(metric="total_examples",
                    before=before.total, after=after.total),
        CorpusDelta(metric="train_examples",
                    before=before.partition_counts.train,
                    after=after.partition_counts.train),
        CorpusDelta(metric="validation_examples",
                    before=before.partition_counts.validation,
                    after=after.partition_counts.validation),
    ]
    for family in sorted(set(_family_counts(before))
                         | set(_family_counts(after))):
        deltas.append(CorpusDelta(
            metric=f"family_{family}",
            before=_family_counts(before).get(family, 0),
            after=_family_counts(after).get(family, 0)))
    binding = descendant.expansion
    return CorpusComparisonReport(
        parent_corpus_id=parent.evaluation_corpus_id,
        parent_corpus_digest=parent.corpus_digest,
        descendant_corpus_id=descendant.evaluation_corpus_id,
        descendant_corpus_digest=descendant.corpus_digest,
        deltas=tuple(sorted(deltas, key=lambda d: d.metric)),
        class_imbalance_before=before.class_imbalance_ratio,
        class_imbalance_after=after.class_imbalance_ratio,
        targets_met=tuple(sorted(c.rule for c in binding.target_checks
                                 if c.passed)),
        targets_unmet=tuple(sorted(c.rule for c in binding.target_checks
                                   if not c.passed)),
        advisory_findings=binding.advisory_findings,
        comparison_id=derive_corpus_comparison_id(
            parent_corpus_id=parent.evaluation_corpus_id,
            descendant_corpus_id=descendant.evaluation_corpus_id))


@dataclass(frozen=True)
class WrittenCorpusComparison:
    root: Path
    comparison_id: str
    comparison_digest: str


class CorpusComparisonManifest(StrictModel):
    schema_version: Literal[1] = 1
    comparison_id: str = Field(min_length=1)
    parent_corpus_id: str = Field(min_length=1)
    descendant_corpus_id: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    comparison_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> CorpusComparisonManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        payload = {
            "comparison_id": self.comparison_id,
            "parent_corpus_id": self.parent_corpus_id,
            "descendant_corpus_id": self.descendant_corpus_id,
            "generated_by": self.generated_by,
            "files": [
                {"relative_path": f.relative_path, "sha256": f.sha256,
                 "size": f.size}
                for f in sorted(self.files, key=lambda f: f.relative_path)
            ],
        }
        expected = "ccmpdig-" + sha256_canonical(payload)[:24]
        if self.comparison_digest != expected:
            raise ValueError("comparison_digest does not match the content")
        return self


def write_corpus_comparison(
    report: CorpusComparisonReport, comparisons_root: str | Path,
) -> WrittenCorpusComparison:
    """Write ``corpus-comparisons/<comparison_id>/``; never overwrite."""
    summary_payload = canonical_json_bytes(report)
    files = (DatasetFileHash(relative_path=SUMMARY_FILE,
                             sha256=sha256_bytes(summary_payload),
                             size=len(summary_payload)),)
    payload = {
        "comparison_id": report.comparison_id,
        "parent_corpus_id": report.parent_corpus_id,
        "descendant_corpus_id": report.descendant_corpus_id,
        "generated_by": CAMPAIGN_GENERATOR,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256,
             "size": f.size} for f in files],
    }
    manifest = CorpusComparisonManifest(
        comparison_id=report.comparison_id,
        parent_corpus_id=report.parent_corpus_id,
        descendant_corpus_id=report.descendant_corpus_id,
        generated_by=CAMPAIGN_GENERATOR, files=files,
        comparison_digest="ccmpdig-" + sha256_canonical(payload)[:24])
    root = Path(comparisons_root) / report.comparison_id
    if root.exists() and any(root.iterdir()):
        raise CorpusExpansionError(f"comparison already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / CAMPAIGN_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    atomic_write_bytes(root / SUMMARY_FILE, summary_payload)
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(manifest))
    verification = verify_corpus_comparison(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise CorpusExpansionError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenCorpusComparison(
        root=root, comparison_id=report.comparison_id,
        comparison_digest=manifest.comparison_digest)


def verify_corpus_comparison(
    comparison_dir: str | Path,
) -> CampaignVerificationResult:
    """Verify a corpus-comparison artifact; fail closed."""
    root = Path(comparison_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("comparison_dir_present", False, str(root)))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("comparison_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / CAMPAIGN_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = CorpusComparisonManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return CampaignVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    hash_ok, summary_ok = True, True
    for fh in manifest.files:
        path = root / fh.relative_path
        raw = path.read_bytes() if path.is_file() else None
        if raw is None or len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok = False
            break
    checks.append(_c("file_hashes_match", hash_ok))
    if hash_ok:
        try:
            report = CorpusComparisonReport.model_validate_json(
                (root / SUMMARY_FILE).read_bytes())
        except ValidationError:
            summary_ok = False
        else:
            summary_ok = (report.comparison_id == manifest.comparison_id
                          and report.parent_corpus_id
                          == manifest.parent_corpus_id
                          and report.descendant_corpus_id
                          == manifest.descendant_corpus_id)
    checks.append(_c("summary_binds_manifest", summary_ok))
    return CampaignVerificationResult(
        verified=all(c.passed for c in checks),
        campaign_digest=manifest.comparison_digest, checks=tuple(checks))
