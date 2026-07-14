"""Gate 9 property tests: order independence, id stability, deterministic ranking."""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.evaluation import ComparisonRow, compute_ranking, derive_benchmark_id

pytestmark = pytest.mark.property

_ids = st.lists(
    st.integers(min_value=0, max_value=1 << 30).map(lambda n: f"baseline-{n:016x}"),
    min_size=1, max_size=6, unique=True,
)
_acc = st.one_of(st.none(), st.integers(0, 100).map(lambda n: f"{n / 100:.6f}"))


@given(ids=_ids, perm_seed=st.integers(0, 1000))
@settings(max_examples=200)
def test_benchmark_id_is_order_independent(ids: list[str], perm_seed: int) -> None:
    shuffled = list(ids)
    # deterministic rotation as a permutation (avoids Random in the test body)
    k = perm_seed % len(shuffled)
    shuffled = shuffled[k:] + shuffled[:k]
    kw = dict(benchmark_version=1, benchmark_name="b", task_id="task-0",
              prepared_digest="a" * 64, normalization_policy_id="norm-0",
              scoring_policy_version=1)
    assert derive_benchmark_id(predictor_identifiers=tuple(ids), **kw) == \
        derive_benchmark_id(predictor_identifiers=tuple(shuffled), **kw)


@st.composite
def _rows(draw: st.DrawFn) -> tuple[ComparisonRow, ...]:
    ids = draw(_ids)
    rows = []
    for i in ids:
        rows.append(ComparisonRow(
            predictor_identifier=i, evaluation_id=f"eval-{i[-16:]}",
            accepted_evaluated=4, accepted_correct=draw(st.integers(0, 4)),
            exact_match_accuracy=draw(_acc), abstention_count=1,
            abstention_correct=draw(st.integers(0, 1)), abstention_accuracy=draw(_acc),
            invalid_prediction_count=draw(st.integers(0, 3)), evaluation_count=5))
    return tuple(rows)


@given(rows=_rows(), rot=st.integers(0, 1000))
@settings(max_examples=200)
def test_ranking_is_order_independent_and_total(rows, rot) -> None:
    k = rot % len(rows)
    shuffled = tuple(rows[k:] + rows[:k])
    r1 = compute_ranking(rows)
    r2 = compute_ranking(shuffled)
    assert r1 == r2  # order independent
    ranks = [e.rank for e in r1]
    assert ranks == list(range(1, len(rows) + 1))  # total order, dense 1..n
    # every predictor appears exactly once
    assert len({e.predictor_identifier for e in r1}) == len(rows)


def _sort_key(row: ComparisonRow):
    from decimal import Decimal

    def _d(v: str | None) -> Decimal:
        return Decimal("-1") if v is None else Decimal(v)

    return (-_d(row.exact_match_accuracy), -_d(row.abstention_accuracy),
            row.invalid_prediction_count, row.predictor_identifier)


@given(rows=_rows())
@settings(max_examples=100)
def test_ranking_respects_accuracy_then_identifier(rows) -> None:
    ranking = compute_ranking(rows)
    by_id = {row.predictor_identifier: row for row in rows}
    for a, b in itertools.pairwise(ranking):
        ra = by_id[a.predictor_identifier]
        rb = by_id[b.predictor_identifier]
        assert _sort_key(ra) <= _sort_key(rb)  # non-decreasing key down the ranking
