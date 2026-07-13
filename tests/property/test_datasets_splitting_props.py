"""Gate 6.2 splitting property tests: determinism, totality, group cohesion."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.datasets import (
    SPLIT_BUCKET_COUNT,
    DatasetPartition,
    SplitPolicy,
    assign_group_split,
    split_policy_id,
)

pytestmark = pytest.mark.property

_TRAINABLE = frozenset(
    {DatasetPartition.TRAIN, DatasetPartition.VALIDATION, DatasetPartition.TEST}
)

_group_ids = st.integers(min_value=0, max_value=(1 << 64) - 1).map(
    lambda n: f"grp-{n:016x}"
)
_salts = st.text(min_size=1, max_size=12)


@st.composite
def _policies(draw: st.DrawFn) -> SplitPolicy:
    train = draw(st.integers(min_value=1, max_value=SPLIT_BUCKET_COUNT - 2))
    validation = draw(st.integers(min_value=1, max_value=SPLIT_BUCKET_COUNT - 1 - train))
    test = SPLIT_BUCKET_COUNT - train - validation
    return SplitPolicy(salt=draw(_salts), train_buckets=train,
                       validation_buckets=validation, test_buckets=test)


@given(group_id=_group_ids, policy=_policies())
@settings(max_examples=200)
def test_assignment_is_total_and_trainable(group_id: str, policy: SplitPolicy) -> None:
    partition = assign_group_split(group_id=group_id, policy=policy)
    assert partition in _TRAINABLE  # never abstention, always a valid split


@given(group_id=_group_ids, policy=_policies())
@settings(max_examples=200)
def test_assignment_is_deterministic(group_id: str, policy: SplitPolicy) -> None:
    first = assign_group_split(group_id=group_id, policy=policy)
    # A structurally identical policy re-derives the identical partition.
    twin = SplitPolicy(salt=policy.salt, train_buckets=policy.train_buckets,
                       validation_buckets=policy.validation_buckets,
                       test_buckets=policy.test_buckets)
    assert assign_group_split(group_id=group_id, policy=twin) is first


@given(
    group_id=_group_ids,
    policy=_policies(),
    salt2=_salts,
)
@settings(max_examples=150)
def test_policy_id_matches_iff_content_matches(
    group_id: str, policy: SplitPolicy, salt2: str
) -> None:
    other = SplitPolicy(salt=salt2, train_buckets=policy.train_buckets,
                        validation_buckets=policy.validation_buckets,
                        test_buckets=policy.test_buckets)
    if salt2 == policy.salt:
        assert split_policy_id(other) == split_policy_id(policy)
    else:
        assert split_policy_id(other) != split_policy_id(policy)


@given(
    a=st.integers(min_value=1, max_value=SPLIT_BUCKET_COUNT - 2),
    salt=_salts,
    group_id=_group_ids,
)
@settings(max_examples=150)
def test_all_but_two_buckets_train_is_stable(a: int, salt: str, group_id: str) -> None:
    # Constructing an extreme-but-valid policy never raises and stays total.
    policy = SplitPolicy(salt=salt, train_buckets=a,
                         validation_buckets=(SPLIT_BUCKET_COUNT - a - 1),
                         test_buckets=1)
    assert assign_group_split(group_id=group_id, policy=policy) in _TRAINABLE
