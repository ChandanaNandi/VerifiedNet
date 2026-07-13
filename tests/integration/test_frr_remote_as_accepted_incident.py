"""Live accepted BGP remote-AS-mismatch incident, through the Gate 4 composition root.

Drives ONE real incident end to end via the PRODUCTION entry point
``run_accepted_incident`` (not hand-wired in the test): healthy convergence ->
preconditions -> inject (wrong-AS on router_a) -> onset -> restore -> recovery ->
GroundTruth -> accepted IncidentRecord -> canonical run directory -> run index ->
verified cleanup. The composition root owns restoration-on-failure and teardown;
this test asserts on the assembled, indexed, reload-through-index result plus an
independent host-side zero-resource proof.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.artifacts import load_verified_run_from_index, verify_run_index
from verifiednet.common.hashing import sha256_file
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LifecyclePhase
from verifiednet.incidents.manifests import incident_to_json_bytes
from verifiednet.labs.frr.compose_project import project_name_for_run
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.orchestrator import run_accepted_incident
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import IncidentRecord, ScenarioDefinition, ScenarioTimeouts

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


def test_accepted_live_remote_as_incident(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_id = unique_run_id("it-incident")
    project = project_name_for_run(run_id)
    commit = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip() or "unknown"
    lock = Path("uv.lock")
    lock_hash = sha256_file(lock) if lock.is_file() else "0" * 64
    out_root = tmp_path / "runs"

    result = run_accepted_incident(
        out_root=out_root,
        work_dir=tmp_path / "lab",
        run_ctx=RunContext(run_id),
        topology=topology,
        scenario=_scenario(),
        git_rev=commit,
        lock_hash=lock_hash,
        convergence_timeout_s=60.0,
    )

    # -- teardown proof (independent host-side) -------------------------------
    assert project_containers(project) == []
    assert project_networks(project) == []

    assert result.convergence.converged
    assembled = result.assembled
    loaded = assembled.loaded
    record = loaded.incident

    # -- accepted record content ---------------------------------------------
    assert record.status == "accepted"
    assert record.ground_truth is not None
    assert record.rejection is None
    assert record.restoration is not None and record.restoration.completed
    assert record.restoration.forced_reset_used is True
    assert record.incident_id.startswith("inc-")
    assert loaded.ledger[-1].phase is LifecyclePhase.RECOVERY_VERIFIED

    # deterministic id + canonical round-trip
    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)

    # mutation transcript fully paired; router_b never mutated
    muts = [e for e in loaded.transcript if e.mode == "mutation"]
    pending = [e for e in muts if e.stage == "pending"]
    completed = [e for e in muts if e.stage == "completed"]
    assert len(pending) == len(completed) == 3  # inject, restore, clear
    assert {e.invocation.command_id for e in pending if e.invocation} == {
        e.invocation.command_id for e in completed if e.invocation
    }
    assert all(e.target == "router_a" for e in muts)

    # every ground-truth evidence id resolves in the persisted evidence
    written_ev = {r.evidence_id for b in loaded.evidence.values() for r in b.records}
    assert set(record.ground_truth.accepted_evidence_ids) <= written_ev

    # -- run index: discoverable + reload-through-index round trip ------------
    assert verify_run_index(out_root).verified
    assert assembled.index_entry.acceptance_status == "accepted"
    reloaded = load_verified_run_from_index(out_root, assembled.run_id)
    assert reloaded.run_digest == assembled.run_digest
    assert reloaded.incident == record
