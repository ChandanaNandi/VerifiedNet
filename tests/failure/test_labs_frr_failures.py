"""Failure-path tests for the live FRR Compose backend.

Every lifecycle failure must be loud and specific: pre-existing resources abort
start, a failed ``up`` raises, services that never reach ``running`` time out,
and incomplete teardown raises rather than silently leaving orphans. Health
checks, by contract, return ``False`` for an unhealthy lab and never raise.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.backend import FrrComposeBackend, LabBackendError
from verifiednet.runtime import RawResult
from verifiednet.schemas.topology import TopologySpec

pytestmark = pytest.mark.failure

SERVICES = ("router_a", "router_b")
NETWORK = "vnet-run-test-0001_default"


def _ok(stdout: str = "") -> RawResult:
    return RawResult(0, stdout, "", False, False, False)


def _fail(stderr: str = "boom", exit_code: int = 1) -> RawResult:
    return RawResult(exit_code, "", stderr, False, False, False)


def _not_found() -> RawResult:
    return RawResult(None, "", "", False, True, False)


class FakeDocker:
    """Programmable docker runner with injectable failure modes."""

    def __init__(
        self,
        *,
        up_ok: bool = True,
        down_ok: bool = True,
        ready_after_up: bool = True,
        leave_on_down: bool = False,
        pre_existing: bool = False,
        exec_result: RawResult | None = None,
        version_result: RawResult | None = None,
        docker_missing: bool = False,
    ) -> None:
        self.up_ok = up_ok
        self.down_ok = down_ok
        self.ready_after_up = ready_after_up
        self.leave_on_down = leave_on_down
        self.exec_result = exec_result if exec_result is not None else _ok("FRRouting 8.4.1")
        self.version_result = version_result
        self.docker_missing = docker_missing
        self.calls: list[tuple[str, ...]] = []
        self._containers: tuple[tuple[str, str, str], ...] = (
            tuple((f"pre_{s}", s, "running") for s in SERVICES) if pre_existing else ()
        )
        self._networks: tuple[str, ...] = (NETWORK,) if pre_existing else ()

    def _running(self, state: str) -> tuple[tuple[str, str, str], ...]:
        return tuple((f"id_{s}", s, state) for s in SERVICES)

    def __call__(
        self, argv: object, timeout_s: float, max_output_bytes: int
    ) -> RawResult:
        a = list(argv)  # type: ignore[call-overload]
        self.calls.append(tuple(a))
        if self.docker_missing:
            return _not_found()
        if a[:3] == ["docker", "compose", "version"]:
            return _ok("v2.29.0")
        if a[:2] == ["docker", "version"]:
            return self.version_result if self.version_result is not None else _ok("26.1.0")
        if a[:3] == ["docker", "image", "inspect"]:
            return _ok("frrouting/frr@sha256:deadbeef")
        if a[:2] == ["docker", "compose"] and "up" in a:
            if not self.up_ok:
                return _fail("compose up failed")
            self._containers = self._running("running" if self.ready_after_up else "created")
            self._networks = (NETWORK,)
            return _ok()
        if a[:2] == ["docker", "compose"] and "down" in a:
            if not self.down_ok:
                return _fail("compose down failed")
            if not self.leave_on_down:
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
    up_timeout_s: float = 5.0,
    monotonic: Callable[[], float] = lambda: 0.0,
    sleep: Callable[[float], None] = lambda _s: None,
) -> FrrComposeBackend:
    return FrrComposeBackend(
        topology,
        run_ctx,
        work_dir=tmp_path,
        runner=runner,
        up_timeout_s=up_timeout_s,
        monotonic=monotonic,
        sleep=sleep,
    )


def test_start_aborts_on_pre_existing_resources(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker(pre_existing=True)
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    with pytest.raises(LabBackendError, match="pre-existing"):
        backend.start()
    assert not any("up" in c for c in runner.calls)


def test_start_raises_when_up_fails(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker(up_ok=False)
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    with pytest.raises(LabBackendError, match="compose up"):
        backend.start()


def test_start_times_out_when_services_never_ready(
    tmp_path: Path,
    run_ctx: RunContext,
    two_router_topology: TopologySpec,
    fake_clock: object,
) -> None:
    runner = FakeDocker(ready_after_up=False)
    backend = make_backend(
        runner,
        tmp_path,
        run_ctx,
        two_router_topology,
        up_timeout_s=3.0,
        monotonic=fake_clock.monotonic,  # type: ignore[attr-defined]
        sleep=fake_clock.advance,  # type: ignore[attr-defined]
    )
    with pytest.raises(LabBackendError, match="not all running"):
        backend.start()


def test_stop_raises_when_down_command_fails(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker(down_ok=False)
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    with pytest.raises(LabBackendError, match="compose down"):
        backend.stop()


def test_stop_raises_when_cleanup_incomplete(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker(leave_on_down=True)
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    with pytest.raises(LabBackendError, match="cleanup incomplete"):
        backend.stop()


def test_health_check_false_when_service_not_running(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker()
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    # never started: no containers reported running
    assert backend.health_check() is False


def test_health_check_false_when_vtysh_nonzero(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker(exec_result=_fail("vtysh error"))
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    backend.start()
    assert backend.health_check() is False


def test_docker_missing_raises_labbackenderror(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    runner = FakeDocker(docker_missing=True)
    backend = make_backend(runner, tmp_path, run_ctx, two_router_topology)
    with pytest.raises(LabBackendError, match="not found"):
        backend.start()


def test_capture_metadata_omits_unavailable_docker_version(
    tmp_path: Path, run_ctx: RunContext, two_router_topology: TopologySpec
) -> None:
    # When `docker version` fails, the key is omitted — never invented.
    runner = FakeDocker(version_result=_fail("cannot connect"))
    backend = FrrComposeBackend(
        two_router_topology,
        run_ctx,
        work_dir=tmp_path,
        runner=runner,
    )
    meta = backend.capture_environment_metadata()
    assert "container_runtime_version" not in meta
    assert meta["compose_version"] == "v2.29.0"
