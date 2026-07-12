"""Live accepted BGP remote-AS-mismatch incident — the Gate 4 Step 3 slice.

Drives ONE real incident end to end against the live two-router lab:
healthy convergence -> preconditions -> inject (wrong-AS on router_a) ->
onset verification -> restore -> recovery verification -> GroundTruth ->
accepted IncidentRecord -> verified cleanup. Nested try/finally guarantees
restoration (if injected) and teardown even on failure. No mutation ever
targets router_b (runtime TargetPolicy), and no accepted record is built unless
the ledger reaches RECOVERY_VERIFIED with every verdict committable.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.faults.bgp_remote_as_mismatch import BgpRemoteAsMismatchScenario
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.incidents.builder import build_accepted_record
from verifiednet.incidents.manifests import incident_to_json_bytes
from verifiednet.incidents.oracle import ORACLE_VERSION, build_ground_truth
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.convergence import wait_for_bgp_established
from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.runtime.policy import bgp_remote_as_mutation_shapes
from verifiednet.runtime.process import default_runner
from verifiednet.schemas import IncidentRecord, ProvenanceInfo, ScenarioDefinition, ScenarioTimeouts
from verifiednet.schemas.evidence import EvidenceBundle, Phase
from verifiednet.verifiers.claims import ClaimVerifier

pytestmark = pytest.mark.integration

PEER_IP = "172.30.0.2"
ROOT_CAUSE = "bgp_remote_as_mismatch"


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


def _config_sha(bundle: EvidenceBundle, target: str) -> str:
    for record in bundle.records:
        if record.source.target == target and "config.sha256" in record.normalized:
            return str(record.normalized["config.sha256"])
    raise AssertionError(f"no config.sha256 for {target} in bundle")


def _route_present(bundle: EvidenceBundle, target: str, prefix: str) -> str:
    key = f"route.{prefix}.present"
    for record in bundle.records:
        if record.source.target == target and key in record.normalized:
            return str(record.normalized[key])
    raise AssertionError(f"no {key} for {target}")


def test_accepted_live_remote_as_incident(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    scenario_def = _scenario()
    run_ctx = RunContext(unique_run_id("it-incident"))
    commit = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip() or "unknown"
    backend = FrrComposeBackend(topology, run_ctx, work_dir=tmp_path)
    project = backend.project_name

    provider = LiveScenarioEvidenceProvider(
        executor=backend.readonly_executor,
        topology=topology,
        run_ctx=run_ctx,
        target_node="router_a",
        peer_node="router_b",
    )
    mutation = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_remote_as_mutation_shapes()
    )
    ledger = Ledger(run_ctx)
    scenario = BgpRemoteAsMismatchScenario(
        topology=topology,
        scenario=scenario_def,
        mutation=mutation,
        ledger=ledger,
        run_ctx=run_ctx,
        evidence_provider=provider,
        verifier=ClaimVerifier(run_ctx),
        monotonic=time.monotonic,
        sleep=time.sleep,
    )

    record: IncidentRecord | None = None
    try:
        backend.start()
        healthy = wait_for_bgp_established(backend.readonly_executor, topology)
        assert healthy.converged

        baseline_bundle = provider(Phase.BASELINE)[0]
        try:
            pre_results = scenario.validate_preconditions()
            assert ledger.current is LifecyclePhase.PRECHECKED

            fault = scenario.inject()
            assert ledger.current is LifecyclePhase.INJECTED
            assert (fault.before_value, fault.after_value) == ("65002", "65999")
            assert fault.target_node == "router_a"

            onset_results = scenario.verify_onset()
            assert ledger.current is LifecyclePhase.ONSET_VERIFIED
            onset_bundle = provider(Phase.ONSET)[0]
            # wrong-AS observed live; peer loopback route withdrawn on router_a
            assert _route_present(onset_bundle, "router_a", "10.255.0.2/32") == "false"

            restoration = scenario.restore()
            assert ledger.current is LifecyclePhase.RESTORED
            recovery_results = scenario.verify_recovery()
            assert ledger.current is LifecyclePhase.RECOVERY_VERIFIED
            recovery_bundle = provider(Phase.RECOVERY)[0]
        finally:
            # If we injected and did not reach a verified recovery, restore now.
            if ledger.current in (
                LifecyclePhase.INJECTING,
                LifecyclePhase.INJECTED,
                LifecyclePhase.ONSET_VERIFIED,
            ):
                scenario.restore()

        # -- acceptance gate: only build when fully verified & committable ----
        all_results = (*pre_results, *onset_results, *recovery_results)
        assert ledger.current is LifecyclePhase.RECOVERY_VERIFIED
        assert all(r.committable for r in all_results), [
            (r.check_id, r.verdict) for r in all_results if not r.committable
        ]

        # peer config unchanged; target config restored to baseline-equivalent
        assert _config_sha(baseline_bundle, "router_b") == _config_sha(recovery_bundle, "router_b")
        assert _config_sha(baseline_bundle, "router_a") == _config_sha(recovery_bundle, "router_a")
        # both loopback routes restored, reachability healthy at recovery
        assert _route_present(recovery_bundle, "router_a", "10.255.0.2/32") == "true"
        assert _route_present(recovery_bundle, "router_b", "10.255.0.1/32") == "true"

        ground_truth = build_ground_truth(
            fault=fault,
            verdicts=(*onset_results, *recovery_results),
            accepted_evidence_ids=(*onset_bundle.evidence_ids, *recovery_bundle.evidence_ids),
            root_cause_label=ROOT_CAUSE,
        )
        assert ground_truth.oracle_version == ORACLE_VERSION
        collected_ids = {
            *baseline_bundle.evidence_ids,
            *onset_bundle.evidence_ids,
            *recovery_bundle.evidence_ids,
        }
        assert set(ground_truth.accepted_evidence_ids) <= collected_ids

        record = build_accepted_record(
            run_ctx=run_ctx,
            scenario=scenario_def,
            topology=topology,
            fault=fault,
            ground_truth=ground_truth,
            baseline=baseline_bundle,
            onset=onset_bundle,
            recovery=recovery_bundle,
            precondition_results=pre_results,
            onset_results=onset_results,
            recovery_results=recovery_results,
            restoration=restoration,
            provenance=ProvenanceInfo(
                generator="verifiednet.faults.bgp_remote_as_mismatch",
                generator_version="0.1.0",
                code_commit=commit,
            ),
            completed_phases=("precondition", "inject", "onset", "restore", "recovery"),
            cleanup_status="clean",
        )
    finally:
        backend.stop()

    # -- teardown proof (independent host-side) -------------------------------
    assert project_containers(project) == []
    assert project_networks(project) == []

    # -- accepted record validation ------------------------------------------
    assert record is not None
    assert record.status == "accepted"
    assert record.ground_truth is not None
    assert record.rejection is None
    assert record.restoration is not None and record.restoration.completed
    assert record.restoration.forced_reset_used is True
    assert record.baseline_evidence.sealed and record.onset_evidence is not None

    # deterministic id + canonical round-trip
    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert record.incident_id.startswith("inc-")
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)

    # mutation transcript fully paired; router_b never mutated
    entries = [e for e in backend.transcript.entries if e.mode == "mutation"]  # type: ignore[attr-defined]
    pending = [e for e in entries if e.stage == "pending"]
    completed = [e for e in entries if e.stage == "completed"]
    assert len(pending) == len(completed) == 3  # inject, restore, clear
    assert {e.invocation.command_id for e in pending if e.invocation} == {
        e.invocation.command_id for e in completed if e.invocation
    }
    assert all(e.target == "router_a" for e in entries)

    # write the accepted record to a temporary Gate 4 output (not a canonical dir)
    out = tmp_path / "accepted_incident.json"
    out.write_bytes(incident_to_json_bytes(record))
    assert json.loads(out.read_text())["status"] == "accepted"
