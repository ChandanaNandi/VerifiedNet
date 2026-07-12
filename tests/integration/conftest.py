"""Docker-gated live integration fixtures (Gate 4 Step 2).

Integration tests run against a REAL Docker daemon with the approved pinned
FRR image. When Docker is unavailable every test in this directory SKIPS with
an explicit reason (autouse gate) — they never silently pass. Project names
are derived deterministically from per-run ids (``project_name_for_run``),
unique per invocation so repeated local runs never collide.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable

import pytest

from verifiednet.runtime.process import default_runner


def _docker_unavailable_reason() -> str | None:
    if shutil.which("docker") is None:
        return "docker binary not on PATH"
    result = default_runner(
        ["docker", "version", "--format", "{{.Server.Version}}"], 10.0, 65536
    )
    if result.exit_code != 0 or not result.stdout.strip():
        detail = result.stderr.strip() or "no server version reported"
        return f"docker daemon unavailable: {detail}"
    return None


@pytest.fixture(autouse=True, scope="session")
def _require_docker() -> None:
    """Skip (never silently pass) every integration test without a Docker daemon.

    Session-scoped so it is instantiated BEFORE module-scoped live-lab fixtures
    (pytest orders fixture setup by scope, widest first).
    """
    reason = _docker_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)


@pytest.fixture
def unique_run_id() -> Callable[[str], str]:
    """Time-derived unique run id (test-side only; src never uses wall-clock ids)."""

    def make(prefix: str) -> str:
        return f"{prefix}-{int(time.time())}"

    return make


@pytest.fixture
def project_containers() -> Callable[[str], list[str]]:
    """Independent host-side check: container names carrying a project label."""

    def query(project: str) -> list[str]:
        result = default_runner(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Names}}",
            ],
            10.0,
            65536,
        )
        assert result.exit_code == 0, f"docker ps failed: {result.stderr}"
        return [line for line in result.stdout.splitlines() if line.strip()]

    return query


@pytest.fixture
def project_networks() -> Callable[[str], list[str]]:
    """Independent host-side check: networks carrying a project label."""

    def query(project: str) -> list[str]:
        result = default_runner(
            [
                "docker",
                "network",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Name}}",
            ],
            10.0,
            65536,
        )
        assert result.exit_code == 0, f"docker network ls failed: {result.stderr}"
        return [line for line in result.stdout.splitlines() if line.strip()]

    return query
