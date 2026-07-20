"""Gate 19A — deterministic family-balanced training-source selection.

Gate 18B selected the natural first-64 accepted train sources (family counts
25 / 21 / 17 / 1) and the trained model collapsed the three ``active``-peer-state
families onto the majority (iface_admin_shutdown). Gate 19 diagnosed this as an
imbalance-driven majority-class optimization collapse — the v2 representation is
provably sufficient (a deterministic 4-flag oracle scores 100 %). Gate 19A
introduces ONE new variable: the training source-selection policy.

``FamilyBalancedSelectionPolicy`` selects a budget-preserving, family-balanced
64-example subset from the FROZEN train partition — a content-addressed, frozen,
deterministic policy. It never inspects validation/test labels, model
predictions, or evaluation artifacts, and it never duplicates, synthesizes, or
redistributes examples. Selection is the sole change: for any source example the
downstream v2 feature derivation, prompt render, target, and objective remain
byte-identical to Gate 18B. This module imports no evaluation package, loads no
model, and touches no network/subprocess.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.features import AcceptedLabels
from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.schemas.base import StrictModel
from verifiednet.training.corpus import TrainingCorpus
from verifiednet.training.policy import TRAINING_CANDIDATE_FAMILIES

FAMILY_BALANCED_SELECTION_VERSION = 1

# Budget-preserving default allocation (Gate 19 design): the abundant active-state
# families are equalised and prefix_withdrawal matched, while the split-scarce
# remote_as family contributes all of its available train examples (4). Ordered
# by the frozen TRAINING_CANDIDATE_FAMILIES tuple. Sum == 64.
DEFAULT_FAMILY_ALLOCATION: tuple[tuple[str, int], ...] = (
    ("bgp_neighbor_removal", 20),
    ("bgp_prefix_withdrawal", 20),
    ("bgp_remote_as_mismatch", 4),
    ("iface_admin_shutdown", 20),
)
DEFAULT_SELECTION_TOTAL = 64


class SelectionError(VerifiedNetError):
    """Raised when family-balanced selection cannot be satisfied fail-closed."""


class FamilyQuota(StrictModel):
    """One family's target (or realised) example count."""

    schema_version: Literal[1] = 1
    fault_family: str = Field(min_length=1)
    count: int = Field(ge=0)


class SelectedSource(StrictModel):
    """One selected training source, with the family it was selected under."""

    schema_version: Literal[1] = 1
    example_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    fault_family: str = Field(min_length=1)


class FamilyBalancedSelectionPolicy(StrictModel):
    """The frozen, content-addressed family-balanced source-selection policy.

    Selection draws first-N-per-family in a canonical within-family order from the
    train partition only, then interleaves families round-robin. Deterministic:
    no randomness, no runtime seed, no filesystem-enumeration or timestamp
    dependence, no validation/test/evaluation input. Changing any bound field
    changes ``policy_id``.
    """

    schema_version: Literal[1] = 1
    policy_format_version: Literal[1] = 1
    allowed_partition: Literal["train"] = "train"
    target_total: int = Field(ge=1)
    family_order: tuple[str, ...] = Field(min_length=1)
    per_family_allocation: tuple[FamilyQuota, ...] = Field(min_length=1)
    scarcity_rule: Literal["exact_quota_no_redistribution"] = (
        "exact_quota_no_redistribution")
    fill_rule: Literal["deterministic_per_family_prefix"] = (
        "deterministic_per_family_prefix")
    within_family_order: Literal["group_id_then_example_id"] = (
        "group_id_then_example_id")
    final_order_rule: Literal["round_robin_by_family_order"] = (
        "round_robin_by_family_order")
    policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> FamilyBalancedSelectionPolicy:
        order = list(self.family_order)
        if len(order) != len(set(order)):
            raise ValueError("family_order must not repeat a family")
        for fam in order:
            if fam not in TRAINING_CANDIDATE_FAMILIES:
                raise ValueError(f"unsupported fault family: {fam}")
        alloc_families = [q.fault_family for q in self.per_family_allocation]
        if alloc_families != order:
            raise ValueError(
                "per_family_allocation families/order must equal family_order")
        if sum(q.count for q in self.per_family_allocation) != self.target_total:
            raise ValueError("per-family quotas must sum to target_total")
        if self.policy_id != _derive_policy_id(self):
            raise ValueError("policy_id does not match the policy content")
        return self


