"""Live shared run index: one accepted + one rejected run in ONE index (Gate 4 Step 6).

Runs both production entry points against the real lab into a SINGLE index root,
then proves the index holds both, loads each back through the index, and refuses
to verify once a single persisted run is tampered. This is the end-to-end closure
proof that the composition root maintains an integrity-verifiable index of
completed runs. Two lab lifecycles; teardown owned by the composition root.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.artifacts import load_run_index, load_verified_run_from_index, verify_run_index
from verifiednet.common.hashing import sha256_file
from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.orchestrator import (
    run_accepted_incident,
    run_precondition_rejected_incident,
)
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts

pytestmark = pytest.mark.integration


def _scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        family="bgp",
        template_id="bgp_remote_as_mismatch",
        version=1,
        parameters={"wrong_asn": 65999, "target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0, command_s=10.0, poll_interval_s=1.0
        ),
    )


def test_shared_index_holds_accepted_and_rejected(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    commit = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip() or "unknown"
    lock = Path("uv.lock")
    lock_hash = sha256_file(lock) if lock.is_file() else "0" * 64
    out_root = tmp_path / "runs"  # ONE shared index root for both runs

    acc = run_accepted_incident(
        out_root=out_root,
        work_dir=tmp_path / "lab-acc",
        run_ctx=RunContext(unique_run_id("it-shared-acc")),
        topology=topology,
        scenario=_scenario(),
        git_rev=commit,
        lock_hash=lock_hash,
        convergence_timeout_s=60.0,
    )
    rej = run_precondition_rejected_incident(
        out_root=out_root,
        work_dir=tmp_path / "lab-rej",
        run_ctx=RunContext(unique_run_id("it-shared-rej")),
        topology=topology,
        scenario=_scenario(),
        git_rev=commit,
        lock_hash=lock_hash,
        convergence_timeout_s=60.0,
    )

    # both runs present in the single index, and it verifies
    index = load_run_index(out_root)
    ids = {e.run_id for e in index.entries}
    assert ids == {acc.assembled.run_id, rej.assembled.run_id}
    statuses = {e.run_id: e.acceptance_status for e in index.entries}
    assert statuses[acc.assembled.run_id] == "accepted"
    assert statuses[rej.assembled.run_id] == "rejected"
    assert verify_run_index(out_root).verified

    # distinct run digests; each loads back through the shared index
    assert acc.assembled.run_digest != rej.assembled.run_digest
    assert load_verified_run_from_index(
        out_root, acc.assembled.run_id
    ).incident.status == "accepted"
    assert load_verified_run_from_index(
        out_root, rej.assembled.run_id
    ).incident.status == "rejected"

    # tamper exactly one persisted run -> the whole index refuses to verify
    victim = out_root / rej.assembled.run_id / "incident.json"
    victim.write_bytes(victim.read_bytes() + b" ")
    assert verify_run_index(out_root).verified is False
