"""Gate 4 composition-root wiring, exercised end to end OFFLINE.

A self-contained ``FrrLabSim`` process runner stands in for Docker + FRR: it
answers the compose lifecycle commands (``up``/``down``/``ps``/``network ls``),
the environment-probe commands (``docker version``, ``image inspect``), and every
read-only + mutation ``vtysh`` exec the real backend issues. It injects NO
failures — it is the happy two-router lab. Driving the REAL
``run_accepted_incident`` / ``run_precondition_rejected_incident`` through it
proves the composition root assembles, verifies, indexes, and loads back a
completed run without any container runtime.

No wall clock, no randomness, no network, no Docker.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from verifiednet.artifacts import (
    ArtifactIntegrityError,
    load_run_index,
    load_verified_run_from_index,
    verify_run_index,
)
from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.orchestrator import (
    LiveRunResult,
    run_accepted_incident,
    run_precondition_rejected_incident,
)
from verifiednet.runtime.process import RawResult
from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts

CORRECT_AS = 65002
WRONG_AS = 65999
PEER_IP = "172.30.0.2"
GIT_REV = "deadbeefcafe"
LOCK_HASH = "b" * 64
EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return EPOCH


def _scenario() -> ScenarioDefinition:
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


class _Clock:
    """Deterministic monotonic clock; ``sleep`` advances virtual time only."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FrrLabSim:
    """Happy-path Docker+FRR simulator (a callable ``ProcessRunner``)."""

    def __init__(self) -> None:
        self.a_remote_as = CORRECT_AS
        self._up = False
        self.mutation_targets: list[str] = []

    # -- compose/docker lifecycle ------------------------------------------
    def _ps(self) -> str:
        if not self._up:
            return ""
        return "cid_a\trouter_a\trunning\ncid_b\trouter_b\trunning"

    # -- FRR read responses -------------------------------------------------
    @property
    def _session_up(self) -> bool:
        return self.a_remote_as == CORRECT_AS

    def _bgp_summary(self, service: str) -> str:
        peer_ip = PEER_IP if service == "router_a" else "172.30.0.1"
        remote = self.a_remote_as if service == "router_a" else 65001
        return json.dumps(
            {
                "ipv4Unicast": {
                    "as": 65001 if service == "router_a" else 65002,
                    "peers": {
                        peer_ip: {
                            "state": "Established" if self._session_up else "Idle",
                            "remoteAs": remote,
                        }
                    },
                }
            }
        )

    @staticmethod
    def _interfaces() -> str:
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
            if self._session_up:
                table["10.255.0.2/32"] = [{"protocol": "bgp"}]
        else:
            table["10.255.0.2/32"] = [{"protocol": "connected"}]
            if self._session_up:
                table["10.255.0.1/32"] = [{"protocol": "bgp"}]
        return json.dumps(table)

    def _running_config(self, service: str) -> str:
        if service == "router_a":
            return (
                "hostname router_a\nrouter bgp 65001\n"
                f" neighbor {PEER_IP} remote-as {self.a_remote_as}\n"
            )
        return "hostname router_b\nrouter bgp 65002\n neighbor 172.30.0.1 remote-as 65001\n"

    @staticmethod
    def _cmds(logical: list[str]) -> list[str]:
        return [logical[i + 1] for i in range(len(logical)) if logical[i] == "-c"]

    def _exec(self, service: str, logical: list[str]) -> RawResult:
        if logical and logical[0] == "ping":
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
            if first == "show version":
                banner = "FRRouting 8.4.1_git (router) on Linux"
                return RawResult(0, banner, "", False, False, False)
            raise AssertionError(f"unhandled show command: {first!r}")
        # mutation path
        self.mutation_targets.append(service)
        if not any(c.startswith("clear bgp") for c in cmds):
            for c in cmds:
                if c.startswith("neighbor") and "remote-as" in c:
                    self.a_remote_as = int(c.split()[-1])
        return RawResult(0, "", "", False, False, False)

    def __call__(
        self, argv: Sequence[str], timeout_s: float, max_output_bytes: int
    ) -> RawResult:
        a = list(argv)
        if a[:2] == ["docker", "ps"]:
            return RawResult(0, self._ps(), "", False, False, False)
        if a[:3] == ["docker", "network", "ls"]:
            return RawResult(0, "", "", False, False, False)
        if a[:2] == ["docker", "version"]:
            return RawResult(0, "29.1.3", "", False, False, False)
        if a[:3] == ["docker", "compose", "version"]:
            return RawResult(0, "v2.29.0", "", False, False, False)
        if a[:3] == ["docker", "image", "inspect"]:
            return RawResult(0, "frrouting/frr@sha256:" + "c" * 64, "", False, False, False)
        if a[:2] == ["docker", "compose"] and "up" in a:
            self._up = True
            return RawResult(0, "", "", False, False, False)
        if a[:2] == ["docker", "compose"] and "down" in a:
            self._up = False
            return RawResult(0, "", "", False, False, False)
        if "exec" in a:
            idx = a.index("exec")
            return self._exec(a[idx + 2], a[idx + 3 :])
        raise AssertionError(f"unhandled command: {a!r}")


