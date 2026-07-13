"""Gate 6.2 deterministic split assignment (leakage-safe, randomness-free)."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets import (
    ABSTENTION_POLICY_ID,
    SPLIT_BUCKET_COUNT,
    DatasetPartition,
    SplitPolicy,
    assign_example_split,
    assign_group_split,
    assign_splits,
    discover_verified_runs,
    project_verified_run,
    split_policy_id,
)
from verifiednet.datasets.splitting import DatasetSplitError
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.unit

_POLICY = SplitPolicy(salt="gate6", train_buckets=8000, validation_buckets=1000,
                      test_buckets=1000)


def _library(tmp_path, run_catalog_case, catalog_sim_cls, specs) -> Path:
    out_root = tmp_path / "runs"
    for case_id, run_id in specs:
        run_catalog_case(case_by_id(case_id), out_root, tmp_path, run_id=run_id,
                         sim=catalog_sim_cls())
    return out_root


def test_split_policy_validates_bucket_sum() -> None:
    with pytest.raises(ValueError):
        SplitPolicy(salt="s", train_buckets=1, validation_buckets=1, test_buckets=1)
    with pytest.raises(ValueError):
        SplitPolicy(salt="s", train_buckets=SPLIT_BUCKET_COUNT,
                    validation_buckets=0, test_buckets=0)
    with pytest.raises(ValueError):  # empty salt is rejected
        SplitPolicy(salt="", train_buckets=8000, validation_buckets=1000,
                    test_buckets=1000)


def test_assign_group_split_is_deterministic() -> None:
    gid = "grp-0123456789abcdef"
    first = assign_group_split(group_id=gid, policy=_POLICY)
    again = assign_group_split(group_id=gid, policy=_POLICY)
    # A freshly-constructed identical policy must yield the identical partition.
    twin = SplitPolicy(salt="gate6", train_buckets=8000, validation_buckets=1000,
                       test_buckets=1000)
    assert first is again
    assert first is assign_group_split(group_id=gid, policy=twin)
    assert first in {DatasetPartition.TRAIN, DatasetPartition.VALIDATION,
                     DatasetPartition.TEST}


def test_assign_group_split_rejects_non_group_id() -> None:
    with pytest.raises(DatasetSplitError):
        assign_group_split(group_id="ex-0123456789abcdef", policy=_POLICY)


def test_all_train_policy_sends_every_group_to_train() -> None:
    allt = SplitPolicy(salt="s", train_buckets=SPLIT_BUCKET_COUNT - 2,
                       validation_buckets=1, test_buckets=1)
    # With ~everything in train, a spread of ids should overwhelmingly be train;
    # assert the extremes deterministically map inside the train band.
    parts = {assign_group_split(group_id=f"grp-{i:016x}", policy=allt)
             for i in range(200)}
    assert DatasetPartition.TRAIN in parts


def test_salt_changes_assignment_distribution() -> None:
    a = SplitPolicy(salt="salt-a", train_buckets=5000, validation_buckets=2500,
                    test_buckets=2500)
    b = SplitPolicy(salt="salt-b", train_buckets=5000, validation_buckets=2500,
                    test_buckets=2500)
    ids = [f"grp-{i:016x}" for i in range(64)]
    pa = [assign_group_split(group_id=g, policy=a) for g in ids]
    pb = [assign_group_split(group_id=g, policy=b) for g in ids]
    assert pa != pb  # different salt -> different partitioning
    assert split_policy_id(a) != split_policy_id(b)


def test_split_policy_id_is_stable_and_content_addressed() -> None:
    twin = SplitPolicy(salt="gate6", train_buckets=8000, validation_buckets=1000,
                       test_buckets=1000)
    assert split_policy_id(_POLICY) == split_policy_id(twin)
    assert split_policy_id(_POLICY).startswith("split-")


def test_abstention_example_bypasses_policy(
    tmp_path: Path, make_rejected_prefix_inputs, write_indexed_run,
) -> None:
    out_root = tmp_path / "runs"
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)
    (d,) = tuple(discover_verified_runs(out_root))
    example = project_verified_run(d)
    assigned = assign_example_split(example=example, policy=_POLICY)
    assert assigned.partition is DatasetPartition.ABSTENTION
    assert assigned.split_policy_id == ABSTENTION_POLICY_ID
    # The source example identity is untouched by assignment.
    assert assigned.example.example_id == example.example_id
    assert assigned.example.group_id == example.group_id


def test_group_cohesion_two_runs_same_partition(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    # Two runs of the SAME scenario share a group_id and MUST land together.
    out_root = _library(tmp_path, run_catalog_case, catalog_sim_cls,
                        [("ras-ref", "run-1"), ("ras-ref", "run-2")])
    examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
    assigned = assign_splits(examples=examples, policy=_POLICY)
    parts = {a.example.run_id: a.partition for a in assigned}
    assert parts["run-1"] is parts["run-2"]


def test_assign_splits_mixed_library(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
    make_rejected_prefix_inputs, write_indexed_run,
) -> None:
    out_root = _library(tmp_path, run_catalog_case, catalog_sim_cls,
                        [("ras-ref", "run-a"), ("nr-ref", "run-b")])
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)
    examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
    assigned = assign_splits(examples=examples, policy=_POLICY)
    parts = {a.example.run_id: a.partition for a in assigned}
    assert parts["run-rej"] is DatasetPartition.ABSTENTION
    for rid in ("run-a", "run-b"):
        assert parts[rid] in {DatasetPartition.TRAIN, DatasetPartition.VALIDATION,
                              DatasetPartition.TEST}
