"""Live accepted BGP neighbor-removal incident, through the composition root.

Gate 5.2: drives ONE real missing-object incident via the PRODUCTION entry
point with the family's explicit binding: healthy convergence → preconditions
(incl. affirmative peer presence) → remove the neighbor object on router_a →
onset (peer AFFIRMATIVELY absent from the summary; peer-side session down;
routes withdrawn both directions) → restore (recreate + activate + clear) →
recovery (session Established, correct remote-as, peer present, routes back,
running-config BYTE-IDENTICAL to baseline) → GroundTruth → canonical run
directory → run index → verified cleanup.
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
from verifiednet.orchestrator import BGP_NEIGHBOR_REMOVAL_BINDING, run_accepted_incident
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import IncidentRecord, ScenarioDefinition, ScenarioTimeouts
from verifiednet.schemas.evidence import EvidenceBundle

pytestmark = pytest.mark.integration

PEER_IP = "172.30.0.2"


def _scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-neighbor-removal-2r-0001",
        family="bgp",
        template_id="bgp_neighbor_removal",
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


def test_accepted_live_neighbor_removal_incident(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_id = unique_run_id("it-nbr-removal")
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
        binding=BGP_NEIGHBOR_REMOVAL_BINDING,
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
    assert record.scenario.template_id == "bgp_neighbor_removal"
    assert record.ground_truth is not None
    assert record.ground_truth.root_cause_label == "bgp_neighbor_removal"
    assert record.rejection is None
    assert record.restoration is not None and record.restoration.completed
    assert record.restoration.forced_reset_used is True
    assert record.fault is not None
    assert record.fault.parameter_name == "neighbor"
    assert record.fault.before_value == f"{PEER_IP} remote-as 65002"
    assert record.fault.after_value == "removed"
    assert loaded.ledger[-1].phase is LifecyclePhase.RECOVERY_VERIFIED

    # -- deterministic verdict content ----------------------------------------
    verdicts = {v.check_id: v for v in record.ground_truth.verdicts}
    absent = [v for k, v in verdicts.items() if k.startswith("bgp_peer_absent")]
    assert absent and absent[0].observed == ("false",)  # AFFIRMATIVE absence
    # byte-identical config recovery is a persisted, committable verdict
    config_verdicts = [
        v for k, v in verdicts.items()
        if k.startswith("config_unchanged:router_a") and v.phase == "recovery"
    ]
    assert config_verdicts and config_verdicts[0].committable

    # independent evidence-level proof of the same equality, both nodes:
    assert record.onset_evidence is not None and record.recovery_evidence is not None
    assert _config_sha(record.baseline_evidence, "router_a") == _config_sha(
        record.recovery_evidence, "router_a"
    )

    # deterministic id + canonical round-trip
    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)

    # mutation transcript: remove/restore/clear pairs; router_b never mutated
    muts = [e for e in loaded.transcript if e.mode == "mutation"]
    pending = [e for e in muts if e.stage == "pending"]
    completed = [e for e in muts if e.stage == "completed"]
    assert len(pending) == len(completed) == 3
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
    assert assembled.index_entry.template_id == "bgp_neighbor_removal"
    reloaded = load_verified_run_from_index(out_root, assembled.run_id)
    assert reloaded.run_digest == assembled.run_digest
    assert reloaded.incident == record
