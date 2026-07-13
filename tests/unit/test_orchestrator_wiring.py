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
    BGP_NEIGHBOR_REMOVAL_BINDING,
    REMOTE_AS_MISMATCH_BINDING,
    FaultFamilyBinding,
    LiveRunError,
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
    """Happy-path Docker+FRR simulator (a callable ``ProcessRunner``).

    Models BOTH approved mutation families: the remote-AS value and the
    neighbor object (presence + activation) on ``router_a``.
    """

    def __init__(self) -> None:
        self.a_remote_as = CORRECT_AS
        self.a_has_neighbor = True
        self.a_activated = True
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
        return self.a_has_neighbor and self.a_remote_as == CORRECT_AS

    @property
    def _routes_exchanged(self) -> bool:
        return self._session_up and self.a_activated

    def _bgp_summary(self, service: str) -> str:
        if service == "router_a":
            if not self.a_has_neighbor:
                # Live-verified FRR 8.4.1 behavior: with the LAST ipv4-unicast
                # neighbor removed, the whole ipv4Unicast object is omitted.
                return json.dumps({})
            peers = {
                PEER_IP: {
                    "state": "Established" if self._session_up else "Idle",
                    "remoteAs": self.a_remote_as,
                }
            }
            local = 65001
        else:
            peers = {
                "172.30.0.1": {
                    "state": "Established" if self._session_up else "Idle",
                    "remoteAs": 65001,
                }
            }
            local = 65002
        return json.dumps({"ipv4Unicast": {"as": local, "peers": peers}})

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
            if self._routes_exchanged:
                table["10.255.0.2/32"] = [{"protocol": "bgp"}]
        else:
            table["10.255.0.2/32"] = [{"protocol": "connected"}]
            if self._routes_exchanged:
                table["10.255.0.1/32"] = [{"protocol": "bgp"}]
        return json.dumps(table)

    def _running_config(self, service: str) -> str:
        # Canonical: a pure function of the logical state (like live FRR), so a
        # restored config is byte-identical to its baseline.
        if service != "router_a":
            return "hostname router_b\nrouter bgp 65002\n neighbor 172.30.0.1 remote-as 65001\n"
        lines = ["hostname router_a", "router bgp 65001"]
        if self.a_has_neighbor:
            lines.append(f" neighbor {PEER_IP} remote-as {self.a_remote_as}")
        lines.append(" address-family ipv4 unicast")
        lines.append("  network 10.255.0.1/32")
        if self.a_has_neighbor and self.a_activated:
            lines.append(f"  neighbor {PEER_IP} activate")
        lines.append(" exit-address-family")
        return "\n".join(lines) + "\n"

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
        for c in cmds:
            if c.startswith("no neighbor"):
                self.a_has_neighbor = False
                self.a_activated = False
            elif c.startswith("neighbor") and "remote-as" in c:
                self.a_has_neighbor = True
                self.a_remote_as = int(c.split()[-1])
            elif c.startswith("neighbor") and c.endswith("activate"):
                self.a_activated = True
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


def _nr_scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-neighbor-removal-2r-0001",
        family="bgp",
        template_id="bgp_neighbor_removal",
        version=1,
        parameters={"target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=5.0, onset_s=3.0, recovery_s=3.0, command_s=10.0, poll_interval_s=0.5
        ),
    )


def _run_accepted(
    out_root: Path,
    run_id: str,
    tmp_path: Path,
    *,
    binding: FaultFamilyBinding = REMOTE_AS_MISMATCH_BINDING,
    scenario: ScenarioDefinition | None = None,
) -> LiveRunResult:
    clock = _Clock()
    return run_accepted_incident(
        out_root=out_root,
        work_dir=tmp_path / run_id,
        run_ctx=RunContext(run_id, clock=_fixed_clock),
        topology=two_router_frr_topology(),
        scenario=scenario if scenario is not None else _scenario(),
        git_rev=GIT_REV,
        lock_hash=LOCK_HASH,
        runner=FrrLabSim(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        convergence_timeout_s=5.0,
        binding=binding,
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


def test_neighbor_removal_run_through_binding(tmp_path: Path) -> None:
    # Gate 5.2: the SAME composition entry point runs the new family via its
    # explicit binding — accepted, assembled, indexed, reload-verified.
    out_root = tmp_path / "runs"
    result = _run_accepted(
        out_root,
        "run-wire-nr1",
        tmp_path,
        binding=BGP_NEIGHBOR_REMOVAL_BINDING,
        scenario=_nr_scenario(),
    )

    assert result.convergence.converged is True
    record = result.assembled.loaded.incident
    assert record.status == "accepted"
    assert record.scenario.template_id == "bgp_neighbor_removal"
    assert record.ground_truth is not None
    assert record.ground_truth.root_cause_label == "bgp_neighbor_removal"
    assert record.fault is not None and record.fault.parameter_name == "neighbor"
    # byte-identical config recovery is part of the persisted verdict set
    assert any(
        "config_unchanged:router_a" in v.check_id
        for v in record.ground_truth.verdicts
    )
    # remove/restore/clear = 3 mutation pairs, router_a only
    muts = [e for e in result.assembled.loaded.transcript if e.mode == "mutation"]
    assert len([e for e in muts if e.stage == "pending"]) == 3
    assert all(e.target == "router_a" for e in muts)
    reloaded = load_verified_run_from_index(out_root, "run-wire-nr1")
    assert reloaded.incident == record


def test_binding_template_mismatch_is_refused(tmp_path: Path) -> None:
    # A remote-AS scenario definition under the neighbor-removal binding is a
    # composition error and must be refused before any lab action.
    with pytest.raises(LiveRunError, match="does not match the"):
        _run_accepted(
            tmp_path / "runs",
            "run-wire-mismatch",
            tmp_path,
            binding=BGP_NEIGHBOR_REMOVAL_BINDING,
            scenario=_scenario(),
        )


def test_cross_family_runs_share_one_index(tmp_path: Path) -> None:
    # Gate 5.2 cross-family regression: a remote-AS run and a neighbor-removal
    # run coexist in ONE verified index and each loads back through it.
    out_root = tmp_path / "runs"
    ras = _run_accepted(out_root, "run-wire-xfam-ras", tmp_path)
    nr = _run_accepted(
        out_root,
        "run-wire-xfam-nr",
        tmp_path,
        binding=BGP_NEIGHBOR_REMOVAL_BINDING,
        scenario=_nr_scenario(),
    )

    index = load_run_index(out_root)
    templates = {e.run_id: e.template_id for e in index.entries}
    assert templates == {
        "run-wire-xfam-ras": "bgp_remote_as_mismatch",
        "run-wire-xfam-nr": "bgp_neighbor_removal",
    }
    assert verify_run_index(out_root).verified is True
    assert ras.assembled.run_digest != nr.assembled.run_digest
    ras_loaded = load_verified_run_from_index(out_root, "run-wire-xfam-ras")
    nr_loaded = load_verified_run_from_index(out_root, "run-wire-xfam-nr")
    assert ras_loaded.incident.ground_truth is not None
    assert nr_loaded.incident.ground_truth is not None
    assert ras_loaded.incident.ground_truth.root_cause_label == "bgp_remote_as_mismatch"
    assert nr_loaded.incident.ground_truth.root_cause_label == "bgp_neighbor_removal"


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
