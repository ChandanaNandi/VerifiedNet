"""Live accepted BGP prefix-advertisement withdrawal incident (Gate 5.4).

Routing-intent family: the BGP session stays ESTABLISHED throughout. Drives ONE
real incident via the production entry point with the family binding: healthy →
withdraw the target's advertised loopback → the peer loses that route while both
sessions stay Established and all other routes/reachability are unaffected →
restore (re-advertise, NO forced reset) → recovery (route back, config
byte-identical) → GroundTruth → run directory → run index → cleanup.
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
from verifiednet.orchestrator import BGP_PREFIX_WITHDRAWAL_BINDING, run_accepted_incident
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import IncidentRecord, ScenarioDefinition, ScenarioTimeouts

pytestmark = pytest.mark.integration

TARGET_LOOPBACK = "10.255.0.1/32"


def _scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-prefix-withdrawal-2r-0001",
        family="bgp",
        template_id="bgp_prefix_withdrawal",
        version=1,
        parameters={"target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0, command_s=10.0, poll_interval_s=1.0
        ),
    )


def test_accepted_live_prefix_withdrawal_incident(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_id = unique_run_id("it-prefix-wd")
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
        binding=BGP_PREFIX_WITHDRAWAL_BINDING,
    )

    assert project_containers(project) == []
    assert project_networks(project) == []

    assert result.convergence.converged
    loaded = result.assembled.loaded
    record = loaded.incident
    assert record.status == "accepted"
    assert record.scenario.template_id == "bgp_prefix_withdrawal"
    assert record.ground_truth is not None
    assert record.ground_truth.root_cause_label == "bgp_prefix_withdrawal"
    assert record.fault is not None
    assert record.fault.parameter_name == "network"
    assert record.fault.before_value == f"{TARGET_LOOPBACK} advertised"
    assert record.fault.after_value == "withdrawn"
    # the distinguishing property: session never dropped -> no forced reset
    assert record.restoration is not None
    assert record.restoration.forced_reset_used is False
    assert record.restoration.forced_reset_command == ""
    assert loaded.ledger[-1].phase is LifecyclePhase.RECOVERY_VERIFIED

    verdicts = {v.check_id: v for v in record.ground_truth.verdicts}
    # route absent on the peer AND both sessions Established at onset
    assert any(k.startswith("route_absent:router_b") and v.observed == ("false",)
               for k, v in verdicts.items())
    established_onset = [v for k, v in verdicts.items()
                        if k.startswith("bgp_established") and v.phase == "onset"]
    assert established_onset and all(v.observed == ("Established",) for v in established_onset)
    # byte-identical config recovery
    cfg = [v for k, v in verdicts.items()
           if k.startswith("config_unchanged:router_a") and v.phase == "recovery"]
    assert cfg and cfg[0].committable

    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)

    # exactly two mutation pairs (withdraw, restore) — no clear bgp
    muts = [e for e in loaded.transcript if e.mode == "mutation"]
    assert len([e for e in muts if e.stage == "pending"]) == 2
    assert all(e.target == "router_a" for e in muts)

    assert verify_run_index(out_root).verified
    reloaded = load_verified_run_from_index(out_root, result.assembled.run_id)
    assert reloaded.incident == record
