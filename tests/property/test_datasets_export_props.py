"""Gate 6.2 Part 3 export property tests: deterministic digest + framing."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.common.hashing import sha256_bytes
from verifiednet.datasets.models import (
    DATASET_GENERATOR,
    DatasetFileHash,
    DatasetPartitionCounts,
    compute_dataset_digest,
)

pytestmark = pytest.mark.property

_hex64 = st.integers(min_value=0, max_value=(1 << 64) - 1).map(
    lambda n: sha256_bytes(str(n).encode())
)


@st.composite
def _file_sets(draw: st.DrawFn) -> tuple[DatasetFileHash, ...]:
    paths = ["splits/train.jsonl", "splits/validation.jsonl",
             "splits/test.jsonl", "splits/abstention.jsonl"]
    files = [
        DatasetFileHash(relative_path=p, sha256=draw(_hex64),
                        size=draw(st.integers(min_value=0, max_value=10_000)))
        for p in paths
    ]
    return tuple(files)


@st.composite
def _counts(draw: st.DrawFn) -> DatasetPartitionCounts:
    return DatasetPartitionCounts(
        train=draw(st.integers(0, 50)), validation=draw(st.integers(0, 50)),
        test=draw(st.integers(0, 50)), abstention=draw(st.integers(0, 50)),
    )


def _digest(files: tuple[DatasetFileHash, ...], counts: DatasetPartitionCounts,
            *, policy_id: str = "split-0123456789abcdef") -> str:
    return compute_dataset_digest(
        schema_version=1, export_version=1, dataset_version="v1",
        generated_by=DATASET_GENERATOR, source_index_digest="a" * 64,
        split_policy_id=policy_id, partition_counts=counts, files=files,
    )


@given(files=_file_sets(), counts=_counts())
@settings(max_examples=200)
def test_digest_is_deterministic(files, counts) -> None:
    assert _digest(files, counts) == _digest(files, counts)


@given(files=_file_sets(), counts=_counts())
@settings(max_examples=200)
def test_digest_independent_of_file_order(files, counts) -> None:
    # The digest path-sorts internally, so input order must not matter.
    assert _digest(files, counts) == _digest(tuple(reversed(files)), counts)


@given(files=_file_sets(), counts=_counts(), pid=st.text(min_size=1, max_size=20))
@settings(max_examples=150)
def test_digest_changes_with_policy_id(files, counts, pid) -> None:
    base = _digest(files, counts)
    other = _digest(files, counts, policy_id=pid)
    if pid == "split-0123456789abcdef":
        assert other == base
    else:
        assert other != base


@given(
    a=st.integers(0, 100), b=st.integers(0, 100),
    c=st.integers(0, 100), d=st.integers(0, 100),
)
@settings(max_examples=150)
def test_partition_counts_totals(a: int, b: int, c: int, d: int) -> None:
    counts = DatasetPartitionCounts(train=a, validation=b, test=c, abstention=d)
    assert counts.accepted_total == a + b + c
    assert counts.total == a + b + c + d
