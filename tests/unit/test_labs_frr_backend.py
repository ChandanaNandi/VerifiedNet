"""Unit tests for the live FRR Compose backend using a fully faked docker runner.

No real containers, no subprocess: a ``FakeDocker`` process runner interprets the
argv the backend builds and returns programmed ``RawResult``s. State transitions
(``up`` populates the project's containers, ``down`` clears them) let us exercise
leftover detection, readiness polling, health, and verified cleanup deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.runtime import ExecStatus, RawResult
from verifiednet.schemas.topology import TopologySpec

pytestmark = pytest.mark.unit

SERVICES = ("router_a", "router_b")
NETWORK = "vnet-run-test-0001_default"


def _ok(stdout: str = "") -> RawResult:
    return RawResult(0, stdout, "", False, False, False)


def _fail(stderr: str = "boom", exit_code: int = 1) -> RawResult:
    return RawResult(exit_code, "", stderr, False, False, False)


class FakeDocker:
    """Programmable docker/compose runner keyed on argv shape.

    ``up`` populates containers (and the project network); ``down`` clears both.
    ``ready_after_up`` lets a test simulate services that are not yet running.
    """

    def __init__(
        self,
        *,
        services: tuple[str, ...] = SERVICES,
        up_ok: bool = True,
        down_ok: bool = True,
        ready_after_up: bool = True,
        exec_result: RawResult | None = None,
        pre_existing: bool = False,
        version: str = "26.1.0",
        compose_version: str = "v2.29.0",
        image_digest: str = "frrouting/frr@sha256:deadbeef",
    ) -> None:
        self.services = services
        self.up_ok = up_ok
        self.down_ok = down_ok
        self.ready_after_up = ready_after_up
        self.exec_result = exec_result if exec_result is not None else _ok("FRRouting 8.4.1")
        self.version = version
        self.compose_version = compose_version
        self.image_digest = image_digest
        self.calls: list[tuple[str, ...]] = []
        self._containers: tuple[tuple[str, str, str], ...] = (
            tuple((f"pre_{s}", s, "running") for s in services) if pre_existing else ()
        )
        self._networks: tuple[str, ...] = (NETWORK,) if pre_existing else ()

    def _running(self, state: str = "running") -> tuple[tuple[str, str, str], ...]:
        return tuple((f"id_{s}", s, state) for s in self.services)

    def __call__(
        self, argv: Sequence[str], timeout_s: float, max_output_bytes: int
    ) -> RawResult:
        a = list(argv)
        self.calls.append(tuple(a))
        if a[:3] == ["docker", "compose", "version"]:
            return _ok(self.compose_version)
        if a[:2] == ["docker", "version"]:
            return _ok(self.version)
        if a[:3] == ["docker", "image", "inspect"]:
            return _ok(self.image_digest)
        if a[:2] == ["docker", "compose"] and "up" in a:
            if not self.up_ok:
                return _fail("up failed")
            self._containers = self._running("running" if self.ready_after_up else "created")
            self._networks = (NETWORK,)
            return _ok()
        if a[:2] == ["docker", "compose"] and "down" in a:
            if not self.down_ok:
                return _fail("down failed")
            self._containers = ()
            self._networks = ()
            return _ok()
        if a[:2] == ["docker", "compose"] and "exec" in a:
            return self.exec_result
        if a[:2] == ["docker", "ps"]:
            return _ok("".join(f"{c}\t{s}\t{st}\n" for c, s, st in self._containers))
        if a[:3] == ["docker", "network", "ls"]:
            return _ok("".join(n + "\n" for n in self._networks))
        return _ok()


def make_backend(
    runner: FakeDocker,
    tmp_path: Path,
    run_ctx: RunContext,
    topology: TopologySpec,
    *,
    image_ref: str | None = None,
    up_timeout_s: float = 120.0,
) -> FrrComposeBackend:
    return FrrComposeBackend(
        topology,
        run_ctx,
        work_dir=tmp_path,
        image_ref=image_ref,
        runner=runner,
        up_timeout_s=up_timeout_s,
        monotonic=lambda: 0.0,
        sleep=lambda _s: None,
    )


# --- lifecycle happy paths --------------------------------------------------


def test_start_renders_detects_no_leftover_and_comes_up(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    assert (tmp_path / "docker-compose.yml").exists()
    assert any("up" in c for c in runner.calls)
    assert backend.project_name == "vnet-run-test-0001"


def test_start_is_idempotent(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    up_calls = sum(1 for c in runner.calls if "up" in c)
    backend.start()
    assert sum(1 for c in runner.calls if "up" in c) == up_calls


def test_health_check_true_when_running_and_vtysh_ok(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    assert backend.health_check() is True


def test_stop_verifies_zero_resources(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    backend.stop()
    assert any("down" in c for c in runner.calls)


def test_execute_readonly_goes_through_transport_adapter(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    result = backend.execute_readonly("router_a", ["vtysh", "-c", "show version"], 10.0)
    assert result.status is ExecStatus.OK
    assert result.invocation is not None
    assert result.argv == result.invocation.transport_argv


def test_topology_returns_spec_and_image_override_applied(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    pinned = "frrouting/frr:v8.4.1@sha256:0f8c174d"
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology, image_ref=pinned)
    assert backend.topology().images.frr == pinned


def test_capture_environment_metadata_uses_real_values(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    pinned = "frrouting/frr:v8.4.1@sha256:0f8c174d"
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology, image_ref=pinned)
    meta = backend.capture_environment_metadata()
    assert meta["container_runtime"] == "docker"
    assert meta["image_reference"] == pinned
    assert meta["image_manifest_digest"] == "sha256:0f8c174d"
    assert meta["container_runtime_version"] == "26.1.0"
    assert meta["compose_version"] == "v2.29.0"
    assert meta["os_name"] and meta["python_version"]


def test_reset_stops_then_starts(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    backend.reset()
    assert sum(1 for c in runner.calls if "down" in c) >= 1
    assert sum(1 for c in runner.calls if "up" in c) >= 2
