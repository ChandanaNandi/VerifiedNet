"""Gate 4 live-incident failure paths, exercised through the REAL wiring (offline).

A configurable ``LabSim`` process runner drives the real
``BgpRemoteAsMismatchScenario`` + real ``MutationExecutor`` (via
``build_mutation_adapter``) + real ``LiveScenarioEvidenceProvider``. Each failure
must be loud and leave the ledger visibly at the phase it failed in; no accepted
record may be built unless the ledger reaches ``RECOVERY_VERIFIED``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from verifiednet.common.errors import (
    InjectFailedError,
    OnsetNotVerifiedError,
    RecoveryNotVerifiedError,
    RestoreFailedError,
)
from verifiednet.common.runctx import RunContext
from verifiednet.faults.bgp_remote_as_mismatch import BgpRemoteAsMismatchScenario
from verifiednet.faults.frr_commands import set_remote_as_argv
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.runtime.policy import bgp_remote_as_mutation_shapes
from verifiednet.runtime.process import RawResult
from verifiednet.runtime.results import ExecStatus
from verifiednet.runtime.transcript import InMemoryTranscript
from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts
from verifiednet.verifiers.claims import ClaimVerifier

pytestmark = pytest.mark.failure

CORRECT_AS = 65002
WRONG_AS = 65999
PEER_IP = "172.30.0.2"


def scenario_def() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        family="bgp",
        template_id="bgp_remote_as_mismatch",
        version=1,
        parameters={"wrong_asn": WRONG_AS, "target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=5.0, onset_s=3.0, recovery_s=3.0, command_s=10.0, poll_interval_s=0.5
        ),
    )


class LabSim:
    """Configurable FRR simulator with injectable failures."""

    def __init__(
        self,
        *,
        fail_command: Callable[[list[str]], bool] | None = None,
        ignore_inject: bool = False,
        keep_down_after_restore: bool = False,
        hide_peer_on_onset: bool = False,
        change_peer_config_after_inject: bool = False,
    ) -> None:
        self.a_remote_as = CORRECT_AS
        self.fail_command = fail_command
        self.ignore_inject = ignore_inject
        self.keep_down_after_restore = keep_down_after_restore
        self.hide_peer_on_onset = hide_peer_on_onset
        self.change_peer_config_after_inject = change_peer_config_after_inject
        self._injected = False
        self._restored = False
        self.mutation_targets: list[str] = []

    @property
    def session_up(self) -> bool:
        if self._restored and self.keep_down_after_restore:
            return False
        return self.a_remote_as == CORRECT_AS

    def _bgp_summary(self, service: str) -> str:
        peer_ip = PEER_IP if service == "router_a" else "172.30.0.1"
        if service == "router_a" and self.hide_peer_on_onset and self._injected:
            return json.dumps({"ipv4Unicast": {"as": 65001, "peers": {}}})
        remote = self.a_remote_as if service == "router_a" else 65001
        return json.dumps(
            {
                "ipv4Unicast": {
                    "as": 65001 if service == "router_a" else 65002,
                    "peers": {
                        peer_ip: {
                            "state": "Established" if self.session_up else "Idle",
                            "remoteAs": remote,
                        }
                    },
                }
            }
        )

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
                "hostname router_a\nrouter bgp 65001\n"
                f" neighbor {PEER_IP} remote-as {self.a_remote_as}\n"
            )
        changed = self._injected and self.change_peer_config_after_inject
        extra = " bgp router-id 9.9.9.9\n" if changed else ""
        return (
            "hostname router_b\nrouter bgp 65002\n"
            f"{extra} neighbor 172.30.0.1 remote-as 65001\n"
        )

    @staticmethod
    def _cmds(logical: list[str]) -> list[str]:
        return [logical[i + 1] for i in range(len(logical)) if logical[i] == "-c"]

    def __call__(self, argv: Sequence[str], timeout_s: float, max_output_bytes: int) -> RawResult:
        a = list(argv)
        exec_idx = a.index("exec")
        service = a[exec_idx + 2]
        logical = a[exec_idx + 3 :]
        if self.fail_command is not None and self.fail_command(logical):
            return RawResult(1, "", "vtysh: command failed", False, False, False)
        if logical[0] == "ping":
            return RawResult(0, "1 received", "", False, False, False)
        cmds = self._cmds(logical)
        first = cmds[0] if cmds else ""
        if first.startswith("show"):
            if first == "show ip bgp summary json":
                return RawResult(0, self._bgp_summary(service), "", False, False, False)
            if first == "show interface json":
                return RawResult(0, self._interfaces(), "", False, False, False)
            if first == "show ip route json":
                return RawResult(0, self._routes(service), "", False, False, False)
            if first == "show running-config":
                return RawResult(0, self._running_config(service), "", False, False, False)
            raise AssertionError(first)
        # mutation
        self.mutation_targets.append(service)
        is_clear = any(c.startswith("clear bgp") for c in cmds)
        if not is_clear:
            for c in cmds:
                if c.startswith("neighbor") and "remote-as" in c:
                    new_as = int(c.split()[-1])
                    if new_as == WRONG_AS:
                        self._injected = True
                        if not self.ignore_inject:
                            self.a_remote_as = new_as
                    else:  # revert
                        self._restored = True
                        self.a_remote_as = new_as
        return RawResult(0, "", "", False, False, False)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def build(
    sim: LabSim,
    run_ctx: RunContext,
    tmp_path: Path,
    *,
    transcript: InMemoryTranscript | None = None,
) -> tuple[BgpRemoteAsMismatchScenario, Ledger, FrrComposeBackend]:
    backend = FrrComposeBackend(
        two_router_frr_topology(), run_ctx, work_dir=tmp_path, runner=sim, transcript=transcript
    )
    provider = LiveScenarioEvidenceProvider(
        executor=backend.readonly_executor,
        topology=two_router_frr_topology(),
        run_ctx=run_ctx,
        target_node="router_a",
        peer_node="router_b",
    )
    mutation = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_remote_as_mutation_shapes()
    )
    ledger = Ledger(run_ctx)
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
    return scenario, ledger, backend


def _inject_cmd(logical: list[str]) -> bool:
    return any("remote-as 65999" in c for c in logical)


def _revert_cmd(logical: list[str]) -> bool:
    return any("remote-as 65002" in c for c in logical)


def _clear_cmd(logical: list[str]) -> bool:
    return any(c.startswith("clear bgp") for c in logical)


def test_inject_nonzero_leaves_ledger_injecting(tmp_path: Path, run_ctx: RunContext) -> None:
    scenario, ledger, _ = build(LabSim(fail_command=_inject_cmd), run_ctx, tmp_path)
    scenario.validate_preconditions()
    with pytest.raises(InjectFailedError):
        scenario.inject()
    assert ledger.current is LifecyclePhase.INJECTING
    # mutation-failure recovery path stays open
    scenario.restore()
    assert ledger.current is LifecyclePhase.RESTORED


def test_injected_state_never_appears_times_out(tmp_path: Path, run_ctx: RunContext) -> None:
    scenario, ledger, _ = build(LabSim(ignore_inject=True), run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    with pytest.raises(OnsetNotVerifiedError):
        scenario.verify_onset()
    assert ledger.current is LifecyclePhase.INJECTED
    restoration = scenario.restore()  # restore still attempted after onset timeout
    assert restoration.attempted


def test_wrong_as_not_observable_times_out(tmp_path: Path, run_ctx: RunContext) -> None:
    scenario, ledger, _ = build(LabSim(hide_peer_on_onset=True), run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    with pytest.raises(OnsetNotVerifiedError):
        scenario.verify_onset()
    assert ledger.current is LifecyclePhase.INJECTED


def test_restore_command_fails_leaves_ledger_restoring(tmp_path: Path, run_ctx: RunContext) -> None:
    scenario, ledger, _ = build(LabSim(fail_command=_revert_cmd), run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    with pytest.raises(RestoreFailedError):
        scenario.restore()
    assert ledger.current is LifecyclePhase.RESTORING


def test_clear_bgp_failure_recorded_but_restore_completes(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    scenario, ledger, _ = build(LabSim(fail_command=_clear_cmd), run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    restoration = scenario.restore()
    assert restoration.completed is True
    assert restoration.failure_reason != ""  # clear-bgp failure surfaced, not hidden
    assert ledger.current is LifecyclePhase.RESTORED


def test_recovery_never_established_times_out(tmp_path: Path, run_ctx: RunContext) -> None:
    scenario, ledger, _ = build(LabSim(keep_down_after_restore=True), run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    scenario.restore()
    with pytest.raises(RecoveryNotVerifiedError):
        scenario.verify_recovery()
    assert ledger.current is LifecyclePhase.RESTORED
    # NO accepted record may be built: final phase is not RECOVERY_VERIFIED.
    assert ledger.current is not LifecyclePhase.RECOVERY_VERIFIED


def test_peer_config_change_during_onset_yields_failing_verdict(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    scenario, _, _ = build(LabSim(change_peer_config_after_inject=True), run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    onset_results = scenario.verify_onset()
    # config_unchanged for the peer must be present and NOT committable.
    config_results = [r for r in onset_results if "config_unchanged" in r.check_id]
    assert config_results and not any(r.committable for r in config_results)


def test_mutation_terminal_transcript_write_failure_is_visible(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    # fail_after=1: the write-ahead PENDING entry lands, the COMPLETED append fails.
    transcript = InMemoryTranscript(fail_after=1)
    _, _, backend = build(LabSim(), run_ctx, tmp_path, transcript=transcript)
    adapter = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_remote_as_mutation_shapes()
    )
    result = adapter.run("router_a", set_remote_as_argv(65001, PEER_IP, WRONG_AS), 10.0)
    assert result.status is ExecStatus.OK
    assert result.transcript_ok is False  # terminal write failure surfaced, not swallowed