def _run_accepted(out_root: Path, run_id: str, tmp_path: Path) -> LiveRunResult:
    clock = _Clock()
    return run_accepted_incident(
        out_root=out_root,
        work_dir=tmp_path / run_id,
        run_ctx=RunContext(run_id, clock=_fixed_clock),
        topology=two_router_frr_topology(),
        scenario=_scenario(),
        git_rev=GIT_REV,
        lock_hash=LOCK_HASH,
        runner=FrrLabSim(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        convergence_timeout_s=5.0,
    )


def _run_rejected(out_root: Path, run_id: str, tmp_path: Path, sim: FrrLabSim) -> LiveRunResult:
    clock = _Clock()
    return run_precondition_rejected_incident(
        out_root=out_root,
        work_dir=tmp_path / run_id,
        run_ctx=RunContext(run_id, clock=_fixed_clock),
        topology=two_router_frr_topology(),
        scenario=_scenario(),
        git_rev=GIT_REV,
        lock_hash=LOCK_HASH,
        runner=sim,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        convergence_timeout_s=5.0,
    )


def test_accepted_run_assembles_verifies_and_indexes(tmp_path: Path) -> None:
    out_root = tmp_path / "runs"
    result = _run_accepted(out_root, "run-wire-acc1", tmp_path)

    assert result.convergence.converged is True
    assembled = result.assembled
    assert assembled.loaded.incident.status == "accepted"
    assert assembled.run_dir.is_dir()
    assert assembled.index_entry.acceptance_status == "accepted"
    # the run is discoverable and re-loadable purely through the index
    index = load_run_index(out_root)
    assert [e.run_id for e in index.entries] == ["run-wire-acc1"]
    reloaded = load_verified_run_from_index(out_root, "run-wire-acc1")
    assert reloaded.run_digest == assembled.run_digest
    assert verify_run_index(out_root).verified is True


def test_rejected_run_never_mutates_and_indexes(tmp_path: Path) -> None:
    out_root = tmp_path / "runs"
    sim = FrrLabSim()
    result = _run_rejected(out_root, "run-wire-rej1", tmp_path, sim)

    assert sim.mutation_targets == []  # zero mutation on the rejected path
    assert sim.a_remote_as == CORRECT_AS  # lab left healthy
    assert result.assembled.loaded.incident.status == "rejected"
    assert result.assembled.index_entry.acceptance_status == "rejected"
    reloaded = load_verified_run_from_index(out_root, "run-wire-rej1")
    assert reloaded.incident.status == "rejected"


def test_accepted_and_rejected_share_one_index(tmp_path: Path) -> None:
    out_root = tmp_path / "runs"
    acc = _run_accepted(out_root, "run-wire-acc2", tmp_path)
    rej = _run_rejected(out_root, "run-wire-rej2", tmp_path, FrrLabSim())

    index = load_run_index(out_root)
    assert {e.run_id for e in index.entries} == {"run-wire-acc2", "run-wire-rej2"}
    assert acc.assembled.run_digest != rej.assembled.run_digest
    # both load back through the single shared, verified index
    assert load_verified_run_from_index(out_root, "run-wire-acc2").incident.status == "accepted"
    assert load_verified_run_from_index(out_root, "run-wire-rej2").incident.status == "rejected"
    result = verify_run_index(out_root)
    assert result.verified is True
    assert len(result.checks) >= 2


def test_tampering_one_indexed_run_fails_index_verification(tmp_path: Path) -> None:
    out_root = tmp_path / "runs"
    _run_accepted(out_root, "run-wire-acc3", tmp_path)
    _run_rejected(out_root, "run-wire-rej3", tmp_path, FrrLabSim())
    assert verify_run_index(out_root).verified is True

    # Corrupt one persisted incident payload; the index must refuse to verify.
    victim = out_root / "run-wire-acc3" / "incident.json"
    victim.write_bytes(victim.read_bytes() + b" ")

    result = verify_run_index(out_root)
    assert result.verified is False
    assert any(not c.passed for c in result.checks)
    with pytest.raises(ArtifactIntegrityError):
        load_verified_run_from_index(out_root, "run-wire-acc3")