class BalancedSelectionResult(StrictModel):
    """The frozen, self-validating result of applying the policy to a prepared
    corpus: which train sources were selected, under which family, and the
    deterministic round-robin order they feed the corpus builder in."""

    schema_version: Literal[1] = 1
    policy_id: str = Field(min_length=1)
    source_prepared_digest: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    family_order: tuple[str, ...] = Field(min_length=1)
    selected: tuple[SelectedSource, ...] = Field(min_length=1)
    ordered_source_example_ids: tuple[str, ...] = Field(min_length=1)
    per_family_counts: tuple[FamilyQuota, ...] = Field(min_length=1)
    total_count: int = Field(ge=1)
    selection_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> BalancedSelectionResult:
        ids = [s.example_id for s in self.selected]
        if len(ids) != len(set(ids)):
            raise ValueError("selected sources must be unique")
        if tuple(ids) != self.ordered_source_example_ids:
            raise ValueError("ordered ids must match selected order")
        if len(ids) != self.total_count:
            raise ValueError("total_count must equal the selected count")
        counts = {q.fault_family: q.count for q in self.per_family_counts}
        realised: dict[str, int] = {}
        for s in self.selected:
            realised[s.fault_family] = realised.get(s.fault_family, 0) + 1
        if realised != counts:
            raise ValueError("per_family_counts must match the selected sources")
        if sum(counts.values()) != self.total_count:
            raise ValueError("per-family counts must sum to total_count")
        _assert_round_robin(self.selected, self.family_order)
        if self.selection_digest != _derive_selection_digest(self):
            raise ValueError("selection_digest does not match the result content")
        return self


