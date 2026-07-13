"""Live accepted interface administrative shutdown incident (Gate 5.3).

FRR-mode, control point proven by the mandatory probe. Drives ONE real
runtime/interface incident via the production entry point with the family
binding: healthy → admin down (eth1 on router_a) → oper down + BGP lost +
reachability fails + peer-loopback route withdrawn → restore (no shutdown +
clear) → recovery (link up, session Established, routes back, running-config
byte-identical) → GroundTruth → canonical run directory → run index → cleanup.
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
from verifiednet.orchestrator import IFACE_ADMIN_SHUTDOWN_BINDING, run_accepted_incident
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import IncidentRecord, ScenarioDefinition, ScenarioTimeouts
from verifiednet.schemas.evidence import EvidenceBundle

pytestmark = pytest.mark.integration


def _scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="iface-admin-shutdown-2r-0001",
        family="interface",
        template_id="iface_admin_shutdown",
        version=1,
        parameters={"target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0, command_s=10.0, poll_interval_s=1.0
        ),
    )


def _config_sha(bundle: EvidenceBundle, target: str) -> str:
    for record in bundle.records:
        if record.source.target == target and "config.sha256" in record.normalized:
            return str(record.normalized["config.sha256"])
    raise AssertionError(f"no config.sha256 for {target} in bundle")


def test_accepted_live_iface_shutdown_incident(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_id = unique_run_id("it-iface-down")
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
        binding=IFACE_ADMIN_SHUTDOWN_BINDING,
    )

    assert project_containers(project) == []
    assert project_networks(project) == []

    assert result.convergence.converged
    loaded = result.assembled.loaded
    record = loaded.incident
    assert record.status == "accepted"
    assert record.scenario.template_id == "iface_admin_shutdown"
    assert record.ground_truth is not None
    assert record.ground_truth.root_cause_label == "iface_admin_shutdown"
    assert record.fault is not None
    assert record.fault.parameter_name == "admin_state"
    assert (record.fault.before_value, record.fault.after_value) == ("up", "down")
    assert record.restoration is not None and record.restoration.completed
    assert loaded.ledger[-1].phase is LifecyclePhase.RECOVERY_VERIFIED

    verdicts = {v.check_id: v for v in record.ground_truth.verdicts}
    # both admin AND oper down proven at onset (the probe decision rule, live)
    assert any(k.startswith("iface_admin_down") and v.observed == ("down",)
               for k, v in verdicts.items())
    assert any(k.startswith("iface_oper_down") and v.observed == ("down",)
               for k, v in verdicts.items())
    assert any(k.startswith("reachability_fails") and v.observed == ("false",)
               for k, v in verdicts.items())
    # byte-identical config recovery, committable and cross-checked by evidence
    cfg = [v for k, v in verdicts.items()
           if k.startswith("config_unchanged:router_a") and v.phase == "recovery"]
    assert cfg and cfg[0].committable
    assert record.recovery_evidence is not None
    assert _config_sha(record.baseline_evidence, "router_a") == _config_sha(
        record.recovery_evidence, "router_a"
    )

    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)

    muts = [e for e in loaded.transcript if e.mode == "mutation"]
    assert len([e for e in muts if e.stage == "pending"]) == 3  # shutdown, no-shut, clear
    assert all(e.target == "router_a" for e in muts)

    assert verify_run_index(out_root).verified
    reloaded = load_verified_run_from_index(out_root, result.assembled.run_id)
    assert reloaded.incident == record
