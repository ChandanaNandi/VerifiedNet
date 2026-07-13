"""Live deliberately-rejected incident on a HEALTHY lab, through the composition root.

The rejection happens entirely inside precondition validation, BEFORE any
mutation. Driven via the PRODUCTION entry point
``run_precondition_rejected_incident``: an impossible RFC 5737 route is required,
the existing collector reports it absent, the existing verifier returns FAIL, one
rejected IncidentRecord is built, persisted, and indexed, and the lab is proven
still healthy afterwards. Ledger stays PENDING; zero mutation entries; teardown
owned by the composition root with an independent host-side zero-resource proof.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.artifacts import load_verified_run_from_index, verify_run_index
from verifiednet.common.hashing import sha256_file
from verifiednet.common.runctx import RunContext
from verifiednet.incidents.manifests import incident_to_json_bytes
from verifiednet.labs.frr.compose_project import project_name_for_run
from verifiednet.labs.frr.rejected_scenario import DEFAULT_IMPOSSIBLE_PREFIX
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.orchestrator import run_precondition_rejected_incident
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import IncidentRecord, ScenarioDefinition, ScenarioTimeouts

pytestmark = pytest.mark.integration

IMPOSSIBLE = DEFAULT_IMPOSSIBLE_PREFIX


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


def test_precondition_rejected_incident(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_id = unique_run_id("it-rejected")
    project = project_name_for_run(run_id)
    commit = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip() or "unknown"
    lock = Path("uv.lock")
    lock_hash = sha256_file(lock) if lock.is_file() else "0" * 64
    out_root = tmp_path / "runs"

    result = run_precondition_rejected_incident(
        out_root=out_root,
        work_dir=tmp_path / "lab",
        run_ctx=RunContext(run_id),
        topology=topology,
        scenario=_scenario(),
        git_rev=commit,
        lock_hash=lock_hash,
        convergence_timeout_s=60.0,
    )

    # independent host-side zero-resource proof
    assert project_containers(project) == []
    assert project_networks(project) == []

    assert result.convergence.converged
    assembled = result.assembled
    loaded = assembled.loaded
    record = loaded.incident

    # rejected record contents + validation
    assert record.status == "rejected"
    assert record.rejection is not None
    assert record.rejection.code.value == "precondition_failed"
    assert record.rejection.failed_phase == "precondition"
    assert IMPOSSIBLE in record.rejection.details
    assert record.ground_truth is None
    assert record.fault is None
    assert record.restoration is None
    assert record.onset_evidence is None and record.recovery_evidence is None
    assert record.completed_phases == ()
    assert record.baseline_evidence.sealed
    assert record.cleanup_status == "clean"

    pre = record.precondition_results
    assert len(pre) == 1 and not pre[0].committable
    assert pre[0].observed == ("false",)
    assert pre[0].check_id.startswith("route_present:router_a:")

    # ledger never left PENDING (no records) and zero mutation persisted
    assert loaded.ledger == ()
    assert [e for e in loaded.transcript if e.mode == "mutation"] == []
    assert {r.value for r in loaded.evidence} == {"evidence_baseline"}  # baseline only

    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)

    # -- run index: discoverable + reload-through-index round trip ------------
    assert verify_run_index(out_root).verified
    assert assembled.index_entry.acceptance_status == "rejected"
    reloaded = load_verified_run_from_index(out_root, assembled.run_id)
    assert reloaded.run_digest == assembled.run_digest
    assert reloaded.incident == record
