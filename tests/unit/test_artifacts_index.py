"""Run-index unit tests: determinism, tamper detection, dedup, and safety.

Runs are materialized offline from the shared ``RunInputs`` fixtures (fixed
clock, no Docker), written with the real ``write_run_artifacts``, then indexed
and verified through the real index API. These tests own the index invariants;
the composition-root wiring test proves the same API under the live entry points.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from verifiednet.artifacts import (
    ArtifactIntegrityError,
    RunIndexError,
    add_run_to_index,
    compute_index_digest,
    load_run_index,
    load_verified_run_from_index,
    verify_run_index,
)
from verifiednet.artifacts.index import INDEX_FILE, RunIndexEntry


def _materialize(inputs: object, out_root: Path, write_inputs: Callable[..., object]) -> str:
    written = write_inputs(inputs, out_root)
    return written.run_id  # type: ignore[attr-defined]


def test_add_then_load_round_trips_single_run(
    accepted_run_inputs: object, write_inputs: Callable[..., object], tmp_path: Path
) -> None:
    run_id = _materialize(accepted_run_inputs, tmp_path, write_inputs)
    index = add_run_to_index(tmp_path, run_id)

    assert [e.run_id for e in index.entries] == [run_id]
    assert index.entries[0].acceptance_status == "accepted"
    loaded = load_verified_run_from_index(tmp_path, run_id)
    assert loaded.run_id == run_id


def test_index_digest_is_deterministic_and_order_independent() -> None:
    ts = datetime(2025, 1, 1, tzinfo=UTC)
    a = RunIndexEntry(
        run_id="run-a", incident_id="inc-1", scenario_id="s", template_id="t",
        acceptance_status="accepted", run_dir="run-a", run_digest="a" * 64,
        topology_hash="1" * 64, layout_schema_version=1, started_at=ts,
    )
    b = RunIndexEntry(
        run_id="run-b", incident_id="inc-2", scenario_id="s", template_id="t",
        acceptance_status="rejected", run_dir="run-b", run_digest="b" * 64,
        topology_hash="2" * 64, layout_schema_version=1, started_at=ts,
    )
    assert compute_index_digest((a, b)) == compute_index_digest((b, a))


def test_index_verifies_after_two_adds(
    make_accepted_inputs: Callable[[str], object],
    make_rejected_inputs: Callable[[str], object],
    write_inputs: Callable[..., object],
    tmp_path: Path,
) -> None:
    r1 = _materialize(make_accepted_inputs("run-idx-a"), tmp_path, write_inputs)
    r2 = _materialize(make_rejected_inputs("run-idx-b"), tmp_path, write_inputs)
    add_run_to_index(tmp_path, r1)
    add_run_to_index(tmp_path, r2)

    result = verify_run_index(tmp_path)
    assert result.verified is True
    index = load_run_index(tmp_path)
    assert [e.run_id for e in index.entries] == ["run-idx-a", "run-idx-b"]  # run-id sorted


def test_duplicate_run_id_is_rejected(
    make_accepted_inputs: Callable[[str], object],
    write_inputs: Callable[..., object],
    tmp_path: Path,
) -> None:
    run_id = _materialize(make_accepted_inputs("run-dup"), tmp_path, write_inputs)
    add_run_to_index(tmp_path, run_id)
    with pytest.raises(RunIndexError, match="duplicate run_id"):
        add_run_to_index(tmp_path, run_id)


def test_tampered_index_digest_fails_verification(
    accepted_run_inputs: object, write_inputs: Callable[..., object], tmp_path: Path
) -> None:
    run_id = _materialize(accepted_run_inputs, tmp_path, write_inputs)
    add_run_to_index(tmp_path, run_id)
    index_path = tmp_path / INDEX_FILE
    corrupted = index_path.read_text().replace(run_id, run_id[:-1] + "z", 1)
    index_path.write_text(corrupted)

    assert verify_run_index(tmp_path).verified is False


def test_tampered_run_payload_fails_index_verification(
    accepted_run_inputs: object, write_inputs: Callable[..., object], tmp_path: Path
) -> None:
    run_id = _materialize(accepted_run_inputs, tmp_path, write_inputs)
    add_run_to_index(tmp_path, run_id)
    victim = tmp_path / run_id / "run_manifest.json"
    victim.write_bytes(victim.read_bytes() + b" ")

    result = verify_run_index(tmp_path)
    assert result.verified is False
    assert any("run_verifies" in c.rule or "run_digest" in c.rule for c in result.failures)


def test_unindexed_run_directory_is_reported(
    make_accepted_inputs: Callable[[str], object],
    write_inputs: Callable[..., object],
    tmp_path: Path,
) -> None:
    r1 = _materialize(make_accepted_inputs("run-indexed"), tmp_path, write_inputs)
    add_run_to_index(tmp_path, r1)
    # Materialize a second run dir but never index it — a hidden run must be caught.
    _materialize(make_accepted_inputs("run-orphan"), tmp_path, write_inputs)

    result = verify_run_index(tmp_path)
    assert result.verified is False
    assert any("no_unindexed_run:run-orphan" == c.rule for c in result.failures)


def test_load_unknown_run_id_raises(
    accepted_run_inputs: object, write_inputs: Callable[..., object], tmp_path: Path
) -> None:
    run_id = _materialize(accepted_run_inputs, tmp_path, write_inputs)
    add_run_to_index(tmp_path, run_id)
    with pytest.raises(RunIndexError, match="exactly one index entry"):
        load_verified_run_from_index(tmp_path, "run-does-not-exist")


def test_unsafe_run_dir_entry_is_rejected_at_validation() -> None:
    # Path traversal in run_dir must be refused by the entry validator itself.
    with pytest.raises(ValueError, match="unsafe"):
        RunIndexEntry(
            run_id="run-x", incident_id="inc", scenario_id="s", template_id="t",
            acceptance_status="accepted", run_dir="../escape", run_digest="a" * 64,
            topology_hash="1" * 64, layout_schema_version=1,
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
        )


def test_load_through_tampered_index_raises_integrity_error(
    accepted_run_inputs: object, write_inputs: Callable[..., object], tmp_path: Path
) -> None:
    run_id = _materialize(accepted_run_inputs, tmp_path, write_inputs)
    add_run_to_index(tmp_path, run_id)
    victim = tmp_path / run_id / "incident.json"
    victim.write_bytes(victim.read_bytes() + b" ")
    with pytest.raises(ArtifactIntegrityError):
        load_verified_run_from_index(tmp_path, run_id)
