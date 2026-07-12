"""Unit tests for the append-only fault-lifecycle ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifiednet.common.errors import LedgerError, PhaseTransitionError
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import (
    LEGAL_TRANSITIONS,
    Ledger,
    LifecyclePhase,
)

pytestmark = pytest.mark.unit

FULL_WALK = (
    LifecyclePhase.PRECHECKED,
    LifecyclePhase.INJECTING,
    LifecyclePhase.INJECTED,
    LifecyclePhase.ONSET_VERIFIED,
    LifecyclePhase.RESTORING,
    LifecyclePhase.RESTORED,
    LifecyclePhase.RECOVERY_VERIFIED,
)


def test_starts_pending_with_no_records(run_ctx: RunContext) -> None:
    ledger = Ledger(run_ctx)
    assert ledger.current is LifecyclePhase.PENDING
    assert ledger.records == ()


def test_legal_full_walk_appends_with_increasing_seq(run_ctx: RunContext) -> None:
    ledger = Ledger(run_ctx)
    for phase in FULL_WALK:
        ledger.append(phase, detail=f"enter {phase}")
    assert ledger.current is LifecyclePhase.RECOVERY_VERIFIED
    seqs = [record.seq for record in ledger.records]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert [record.phase for record in ledger.records] == list(FULL_WALK)
    assert all(record.at.tzinfo is not None for record in ledger.records)


@pytest.mark.parametrize(
    ("walk", "bad"),
    [
        ((), LifecyclePhase.INJECTED),  # PENDING -> INJECTED
        ((LifecyclePhase.PRECHECKED,), LifecyclePhase.RESTORED),  # PRECHECKED -> RESTORED
        (FULL_WALK, LifecyclePhase.PENDING),  # RECOVERY_VERIFIED -> anything
        (FULL_WALK, LifecyclePhase.RESTORING),
        ((), LifecyclePhase.PENDING),  # PENDING -> PENDING
    ],
)
def test_illegal_jumps_raise(
    run_ctx: RunContext, walk: tuple[LifecyclePhase, ...], bad: LifecyclePhase
) -> None:
    ledger = Ledger(run_ctx)
    for phase in walk:
        ledger.append(phase)
    before = ledger.records
    with pytest.raises(PhaseTransitionError, match="illegal"):
        ledger.append(bad)
    assert ledger.records == before  # nothing recorded on failure


def test_injecting_may_transition_to_restoring(run_ctx: RunContext) -> None:
    """Mutation-failure recovery path: INJECTING -> RESTORING is legal."""
    ledger = Ledger(run_ctx)
    ledger.append(LifecyclePhase.PRECHECKED)
    ledger.append(LifecyclePhase.INJECTING)
    ledger.append(LifecyclePhase.RESTORING)
    assert ledger.current is LifecyclePhase.RESTORING


def test_recovery_verified_is_terminal() -> None:
    assert LEGAL_TRANSITIONS[LifecyclePhase.RECOVERY_VERIFIED] == frozenset()


def test_transition_table_covers_every_phase() -> None:
    assert set(LEGAL_TRANSITIONS) == set(LifecyclePhase)


def test_file_backed_append_writes_jsonl(run_ctx: RunContext, tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(run_ctx, path=path)
    ledger.append(LifecyclePhase.PRECHECKED, detail="checks ok")
    ledger.append(LifecyclePhase.INJECTING)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["phase"] == "prechecked"
    assert first["detail"] == "checks ok"
    assert list(first) == sorted(first)  # canonical: sorted keys


def test_read_round_trips(run_ctx: RunContext, tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(run_ctx, path=path)
    for phase in FULL_WALK:
        ledger.append(phase)
    records, torn = Ledger.read(path)
    assert not torn
    assert records == ledger.records


def test_torn_final_line_tolerated(run_ctx: RunContext, tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(run_ctx, path=path)
    ledger.append(LifecyclePhase.PRECHECKED)
    ledger.append(LifecyclePhase.INJECTING)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 99, "phase": "inj')  # torn mid-write
    records, torn = Ledger.read(path)
    assert torn is True
    assert len(records) == 2
    assert records == ledger.records


def test_malformed_middle_line_raises(run_ctx: RunContext, tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(run_ctx, path=path)
    ledger.append(LifecyclePhase.PRECHECKED)
    original = path.read_text(encoding="utf-8")
    path.write_text("not json at all\n" + original, encoding="utf-8")
    with pytest.raises(LedgerError, match="corruption at line 1"):
        Ledger.read(path)


def test_read_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LedgerError):
        Ledger.read(tmp_path / "absent.jsonl")


def test_write_failure_wrapped_in_ledger_error(run_ctx: RunContext, tmp_path: Path) -> None:
    ledger = Ledger(run_ctx, path=tmp_path / "no-such-dir" / "ledger.jsonl")
    with pytest.raises(LedgerError, match="ledger write failed"):
        ledger.append(LifecyclePhase.PRECHECKED)
    # The failed write must not be recorded in memory either.
    assert ledger.records == ()
    assert ledger.current is LifecyclePhase.PENDING


def test_no_module_global_state(run_ctx: RunContext) -> None:
    first = Ledger(run_ctx)
    second = Ledger(run_ctx)
    first.append(LifecyclePhase.PRECHECKED)
    assert second.current is LifecyclePhase.PENDING
    assert second.records == ()
