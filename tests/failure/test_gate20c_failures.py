"""Gate 20C failure tests: group-aware selection fails closed on an insufficient
family, a remote-AS independent-group-coverage shortfall, an unsupported family,
a duplicate/forged result, and a non-train partition policy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.datasets.models import DatasetPartition
from verifiednet.datasets.prepared import LoadedPrepared, PreparedManifest
from verifiednet.training.selection import (
    BalancedSelectionResult,
    GroupBalancedSelectionPolicy,
    SelectionError,
    group_balanced_selection_policy,
    select_group_balanced,
)

pytestmark = pytest.mark.failure

_RAS = "bgp_remote_as_mismatch"


def _prepared(rows, digest="prep-fail-" + "0" * 14):
    ordered = tuple(sorted(rows, key=lambda e: e.trace.example_id))
    return LoadedPrepared(
        manifest=PreparedManifest.model_construct(
            prepared_digest=digest, dataset_version="v4-remoteas-expansion"),
        examples=ordered, by_partition={})


def test_insufficient_family_fails_closed(coverage_prepared) -> None:
    # only 3 remote-AS groups of 2 = 6 examples, but quota is 16
    prepared = coverage_prepared(ras_groups=((), (2, 2, 2)))
    with pytest.raises(SelectionError, match="insufficient 'bgp_remote_as_mismatch'"):
        select_group_balanced(prepared, policy=group_balanced_selection_policy())


def test_group_floor_shortfall_fails_closed(coverage_prepared) -> None:
    # 16 remote-AS examples but only 4 groups (4 x 4) -> < 8 group floor
    prepared = coverage_prepared(ras_groups=((4, 4), (4, 4)))
    with pytest.raises(SelectionError, match="independent groups"):
        select_group_balanced(prepared, policy=group_balanced_selection_policy())


def test_unsupported_family_in_train_fails_closed(coverage_prepared) -> None:
    ex = coverage_prepared.example
    rows = list(coverage_prepared().examples)
    rows.append(ex("ex-bad-9999", "grp-bad", "totally_unknown_family",
                   partition=DatasetPartition.TRAIN))
    with pytest.raises(SelectionError, match="unsupported fault family"):
        select_group_balanced(_prepared(rows), policy=group_balanced_selection_policy())


def test_non_train_policy_refused() -> None:
    p = group_balanced_selection_policy()
    forged = p.model_copy(update={"allowed_partition": "train"})  # stays train
    # tamper via model_construct to bypass the Literal, then selection refuses
    bad = GroupBalancedSelectionPolicy.model_construct(
        **{**forged.model_dump(), "allowed_partition": "validation"})
    with pytest.raises(SelectionError, match="non-train partition"):
        select_group_balanced(_prepared([]), policy=bad)


def test_forged_selection_digest_rejected(coverage_prepared) -> None:
    sel = select_group_balanced(coverage_prepared(), policy=group_balanced_selection_policy())
    payload = sel.model_dump()
    payload["total_count"] = 63
    with pytest.raises(ValidationError):
        BalancedSelectionResult.model_validate(payload)
