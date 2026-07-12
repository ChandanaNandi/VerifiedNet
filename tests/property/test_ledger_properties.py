"""Hypothesis property: a phase sequence is accepted iff every step is legal."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.common.errors import PhaseTransitionError
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LEGAL_TRANSITIONS, Ledger, LifecyclePhase

pytestmark = pytest.mark.property

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

phases = st.sampled_from(list(LifecyclePhase))


def _fresh_ledger() -> Ledger:
    return Ledger(RunContext("run-prop-0001", clock=lambda: EPOCH))


@settings(max_examples=50, deadline=None, derandomize=True)
@given(st.lists(phases, max_size=10))
def test_walk_accepted_iff_every_step_legal(walk: list[LifecyclePhase]) -> None:
    ledger = _fresh_ledger()
    model_current = LifecyclePhase.PENDING
    for phase in walk:
        legal = phase in LEGAL_TRANSITIONS[model_current]
        if legal:
            ledger.append(phase)
            model_current = phase
        else:
            with pytest.raises(PhaseTransitionError):
                ledger.append(phase)
        assert ledger.current is model_current
    assert [record.phase for record in ledger.records] == [
        phase
        for phase, ok in _replay(walk)
        if ok
    ]


def _replay(walk: list[LifecyclePhase]) -> list[tuple[LifecyclePhase, bool]]:
    current = LifecyclePhase.PENDING
    out: list[tuple[LifecyclePhase, bool]] = []
    for phase in walk:
        ok = phase in LEGAL_TRANSITIONS[current]
        if ok:
            current = phase
        out.append((phase, ok))
    return out


@settings(max_examples=50, deadline=None, derandomize=True)
@given(st.lists(phases, min_size=1, max_size=10))
def test_seq_strictly_increases_over_accepted_appends(walk: list[LifecyclePhase]) -> None:
    ledger = _fresh_ledger()
    for phase in walk:
        if phase in LEGAL_TRANSITIONS[ledger.current]:
            ledger.append(phase)
    seqs = [record.seq for record in ledger.records]
    assert seqs == sorted(set(seqs))
