"""Shared deterministic test fixtures. No wall clocks, no randomness, no services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.schemas import (
    ScenarioDefinition,
    ScenarioTimeouts,
    TopologySpec,
)

EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class FakeClock:
    """Deterministic, manually-advanced clock."""

    def __init__(self, start: datetime = EPOCH) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self._now.timestamp()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def run_ctx(fake_clock: FakeClock) -> RunContext:
    return RunContext("run-test-0001", clock=fake_clock)


def make_two_router_topology() -> TopologySpec:
    # Delegates to the canonical factory (single source of the approved values).
    return two_router_frr_topology()


def make_scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        family="bgp",
        template_id="bgp_remote_as_mismatch",
        version=1,
        parameters={"wrong_asn": 65999, "target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0,
            onset_s=30.0,
            recovery_s=60.0,
            command_s=10.0,
            poll_interval_s=0.5,
        ),
    )


@pytest.fixture
def two_router_topology() -> TopologySpec:
    return make_two_router_topology()


@pytest.fixture
def scenario() -> ScenarioDefinition:
    return make_scenario()


ClockFn = Callable[[], datetime]


# --------------------------------------------------------------------------
# Synthetic run inputs for artifact tests (Gate 4 Step 5). Deterministic,
# offline: fixed clock, no lab, no Docker. Reused across artifact test tiers.
# --------------------------------------------------------------------------

import json as _json  # noqa: E402
from dataclasses import dataclass  # noqa: E402


@dataclass(frozen=True)
class RunInputs:
    run_manifest: object
    environment_manifest: object
    incident: object
    transcript_entries: tuple
    ledger_records: tuple


def _evidence_bundle(rc: RunContext, phase: object, target: str, normalized: dict) -> object:
    from verifiednet.common.hashing import sha256_bytes
    from verifiednet.schemas import EvidenceBundle, EvidenceRecord, EvidenceSource

    payload = _json.dumps(normalized, sort_keys=True)
    seq = rc.next_seq()
    record = EvidenceRecord(
        evidence_id=rc.content_id("ev", {"phase": str(phase), "target": target, "seq": seq}),
        phase=phase,
        source=EvidenceSource(collector="fake.collector", target=target, trusted=True),
        raw_sha256=sha256_bytes(payload.encode("utf-8")),
        raw_payload=payload,
        normalized=normalized,
        captured_at=EPOCH,
        run_seq=seq,
    )
    return EvidenceBundle(
        bundle_id=rc.content_id("bundle", {"phase": str(phase), "t": target}),
        phase=phase,
        records=(record,),
    ).seal()


def _env_manifest() -> object:
    from verifiednet.schemas import EnvironmentManifest

    return EnvironmentManifest(
        os_name="Darwin", kernel="25.5.0", arch="arm64", python_version="3.12.12",
        container_runtime="docker", container_runtime_version="29.1.3",
        image_reference="frrouting/frr:v8.4.1@sha256:" + "c" * 64,
        image_manifest_digest="sha256:" + "c" * 64, frr_version="8.4.1_git", captured_at=EPOCH,
    )


def build_accepted_inputs(run_id: str = "run-test-acc1") -> RunInputs:
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.faults.ledger import Ledger, LifecyclePhase
    from verifiednet.incidents.builder import build_accepted_record
    from verifiednet.incidents.oracle import build_ground_truth
    from verifiednet.runtime.invocation import CommandInvocation
    from verifiednet.runtime.transcript import TranscriptEntry
    from verifiednet.schemas import (
        Phase,
        ProvenanceInfo,
        RestorationMetadata,
        RunManifest,
        Verdict,
        VerificationResult,
    )
    from verifiednet.schemas.fault import FaultInjection

    rc = RunContext(run_id, clock=lambda: EPOCH)
    topo = make_two_router_topology()
    scen = make_scenario()
    up = {"bgp.peer.172.30.0.2.state": "Established"}
    down = {"bgp.peer.172.30.0.2.state": "Idle"}
    baseline = _evidence_bundle(rc, Phase.BASELINE, "router_a", up)
    onset = _evidence_bundle(rc, Phase.ONSET, "router_a", down)
    recovery = _evidence_bundle(rc, Phase.RECOVERY, "router_a", up)
    fault = FaultInjection(
        scenario_id=scen.scenario_id, template_id=scen.template_id, target_node="router_a",
        target_session="a-b", method="vtysh-remote-as", parameter_name="remote_as",
        before_value="65002", after_value="65999", transcript_refs=(2,),
        injected_at_seq=rc.next_seq(), injected_at=EPOCH,
    )
    vr = VerificationResult(
        check_id="bgp_not_established:router_a:x:onset", verdict=Verdict.PASS, phase="onset",
        evidence_ids=("ev-transient",), observed=("Idle",), evaluated_at_seq=rc.next_seq(),
        evaluated_at=EPOCH,
    )
    gt = build_ground_truth(
        fault=fault, verdicts=(vr,), accepted_evidence_ids=onset.evidence_ids,
        root_cause_label="bgp_remote_as_mismatch",
    )
    prov = ProvenanceInfo(generator="g", generator_version="0.1.0", code_commit="deadbeef")
    incident = build_accepted_record(
        run_ctx=rc, scenario=scen, topology=topo, fault=fault, ground_truth=gt,
        baseline=baseline, onset=onset, recovery=recovery, precondition_results=(vr,),
        onset_results=(vr,), recovery_results=(vr,),
        restoration=RestorationMetadata(method="m", forced_reset_used=True,
            forced_reset_command="clear bgp 172.30.0.2", transcript_refs=(3, 4), completed=True),
        provenance=prov,
        completed_phases=("precondition", "inject", "onset", "restore", "recovery"),
        cleanup_status="clean",
    )
    inv = CommandInvocation(
        command_id="cmd-000000000000abcd", target="router_a",
        logical_argv=("vtysh", "-c", "configure terminal"),
        transport_argv=(
            "docker", "compose", "exec", "-T", "router_a", "vtysh", "-c", "configure terminal",
        ),
    )
    transcript = (
        TranscriptEntry(seq=1, mode="read", stage="completed", target="router_a",
            argv=("vtysh", "-c", "show version"), status="ok", started_at=EPOCH),
        TranscriptEntry(seq=2, mode="mutation", stage="pending", target="router_a",
            argv=inv.transport_argv, status="pending", started_at=EPOCH, invocation=inv),
        TranscriptEntry(seq=2, mode="mutation", stage="completed", target="router_a",
            argv=inv.transport_argv, status="ok", started_at=EPOCH, invocation=inv),
    )
    led = Ledger(rc)
    for ph in (LifecyclePhase.PRECHECKED, LifecyclePhase.INJECTING, LifecyclePhase.INJECTED,
               LifecyclePhase.ONSET_VERIFIED, LifecyclePhase.RESTORING, LifecyclePhase.RESTORED,
               LifecyclePhase.RECOVERY_VERIFIED):
        led.append(ph, "")
    rm = RunManifest(
        run_id=run_id, git_rev="deadbeef", lock_hash="b" * 64, scenario_id=scen.scenario_id,
        template_id=scen.template_id, topology_hash=sha256_canonical(topo), started_at=EPOCH,
        acceptance_status="accepted",
    )
    return RunInputs(rm, _env_manifest(), incident, transcript, led.records)


def build_rejected_inputs(run_id: str = "run-test-rej1") -> RunInputs:
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.incidents.builder import build_rejected_record
    from verifiednet.schemas import (
        Phase,
        ProvenanceInfo,
        RejectionCode,
        RunManifest,
        Verdict,
        VerificationResult,
    )

    rc = RunContext(run_id, clock=lambda: EPOCH)
    topo = make_two_router_topology()
    scen = make_scenario()
    baseline = _evidence_bundle(
        rc, Phase.PRECONDITION, "router_a", {"route.203.0.113.99/32.present": "false"}
    )
    ev_id = baseline.records[0].evidence_id
    vr = VerificationResult(
        check_id="route_present:router_a:route.203.0.113.99/32.present:precondition",
        verdict=Verdict.FAIL, phase="precondition", evidence_ids=(ev_id,), observed=("false",),
        evaluated_at_seq=rc.next_seq(), evaluated_at=EPOCH,
    )
    prov = ProvenanceInfo(generator="g", generator_version="0.1.0", code_commit="deadbeef")
    incident = build_rejected_record(
        run_ctx=rc, scenario=scen, topology=topo, baseline=baseline,
        rejection_code=RejectionCode.PRECONDITION_FAILED,
        details="required route 203.0.113.99/32 was absent on router_a",
        failed_phase="precondition", precondition_results=(vr,), provenance=prov,
        completed_phases=(), cleanup_status="clean",
    )
    rm = RunManifest(
        run_id=run_id, git_rev="deadbeef", lock_hash="b" * 64, scenario_id=scen.scenario_id,
        template_id=scen.template_id, topology_hash=sha256_canonical(topo), started_at=EPOCH,
        acceptance_status="rejected",
    )
    return RunInputs(rm, _env_manifest(), incident, (), ())


@pytest.fixture
def accepted_run_inputs() -> RunInputs:
    return build_accepted_inputs()


@pytest.fixture
def rejected_run_inputs() -> RunInputs:
    return build_rejected_inputs()


@pytest.fixture
def make_accepted_inputs() -> Callable[[str], RunInputs]:
    return build_accepted_inputs


@pytest.fixture
def make_rejected_inputs() -> Callable[[str], RunInputs]:
    return build_rejected_inputs


@pytest.fixture
def make_live_manifests() -> Callable[..., tuple]:
    """Build (RunManifest, EnvironmentManifest) from a live backend + run context."""
    import re
    from pathlib import Path as _Path

    from verifiednet.common.hashing import sha256_canonical, sha256_file
    from verifiednet.runtime.process import default_runner
    from verifiednet.schemas import EnvironmentManifest, RunManifest

    def _make(backend: object, run_ctx: RunContext, scenario: object, *, status: str) -> tuple:
        topo = backend.topology()  # type: ignore[attr-defined]
        meta = backend.capture_environment_metadata()  # type: ignore[attr-defined]
        vr = backend.execute_readonly(  # type: ignore[attr-defined]
            topo.nodes[0].name, ["vtysh", "-c", "show version"], 10.0
        )
        match = re.search(r"FRRouting (\S+)", vr.stdout)
        rev = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip()
        commit = rev or "unknown"
        lock = _Path("uv.lock")
        lock_hash = sha256_file(lock) if lock.is_file() else "0" * 64
        env = EnvironmentManifest(
            os_name=meta["os_name"], kernel=meta["kernel"], arch=meta["arch"],
            python_version=meta["python_version"], container_runtime=meta["container_runtime"],
            container_runtime_version=meta.get("container_runtime_version", ""),
            image_reference=meta["image_reference"],
            image_manifest_digest=meta.get("image_manifest_digest"),
            platform_resolved_digest=meta.get("platform_resolved_repo_digest"),
            frr_version=match.group(1) if match else None, captured_at=run_ctx.now(),
        )
        rm = RunManifest(
            run_id=run_ctx.run_id, git_rev=commit, lock_hash=lock_hash,
            scenario_id=scenario.scenario_id, template_id=scenario.template_id,  # type: ignore[attr-defined]
            topology_hash=sha256_canonical(topo),
            image_digests={"frr": topo.images.frr}, started_at=run_ctx.now(),
            acceptance_status=status,  # type: ignore[arg-type]
        )
        return rm, env

    return _make


@pytest.fixture
def write_inputs() -> Callable[..., object]:
    """Return a helper that writes a RunInputs to a directory and returns WrittenRun."""
    from verifiednet.artifacts import write_run_artifacts

    def _write(inputs: RunInputs, out_root: object) -> object:
        return write_run_artifacts(
            out_root=out_root,  # type: ignore[arg-type]
            run_manifest=inputs.run_manifest,  # type: ignore[arg-type]
            environment_manifest=inputs.environment_manifest,  # type: ignore[arg-type]
            incident=inputs.incident,  # type: ignore[arg-type]
            transcript_entries=inputs.transcript_entries,
            ledger_records=inputs.ledger_records,
        )

    return _write