def _derive_policy_id(policy: FamilyBalancedSelectionPolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("policy_id", None)
    return "fbsel-" + sha256_canonical(payload)[:16]


def _derive_selection_digest(result: BalancedSelectionResult) -> str:
    payload = result.model_dump(mode="json")
    payload.pop("selection_digest", None)
    return "seldig-" + sha256_canonical(payload)[:24]


def _assert_round_robin(
    selected: tuple[SelectedSource, ...], family_order: tuple[str, ...],
) -> None:
    """Prove the order is exactly the round-robin interleave of the per-family
    subsequences taken in ``family_order`` (a family is skipped once exhausted)."""
    per_family: dict[str, list[str]] = {fam: [] for fam in family_order}
    for s in selected:
        if s.fault_family not in per_family:
            raise ValueError(f"selected family not in family_order: {s.fault_family}")
        per_family[s.fault_family].append(s.example_id)
    counts = {fam: len(per_family[fam]) for fam in family_order}
    expected: list[str] = []
    for column in range(max(counts.values(), default=0)):
        for fam in family_order:
            if column < counts[fam]:
                expected.append(per_family[fam][column])
    if [s.example_id for s in selected] != expected:
        raise ValueError("selected order is not the declared round-robin interleave")


def family_balanced_selection_policy(
    *,
    target_total: int = DEFAULT_SELECTION_TOTAL,
    allocation: tuple[tuple[str, int], ...] = DEFAULT_FAMILY_ALLOCATION,
) -> FamilyBalancedSelectionPolicy:
    """Construct the frozen family-balanced policy with a derived, content-
    addressed ``policy_id``. ``allocation`` is ``((family, quota), ...)`` in the
    canonical family order the round-robin uses."""
    quotas = tuple(FamilyQuota(fault_family=fam, count=n) for fam, n in allocation)
    family_order = tuple(fam for fam, _ in allocation)
    fields: dict[str, object] = {
        "target_total": target_total,
        "family_order": family_order,
        "per_family_allocation": quotas,
    }
    probe = FamilyBalancedSelectionPolicy.model_construct(**fields)  # type: ignore[arg-type]
    return FamilyBalancedSelectionPolicy(
        **fields,  # type: ignore[arg-type]
        policy_id=_derive_policy_id(probe))


def select_family_balanced(
    prepared: LoadedPrepared, *, policy: FamilyBalancedSelectionPolicy,
) -> BalancedSelectionResult:
    """Deterministically select the family-balanced training sources.

    Reads only accepted labels in the frozen TRAIN partition. Fails closed on a
    missing/short family, a non-train source, a rejected/missing-label example,
    an unsupported family, or a duplicate identity. Never redistributes a
    missing quota and never duplicates or synthesises an example.
    """
    if policy.allowed_partition != "train":
        raise SelectionError("policy allows a non-train partition")
    alloc = {q.fault_family: q.count for q in policy.per_family_allocation}
    groups: dict[str, list[tuple[str, str]]] = {fam: [] for fam in policy.family_order}
    for source in prepared.examples:
        if source.trace.partition is not DatasetPartition.TRAIN:
            continue
        if source.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        labels = source.labels
        if not isinstance(labels, AcceptedLabels):
            raise SelectionError(
                f"train example {source.trace.example_id} lacks accepted labels")
        fam = labels.fault_family
        if fam not in groups:
            raise SelectionError(f"unsupported fault family in train partition: {fam}")
        groups[fam].append((source.trace.group_id, source.trace.example_id))

    picked: dict[str, list[tuple[str, str]]] = {}
    for fam in policy.family_order:
        available = sorted(groups[fam])  # (group_id, example_id) canonical order
        if not available:
            raise SelectionError(f"required family absent from train partition: {fam}")
        need = alloc[fam]
        if len(available) < need:
            raise SelectionError(
                f"insufficient '{fam}' train examples: need {need}, have "
                f"{len(available)}; no redistribution permitted")
        picked[fam] = available[:need]

    ordered: list[SelectedSource] = []
    columns = max(alloc.values(), default=0)
    for column in range(columns):
        for fam in policy.family_order:
            if column < alloc[fam]:
                group_id, example_id = picked[fam][column]
                ordered.append(SelectedSource(
                    example_id=example_id, group_id=group_id, fault_family=fam))

    example_ids = [s.example_id for s in ordered]
    if len(example_ids) != len(set(example_ids)):
        raise SelectionError("duplicate source identity in the selection")
    if len(ordered) != policy.target_total:
        raise SelectionError(
            f"selected {len(ordered)} != target_total {policy.target_total}")

    per_family_counts = tuple(
        FamilyQuota(fault_family=fam, count=alloc[fam]) for fam in policy.family_order)
    fields: dict[str, object] = {
        "policy_id": policy.policy_id,
        "source_prepared_digest": prepared.manifest.prepared_digest,
        "dataset_version": prepared.manifest.dataset_version,
        "family_order": tuple(policy.family_order),
        "selected": tuple(ordered),
        "ordered_source_example_ids": tuple(example_ids),
        "per_family_counts": per_family_counts,
        "total_count": len(ordered),
    }
    probe = BalancedSelectionResult.model_construct(**fields)  # type: ignore[arg-type]
    return BalancedSelectionResult(
        **fields,  # type: ignore[arg-type]
        selection_digest=_derive_selection_digest(probe))


DEFAULT_GROUP_BALANCED_ALLOCATION: tuple[tuple[str, int], ...] = (
    ("bgp_neighbor_removal", 16),
    ("bgp_prefix_withdrawal", 16),
    ("bgp_remote_as_mismatch", 16),
    ("iface_admin_shutdown", 16),
)
GROUP_BALANCED_SELECTION_TOTAL = 64
#: Independent-group coverage floor per family (Gate 20C): remote-AS must span at
#: least eight independent TRAIN groups so its 16 examples are diverse coverage,
#: not repeated runs of a few groups. Other families are unconstrained (min 1).
DEFAULT_MIN_GROUPS: tuple[tuple[str, int], ...] = (
    ("bgp_neighbor_removal", 1),
    ("bgp_prefix_withdrawal", 1),
    ("bgp_remote_as_mismatch", 8),
    ("iface_admin_shutdown", 1),
)


class GroupBalancedSelectionPolicy(StrictModel):
    """The frozen, content-addressed GROUP-AWARE budget-preserving selection
    policy (Gate 20C). Identical to the Gate 19A family-balanced policy except the
    within-family fill draws one example per independent ``group_id`` in rotation
    (group round-robin), maximising independent-group coverage, and each family
    carries a fail-closed minimum-independent-group floor. Additive: it reuses the
    Gate 19A ``FamilyQuota``/``SelectedSource``/``BalancedSelectionResult`` and
    introduces the ``gbsel-`` id namespace, leaving every ``fbsel-`` id untouched.
    """

    schema_version: Literal[1] = 1
    policy_format_version: Literal[1] = 1
    allowed_partition: Literal["train"] = "train"
    target_total: int = Field(ge=1)
    family_order: tuple[str, ...] = Field(min_length=1)
    per_family_allocation: tuple[FamilyQuota, ...] = Field(min_length=1)
    min_groups_per_family: tuple[FamilyQuota, ...] = Field(min_length=1)
    scarcity_rule: Literal["exact_quota_no_redistribution"] = (
        "exact_quota_no_redistribution")
    fill_rule: Literal["deterministic_group_round_robin"] = (
        "deterministic_group_round_robin")
    within_family_order: Literal["group_round_robin_then_example_id"] = (
        "group_round_robin_then_example_id")
    final_order_rule: Literal["round_robin_by_family_order"] = (
        "round_robin_by_family_order")
    policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> GroupBalancedSelectionPolicy:
        order = list(self.family_order)
        if len(order) != len(set(order)):
            raise ValueError("family_order must not repeat a family")
        for fam in order:
            if fam not in TRAINING_CANDIDATE_FAMILIES:
                raise ValueError(f"unsupported fault family: {fam}")
        if [q.fault_family for q in self.per_family_allocation] != order:
            raise ValueError(
                "per_family_allocation families/order must equal family_order")
        if [q.fault_family for q in self.min_groups_per_family] != order:
            raise ValueError(
                "min_groups_per_family families/order must equal family_order")
        if sum(q.count for q in self.per_family_allocation) != self.target_total:
            raise ValueError("per-family quotas must sum to target_total")
        for alloc, floor in zip(self.per_family_allocation,
                                self.min_groups_per_family, strict=True):
            if floor.count > alloc.count:
                raise ValueError(
                    f"min group floor exceeds quota for {alloc.fault_family}")
        if self.policy_id != _derive_group_policy_id(self):
            raise ValueError("policy_id does not match the policy content")
        return self


def _derive_group_policy_id(policy: GroupBalancedSelectionPolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("policy_id", None)
    return "gbsel-" + sha256_canonical(payload)[:16]


def group_balanced_selection_policy(
    *,
    target_total: int = GROUP_BALANCED_SELECTION_TOTAL,
    allocation: tuple[tuple[str, int], ...] = DEFAULT_GROUP_BALANCED_ALLOCATION,
    min_groups: tuple[tuple[str, int], ...] = DEFAULT_MIN_GROUPS,
) -> GroupBalancedSelectionPolicy:
    """Construct the frozen group-aware policy with a content-addressed id."""
    quotas = tuple(FamilyQuota(fault_family=fam, count=n) for fam, n in allocation)
    floors = tuple(FamilyQuota(fault_family=fam, count=n) for fam, n in min_groups)
    family_order = tuple(fam for fam, _ in allocation)
    fields: dict[str, object] = {
        "target_total": target_total, "family_order": family_order,
        "per_family_allocation": quotas, "min_groups_per_family": floors}
    probe = GroupBalancedSelectionPolicy.model_construct(**fields)  # type: ignore[arg-type]
    return GroupBalancedSelectionPolicy(
        **fields, policy_id=_derive_group_policy_id(probe))  # type: ignore[arg-type]


def _group_round_robin(available: dict[str, list[str]], need: int) -> list[tuple[str, str]]:
    """Pick ``need`` (group_id, example_id) pairs by drawing one example per group
    in ``group_id`` order, rotating rounds, taking each group's examples in
    ``example_id`` order. Maximises independent-group coverage."""
    group_ids = sorted(available)
    picked: list[tuple[str, str]] = []
    column = 0
    while len(picked) < need:
        advanced = False
        for gid in group_ids:
            members = available[gid]
            if column < len(members):
                advanced = True
                picked.append((gid, members[column]))
                if len(picked) == need:
                    return picked
        if not advanced:
            break
        column += 1
    return picked


def select_group_balanced(
    prepared: LoadedPrepared, *, policy: GroupBalancedSelectionPolicy,
) -> BalancedSelectionResult:
    """Deterministically select the group-aware balanced training sources.

    Reads only accepted labels in the frozen TRAIN partition. For each family the
    quota is filled by group round-robin over the family's independent groups;
    fails closed on a short family, a group-coverage floor violation, a non-train
    or rejected/unlabelled example, an unsupported family, or a duplicate. Never
    redistributes a quota, oversamples, or synthesises.
    """
    if policy.allowed_partition != "train":
        raise SelectionError("policy allows a non-train partition")
    alloc = {q.fault_family: q.count for q in policy.per_family_allocation}
    floors = {q.fault_family: q.count for q in policy.min_groups_per_family}
    by_family_group: dict[str, dict[str, list[str]]] = {
        fam: {} for fam in policy.family_order}
    for source in prepared.examples:
        if source.trace.partition is not DatasetPartition.TRAIN:
            continue
        if source.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        labels = source.labels
        if not isinstance(labels, AcceptedLabels):
            raise SelectionError(
                f"train example {source.trace.example_id} lacks accepted labels")
        fam = labels.fault_family
        if fam not in by_family_group:
            raise SelectionError(f"unsupported fault family in train partition: {fam}")
        by_family_group[fam].setdefault(source.trace.group_id, []).append(
            source.trace.example_id)

    picked: dict[str, list[tuple[str, str]]] = {}
    for fam in policy.family_order:
        groups = {g: sorted(v) for g, v in by_family_group[fam].items()}
        total_available = sum(len(v) for v in groups.values())
        need = alloc[fam]
        if total_available < need:
            raise SelectionError(
                f"insufficient '{fam}' train examples: need {need}, have "
                f"{total_available}; no redistribution permitted")
        chosen = _group_round_robin(groups, need)
        covered = len({g for g, _ in chosen})
        if covered < floors[fam]:
            raise SelectionError(
                f"'{fam}' spans only {covered} independent groups; "
                f"policy requires >= {floors[fam]}")
        picked[fam] = chosen

    ordered: list[SelectedSource] = []
    columns = max(alloc.values(), default=0)
    for column in range(columns):
        for fam in policy.family_order:
            if column < alloc[fam]:
                group_id, example_id = picked[fam][column]
                ordered.append(SelectedSource(
                    example_id=example_id, group_id=group_id, fault_family=fam))

    example_ids = [s.example_id for s in ordered]
    if len(example_ids) != len(set(example_ids)):
        raise SelectionError("duplicate source identity in the selection")
    if len(ordered) != policy.target_total:
        raise SelectionError(
            f"selected {len(ordered)} != target_total {policy.target_total}")

    per_family_counts = tuple(
        FamilyQuota(fault_family=fam, count=alloc[fam]) for fam in policy.family_order)
    fields: dict[str, object] = {
        "policy_id": policy.policy_id,
        "source_prepared_digest": prepared.manifest.prepared_digest,
        "dataset_version": prepared.manifest.dataset_version,
        "family_order": tuple(policy.family_order),
        "selected": tuple(ordered),
        "ordered_source_example_ids": tuple(example_ids),
        "per_family_counts": per_family_counts,
        "total_count": len(ordered)}
    probe = BalancedSelectionResult.model_construct(**fields)  # type: ignore[arg-type]
    return BalancedSelectionResult(
        **fields, selection_digest=_derive_selection_digest(probe))  # type: ignore[arg-type]


def independent_group_counts(
    result: BalancedSelectionResult,
) -> tuple[FamilyQuota, ...]:
    """The number of distinct independent ``group_id``s selected per family."""
    groups: dict[str, set[str]] = {}
    for s in result.selected:
        groups.setdefault(s.fault_family, set()).add(s.group_id)
    return tuple(
        FamilyQuota(fault_family=fam, count=len(groups[fam]))
        for fam in sorted(groups))


class TrainingCorpusComparison(StrictModel):
    """A deterministic, byte-level comparison between two v2 training corpora
    (Gate 18B natural-order vs Gate 19 family-balanced). Proves the source
    composition is the sole change: shared source examples render byte-identical
    inputs/targets and the corpora bind the same feature policy, input template,
    and target template."""

    schema_version: Literal[1] = 1
    baseline_corpus_id: str = Field(min_length=1)
    candidate_corpus_id: str = Field(min_length=1)
    baseline_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    baseline_unique: bool
    candidate_unique: bool
    baseline_family_counts: tuple[FamilyQuota, ...]
    candidate_family_counts: tuple[FamilyQuota, ...]
    intersection_count: int = Field(ge=0)
    added_source_ids: tuple[str, ...]
    removed_source_ids: tuple[str, ...]
    ordering_identical: bool
    shared_example_count: int = Field(ge=0)
    shared_inputs_equal: bool
    shared_targets_equal: bool
    feature_policy_equal: bool
    input_template_equal: bool
    target_template_equal: bool


def _corpus_family(example: object) -> str:
    text = example.target.text  # type: ignore[attr-defined]
    return str(json.loads(text)["fault_family"])


def _family_counts(corpus: TrainingCorpus) -> tuple[FamilyQuota, ...]:
    counts: dict[str, int] = {}
    for e in corpus.examples:
        fam = _corpus_family(e)
        counts[fam] = counts.get(fam, 0) + 1
    return tuple(
        FamilyQuota(fault_family=fam, count=counts[fam]) for fam in sorted(counts))


def compare_training_corpora(
    baseline: TrainingCorpus, candidate: TrainingCorpus,
) -> TrainingCorpusComparison:
    """Compare the Gate 18B corpus (``baseline``) with the Gate 19 balanced
    corpus (``candidate``). Deterministic; parses the family only from each
    example's own target JSON; consults no evaluation artifact."""
    b_ids = [e.trace.source_example_id for e in baseline.examples]
    c_ids = [e.trace.source_example_id for e in candidate.examples]
    b_set, c_set = set(b_ids), set(c_ids)
    b_by = {e.trace.source_example_id: e for e in baseline.examples}
    c_by = {e.trace.source_example_id: e for e in candidate.examples}
    shared = sorted(b_set & c_set)
    inputs_equal = all(b_by[i].input.text == c_by[i].input.text for i in shared)
    targets_equal = all(b_by[i].target.text == c_by[i].target.text for i in shared)
    return TrainingCorpusComparison(
        baseline_corpus_id=baseline.training_corpus_id,
        candidate_corpus_id=candidate.training_corpus_id,
        baseline_count=len(b_ids), candidate_count=len(c_ids),
        baseline_unique=len(b_ids) == len(b_set),
        candidate_unique=len(c_ids) == len(c_set),
        baseline_family_counts=_family_counts(baseline),
        candidate_family_counts=_family_counts(candidate),
        intersection_count=len(shared),
        added_source_ids=tuple(sorted(c_set - b_set)),
        removed_source_ids=tuple(sorted(b_set - c_set)),
        ordering_identical=b_ids == c_ids,
        shared_example_count=len(shared),
        shared_inputs_equal=inputs_equal,
        shared_targets_equal=targets_equal,
        feature_policy_equal=baseline.feature_policy_id == candidate.feature_policy_id,
        input_template_equal=(
            baseline.input_template.input_template_id
            == candidate.input_template.input_template_id),
        target_template_equal=(
            baseline.target_template.target_template_id
            == candidate.target_template.target_template_id))
