"""Live deliberately-rejected incident on a HEALTHY two-router lab (Gate 4 Step 4).

The rejection happens entirely inside precondition validation, BEFORE any
mutation: an impossible RFC 5737 route is required, the existing route collector
reports it absent, the existing verifier returns FAIL, one rejected
IncidentRecord is built, and the lab is proven still healthy afterwards. Ledger
stays PENDING; zero mutation entries; teardown in finally with independent
zero-resource checks.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.incidents.manifests import incident_to_json_bytes
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.convergence import wait_for_bgp_established
from verifiednet.labs.frr.rejected_scenario import (
    DEFAULT_IMPOSSIBLE_PREFIX,
    RejectedPreconditionRun,
)
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import IncidentRecord, ProvenanceInfo, ScenarioDefinition, ScenarioTimeouts
from verifiednet.schemas.evidence import EvidenceBundle, Phase
from verifiednet.verifiers.claims import ClaimVerifier

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


def _metric(bundle: EvidenceBundle, target: str, key: str) -> str:
    for record in bundle.records:
        if record.source.target == target and key in record.normalized:
            return str(record.normalized[key])
    raise AssertionError(f"no {key} for {target}")


def test_precondition_rejected_incident(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_ctx = RunContext(unique_run_id("it-rejected"))
    commit = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip() or "unknown"
    backend = FrrComposeBackend(topology, run_ctx, work_dir=tmp_path)
    project = backend.project_name
    ledger = Ledger(run_ctx)

    record: IncidentRecord | None = None
    try:
        t0 = time.monotonic()
        backend.start()
        conv = wait_for_bgp_established(backend.readonly_executor, topology)
        assert conv.converged
        convergence_s = conv.elapsed_s

        rejected = RejectedPreconditionRun(
            executor=backend.readonly_executor,
            topology=topology,
            scenario=_scenario(),
            run_ctx=run_ctx,
            ledger=ledger,
            verifier=ClaimVerifier(run_ctx),
            target_node="router_a",
            peer_node="router_b",
        )
        record = rejected.execute(
            provenance=ProvenanceInfo(
                generator="verifiednet.labs.frr.rejected_scenario",
                generator_version="0.1.0",
                code_commit=commit,
            )
        )
        rejected_s = time.monotonic() - t0

        # ledger never left PENDING; zero mutation on the wire
        assert ledger.current is LifecyclePhase.PENDING
        muts = [e for e in backend.transcript.entries if e.mode == "mutation"]  # type: ignore[attr-defined]
        assert muts == []

        # the failed check observed the impossible route as absent
        pre = record.precondition_results
        assert len(pre) == 1 and not pre[0].committable
        assert pre[0].observed == ("false",)
        failed_check_id = pre[0].check_id

        # the lab is STILL healthy after the rejected run
        after = rejected._provider(Phase.PRECONDITION)[0]  # type: ignore[attr-defined]
        assert _metric(after, "router_a", "bgp.peer.172.30.0.2.state") == "Established"
        assert _metric(after, "router_b", "bgp.peer.172.30.0.1.state") == "Established"
        assert _metric(after, "router_a", "route.10.255.0.2/32.present") == "true"
        assert _metric(after, "router_b", "route.10.255.0.1/32.present") == "true"
        assert _metric(after, "router_a", "ping.172.30.0.2.all_success") == "true"
        # configs unchanged vs the baseline captured during the rejected run
        baseline = record.baseline_evidence
        assert _metric(baseline, "router_a", "config.sha256") == _metric(
            after, "router_a", "config.sha256"
        )
        assert _metric(baseline, "router_b", "config.sha256") == _metric(
            after, "router_b", "config.sha256"
        )
    finally:
        backend.stop()

    # independent host-side zero-resource proof
    assert project_containers(project) == []
    assert project_networks(project) == []

    # rejected record contents + validation
    assert record is not None
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
    assert failed_check_id.startswith("route_present:router_a:")
    assert convergence_s > 0.0 and rejected_s > 0.0

    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)

    out = tmp_path / "rejected_incident.json"
    out.write_bytes(incident_to_json_bytes(record))
    assert json.loads(out.read_text())["status"] == "rejected"
