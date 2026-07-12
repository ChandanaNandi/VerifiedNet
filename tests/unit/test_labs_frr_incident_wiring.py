"""Offline proof of the Gate 4 live-incident wiring (no Docker).

A single ``LabSim`` process runner answers read commands from shared state and
applies mutation commands to it. Built into a real ``FrrComposeBackend`` (never
started — construction touches no Docker), it drives the REAL
``BgpRemoteAsMismatchScenario`` through the REAL ``MutationExecutor`` (via
``build_mutation_adapter``) and the REAL ``LiveScenarioEvidenceProvider`` over
one shared transcript — the whole accepted vertical slice, deterministically,
offline.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.faults.bgp_remote_as_mismatch import BgpRemoteAsMismatchScenario
from verifiednet.faults.frr_commands import set_remote_as_argv
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.incidents.builder import build_accepted_record
from verifiednet.incidents.oracle import build_ground_truth
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.runtime.policy import bgp_remote_as_mutation_shapes
from verifiednet.runtime.process import RawResult
from verifiednet.runtime.results import ExecStatus
from verifiednet.schemas import ProvenanceInfo, ScenarioDefinition
from verifiednet.schemas.evidence import Phase
from verifiednet.verifiers.claims import ClaimVerifier

pytestmark = pytest.mark.unit

CORRECT_AS = 65002
WRONG_AS = 65999
PEER_IP = "172.30.0.2"


def scenario_def() -> ScenarioDefinition:
    from verifiednet.schemas import ScenarioTimeouts

    return ScenarioDefinition(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        family="bgp",
        template_id="bgp_remote_as_mismatch",
        version=1,
        parameters={"wrong_asn": WRONG_AS, "target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0, command_s=10.0, poll_interval_s=0.5
        ),
    )


class LabSim:
    """Deterministic FRR lab: reads reflect shared state; mutations change it."""

    def __init__(self) -> None:
        # router_a's configured remote-as for peer 172.30.0.2 (the only mutable).
        self.a_remote_as = CORRECT_AS
        self.mutation_targets: list[str] = []
        self.reads: list[tuple[str, tuple[str, ...]]] = []

    @property
    def session_up(self) -> bool:
        return self.a_remote_as == CORRECT_AS

    def _bgp_summary(self, service: str) -> str:
        if service == "router_a":
            peers = {
                PEER_IP: {
                    "state": "Established" if self.session_up else "Idle",
                    "remoteAs": self.a_remote_as,
                }
            }
            local = 65001
        else:
            peers = {
                "172.30.0.1": {
                    "state": "Established" if self.session_up else "Idle",
                    "remoteAs": 65001,
                }
            }
            local = 65002
        return json.dumps({"ipv4Unicast": {"as": local, "peers": peers}})

    def _interfaces(self) -> str:
        return json.dumps(
            {
                "eth1": {"administrativeStatus": "up", "operationalStatus": "up"},
                "lo": {"administrativeStatus": "up", "operationalStatus": "up"},
            }
        )

    def _routes(self, service: str) -> str:
        table: dict[str, list[dict[str, object]]] = {}
        if service == "router_a":
            table["10.255.0.1/32"] = [{"protocol": "connected"}]
            if self.session_up:
                table["10.255.0.2/32"] = [{"protocol": "bgp"}]
        else:
            table["10.255.0.2/32"] = [{"protocol": "connected"}]
            if self.session_up:
                table["10.255.0.1/32"] = [{"protocol": "bgp"}]
        return json.dumps(table)

    def _running_config(self, service: str) -> str:
        if service == "router_a":
            return (
                "frr version 8.4.1_git\nhostname router_a\nrouter bgp 65001\n"
                f" neighbor {PEER_IP} remote-as {self.a_remote_as}\n"
            )
        return (
            "frr version 8.4.1_git\nhostname router_b\nrouter bgp 65002\n"
            " neighbor 172.30.0.1 remote-as 65001\n"
        )

    @staticmethod
    def _vtysh_commands(logical: list[str]) -> list[str]:
        return [logical[i + 1] for i in range(len(logical)) if logical[i] == "-c"]

    def __call__(self, argv: Sequence[str], timeout_s: float, max_output_bytes: int) -> RawResult:
        a = list(argv)
        exec_idx = a.index("exec")
        service = a[exec_idx + 2]  # after "-T"
        logical = a[exec_idx + 3 :]
        binary = logical[0]
        if binary == "ping":
            return RawResult(0, "1 packets transmitted, 1 received", "", False, False, False)
        commands = self._vtysh_commands(logical)
        first = commands[0] if commands else ""
        if first.startswith("show"):
            self.reads.append((service, tuple(logical)))
            if first == "show ip bgp summary json":
                return RawResult(0, self._bgp_summary(service), "", False, False, False)
            if first == "show interface json":
                return RawResult(0, self._interfaces(), "", False, False, False)
            if first == "show ip route json":
                return RawResult(0, self._routes(service), "", False, False, False)
            if first == "show running-config":
                return RawResult(0, self._running_config(service), "", False, False, False)
            raise AssertionError(f"unexpected show: {first!r}")
        # mutation
        self.mutation_targets.append(service)
        for cmd in commands:
            if cmd.startswith("neighbor") and "remote-as" in cmd:
                self.a_remote_as = int(cmd.split()[-1])
        return RawResult(0, "", "", False, False, False)


def make_backend(sim: LabSim, run_ctx: RunContext, tmp_path: Path) -> FrrComposeBackend:
    return FrrComposeBackend(
        two_router_frr_topology(),
        run_ctx,
        work_dir=tmp_path,
        runner=sim,
    )


# --- mutation adapter: transport, policy, write-ahead pairing ----------------


def test_build_mutation_adapter_transport_and_write_ahead(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    sim = LabSim()
    backend = make_backend(sim, run_ctx, tmp_path)
    adapter = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_remote_as_mutation_shapes()
    )
    result = adapter.run("router_a", set_remote_as_argv(65001, PEER_IP, WRONG_AS), 10.0)

    assert result.status is ExecStatus.OK
    # transport is compose exec -T router_a <logical>
    assert result.invocation is not None
    transport = result.invocation.transport_argv
    assert transport[:2] == ("docker", "compose")
    assert "exec" in transport and "-T" in transport
    assert transport[transport.index("-T") + 1] == "router_a"
    assert result.invocation.logical_argv == set_remote_as_argv(65001, PEER_IP, WRONG_AS)

    # write-ahead: pending BEFORE completed, both sharing one command_id
    entries = backend.transcript.entries  # type: ignore[attr-defined]
    stages = [(e.stage, e.status) for e in entries]
    assert stages == [("pending", "pending"), ("completed", "ok")]
    ids = {e.invocation.command_id for e in entries if e.invocation}
    assert len(ids) == 1
    assert sim.a_remote_as == WRONG_AS  # mutation actually applied


def test_mutation_adapter_denies_router_b(tmp_path: Path, run_ctx: RunContext) -> None:
    sim = LabSim()
    backend = make_backend(sim, run_ctx, tmp_path)
    adapter = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_remote_as_mutation_shapes()
    )
    result = adapter.run("router_b", set_remote_as_argv(65002, "172.30.0.1", WRONG_AS), 10.0)
    assert result.status is ExecStatus.DENIED_TARGET
    assert sim.mutation_targets == []  # nothing executed on the wire


def test_mutation_adapter_rejects_unapproved_shape(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    sim = LabSim()
    backend = make_backend(sim, run_ctx, tmp_path)
    adapter = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_remote_as_mutation_shapes()
    )
    # a shutdown-style command is not one of the two approved shapes
    bad = ("vtysh", "-c", "configure terminal", "-c", "router bgp 65001", "-c", "shutdown")
    result = adapter.run("router_a", bad, 10.0)
    assert result.status is ExecStatus.DENIED_COMMAND
    assert sim.mutation_targets == []


# --- evidence provider shape (target-blind verifier safety) ------------------


def build_provider(
    backend: FrrComposeBackend, run_ctx: RunContext
) -> LiveScenarioEvidenceProvider:
    return LiveScenarioEvidenceProvider(
        executor=backend.readonly_executor,
        topology=two_router_frr_topology(),
        run_ctx=run_ctx,
        target_node="router_a",
        peer_node="router_b",
    )


def test_onset_bundle_has_peer_config_only(tmp_path: Path, run_ctx: RunContext) -> None:
    sim = LabSim()
    sim.a_remote_as = WRONG_AS  # session down
    backend = make_backend(sim, run_ctx, tmp_path)
    (bundle,) = build_provider(backend, run_ctx)(Phase.ONSET)
    config_targets = {
        r.source.target for r in bundle.records if "config.sha256" in r.normalized
    }
    # config hash present for the PEER only (so config_unchanged is unambiguous)
    assert config_targets == {"router_b"}
    assert bundle.sealed


# --- capstone: full accepted slice offline -----------------------------------


def drive_full_lifecycle(
    tmp_path: Path, run_ctx: RunContext
) -> tuple[
    BgpRemoteAsMismatchScenario, Ledger, LabSim, LiveScenarioEvidenceProvider, FrrComposeBackend
]:
    sim = LabSim()
    backend = make_backend(sim, run_ctx, tmp_path)
    provider = build_provider(backend, run_ctx)
    mutation = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_remote_as_mutation_shapes()
    )
    ledger = Ledger(run_ctx)

    class Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def monotonic(self) -> float:
            return self.t

        def sleep(self, s: float) -> None:
            self.t += s

    clock = Clock()
    scenario = BgpRemoteAsMismatchScenario(
        topology=two_router_frr_topology(),
        scenario=scenario_def(),
        mutation=mutation,
        ledger=ledger,
        run_ctx=run_ctx,
        evidence_provider=provider,
        verifier=ClaimVerifier(run_ctx),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return scenario, ledger, sim, provider, backend


def test_full_accepted_slice_offline(tmp_path: Path, run_ctx: RunContext) -> None:
    scenario, ledger, sim, provider, backend = drive_full_lifecycle(tmp_path, run_ctx)

    pre = scenario.validate_preconditions()
    fault = scenario.inject()
    onset = scenario.verify_onset()
    restoration = scenario.restore()
    recovery = scenario.verify_recovery()

    # ledger walked the full legal sequence
    assert [r.phase for r in ledger.records] == [
        LifecyclePhase.PRECHECKED,
        LifecyclePhase.INJECTING,
        LifecyclePhase.INJECTED,
        LifecyclePhase.ONSET_VERIFIED,
        LifecyclePhase.RESTORING,
        LifecyclePhase.RESTORED,
        LifecyclePhase.RECOVERY_VERIFIED,
    ]
    # only router_a was ever mutated
    assert set(sim.mutation_targets) == {"router_a"}
    # exact before/after
    assert (fault.before_value, fault.after_value) == ("65002", "65999")
    # state returned to correct AS after recovery
    assert sim.a_remote_as == CORRECT_AS
    assert restoration.forced_reset_used is True

    # accepted record assembles + round-trips
    baseline_provider = provider(Phase.BASELINE)[0]
    onset_bundle = provider(Phase.ONSET)[0]
    recovery_bundle = provider(Phase.RECOVERY)[0]
    ground_truth = build_ground_truth(
        fault=fault,
        verdicts=(*onset, *recovery),
        accepted_evidence_ids=(*onset_bundle.evidence_ids, *recovery_bundle.evidence_ids),
        root_cause_label="bgp_remote_as_mismatch",
    )
    record = build_accepted_record(
        run_ctx=run_ctx,
        scenario=scenario_def(),
        topology=two_router_frr_topology(),
        fault=fault,
        ground_truth=ground_truth,
        baseline=baseline_provider,
        onset=onset_bundle,
        recovery=recovery_bundle,
        precondition_results=pre,
        onset_results=onset,
        recovery_results=recovery,
        restoration=restoration,
        provenance=ProvenanceInfo(
            generator="verifiednet.faults.bgp_remote_as_mismatch",
            generator_version="0.1.0",
            code_commit="offline-sim",
        ),
        completed_phases=("precondition", "inject", "onset", "restore", "recovery"),
        cleanup_status="clean",
    )
    assert record.status == "accepted"
    from verifiednet.schemas import IncidentRecord

    assert IncidentRecord.model_validate_json(record.model_dump_json()) == record

    # transcript: every mutation pending entry is matched by a completed entry
    entries = [
        e
        for e in backend.transcript.entries  # type: ignore[attr-defined]
        if e.mode == "mutation"
    ]
    pend = [e for e in entries if e.stage == "pending"]
    done = [e for e in entries if e.stage == "completed"]
    assert len(pend) == len(done) == 3  # inject, restore, clear
    assert {e.invocation.command_id for e in pend} == {  # type: ignore[union-attr]
        e.invocation.command_id for e in done  # type: ignore[union-attr]
    }
    # router_b never appears in any mutation transcript entry
    assert all(e.target == "router_a" for e in entries)
