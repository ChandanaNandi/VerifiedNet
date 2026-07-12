"""Live FRR-on-Compose lab backend (Gate 4 Step 6).

Implements the ``LabBackend`` protocol for the approved two-router routed-eBGP
topology. Lifecycle (``up``/``down``/``ps``/``network ls``) is driven through the
runtime process runner — this module never imports ``subprocess`` and never uses
shell strings. Read-only commands flow through the transport adapter, which
validates the logical command and executes the ``docker compose exec -T``
transport under one command identity.

Scope note (Gate 4 Step 1 commit): this backend brings services up from the
pinned FRR image, proves they are running and answer read-only ``vtysh``
commands, and tears them down with verified cleanup. Fault injection, BGP-state
verification, and incident orchestration are later commits — ``health_check``
does NOT require BGP convergence (that belongs to collectors/verifiers).

Container identity is resolved by Compose project + service labels; ``start``
detects pre-existing resources carrying this project's label and fails loudly.
Readiness is polled with a bounded deadline — never a bare sleep as a
correctness check.
"""

from __future__ import annotations

import platform
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.compose_project import ComposeProject
from verifiednet.labs.frr.exec_adapter import FrrReadOnlyTransportAdapter
from verifiednet.labs.frr.render import render_all, write_rendered
from verifiednet.runtime.policy import CommandPolicy, TargetPolicy
from verifiednet.runtime.process import ProcessRunner, RawResult, default_runner
from verifiednet.runtime.readonly import DEFAULT_MAX_OUTPUT_BYTES, ReadOnlyExecutor
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.runtime.transcript import InMemoryTranscript, TranscriptWriter
from verifiednet.schemas.topology import ImageSpec, TopologySpec

DEFAULT_COMMAND_TIMEOUT_S = 10.0
DEFAULT_UP_TIMEOUT_S = 120.0
DEFAULT_DOWN_TIMEOUT_S = 60.0
DEFAULT_POLL_INTERVAL_S = 1.0
_HEALTH_VTYSH_ARGV = ("vtysh", "-c", "show version")


class LabBackendError(VerifiedNetError):
    """A lab-backend lifecycle operation failed (start/stop/cleanup)."""


class FrrComposeBackend:
    """Live FRR lab backend over Docker Compose (implements ``LabBackend``)."""

    def __init__(
        self,
        topology: TopologySpec,
        run_ctx: RunContext,
        *,
        work_dir: str | Path,
        image_ref: str | None = None,
        runner: ProcessRunner = default_runner,
        transcript: TranscriptWriter | None = None,
        command_timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
        up_timeout_s: float = DEFAULT_UP_TIMEOUT_S,
        down_timeout_s: float = DEFAULT_DOWN_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if image_ref is not None:
            topology = topology.model_copy(update={"images": ImageSpec(frr=image_ref)})
        self._topology = topology
        self._image_ref = topology.images.frr
        self._run_ctx = run_ctx
        self._work_dir = Path(work_dir)
        self._runner = runner
        self._transcript = transcript if transcript is not None else InMemoryTranscript()
        self._services = tuple(node.name for node in topology.nodes)
        self._compose_file = self._work_dir / "docker-compose.yml"
        self._project = ComposeProject.for_run(run_ctx.run_id, self._compose_file, self._services)
        # "ping" is allowed alongside "vtysh": the reachability collector's
        # probes are read-only evidence gathering (Gate 2.5 W8 3/3 rule).
        read_executor = ReadOnlyExecutor(
            runner,
            CommandPolicy(allowed_binaries=frozenset({"vtysh", "ping"})),
            TargetPolicy(allowed_targets=frozenset(self._services)),
            self._transcript,
            run_ctx,
        )
        self._read_adapter = FrrReadOnlyTransportAdapter(self._project, read_executor, run_ctx)
        self._command_timeout_s = command_timeout_s
        self._up_timeout_s = up_timeout_s
        self._down_timeout_s = down_timeout_s
        self._poll_interval_s = poll_interval_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._started = False

    # -- properties ---------------------------------------------------------

    @property
    def project_name(self) -> str:
        return self._project.project

    @property
    def transcript(self) -> TranscriptWriter:
        return self._transcript

    @property
    def readonly_executor(self) -> FrrReadOnlyTransportAdapter:
        """The read-only transport adapter, for wiring collectors.

        Satisfies the collectors' ``ReadOnlyExec`` protocol. Mutation capability
        is deliberately NOT exposed anywhere on this backend.
        """
        return self._read_adapter

    def topology(self) -> TopologySpec:
        return self._topology

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Render, detect leftovers, ``compose up -d``, poll readiness."""
        if self._started:
            return
        write_rendered(render_all(self._topology), self._work_dir)
        leftover = self._project_containers()
        if leftover:
            raise LabBackendError(
                f"pre-existing resources for project {self._project.project!r}: {leftover!r}"
            )
        raw = self._runner(self._project.up_argv(), self._up_timeout_s, DEFAULT_MAX_OUTPUT_BYTES)
        self._require_ok(raw, "compose up")
        self._await_services_running()
        self._started = True

    def stop(self) -> None:
        """``compose down`` with volume + orphan removal, then verify zero resources."""
        raw = self._runner(
            self._project.down_argv(), self._down_timeout_s, DEFAULT_MAX_OUTPUT_BYTES
        )
        self._require_ok(raw, "compose down")
        remaining = self._project_containers()
        networks = self._project_networks()
        if remaining or networks:
            raise LabBackendError(
                f"cleanup incomplete for {self._project.project!r}: "
                f"containers={remaining!r} networks={networks!r}"
            )
        self._started = False

    def reset(self) -> None:
        """Deterministic stop-then-start; no hidden retry loop."""
        self.stop()
        self.start()

    def health_check(self) -> bool:
        """True iff both services are running and answer a read-only ``vtysh``."""
        try:
            rows = self._project_containers()
        except LabBackendError:
            return False
        running = {svc for _cid, svc, state in rows if state.lower() == "running"}
        if not all(svc in running for svc in self._services):
            return False
        for svc in self._services:
            result = self._read_adapter.run(svc, _HEALTH_VTYSH_ARGV, self._command_timeout_s)
            if result.status is not ExecStatus.OK:
                return False
        return True

    def execute_readonly(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        """Run a policy-checked read-only command on *target* via the adapter."""
        return self._read_adapter.run(target, argv, timeout_s)

    def capture_environment_metadata(self) -> dict[str, str]:
        """Collect real reproducibility metadata; never invent unavailable values."""
        meta: dict[str, str] = {
            "os_name": platform.system(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "python_version": platform.python_version(),
            "container_runtime": "docker",
            "image_reference": self._image_ref,
        }
        if "@sha256:" in self._image_ref:
            meta["image_manifest_digest"] = self._image_ref.split("@", 1)[1]
        version = self._runner(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            self._command_timeout_s,
            DEFAULT_MAX_OUTPUT_BYTES,
        )
        if version.exit_code == 0 and version.stdout.strip():
            meta["container_runtime_version"] = version.stdout.strip()
        compose_v = self._runner(
            ["docker", "compose", "version", "--short"],
            self._command_timeout_s,
            DEFAULT_MAX_OUTPUT_BYTES,
        )
        if compose_v.exit_code == 0 and compose_v.stdout.strip():
            meta["compose_version"] = compose_v.stdout.strip()
        resolved = self._runner(
            ["docker", "image", "inspect", self._image_ref, "--format", "{{index .RepoDigests 0}}"],
            self._command_timeout_s,
            DEFAULT_MAX_OUTPUT_BYTES,
        )
        if resolved.exit_code == 0 and resolved.stdout.strip():
            meta["platform_resolved_repo_digest"] = resolved.stdout.strip()
        return meta

    # -- internals ----------------------------------------------------------

    def _project_containers(self) -> tuple[tuple[str, str, str], ...]:
        raw = self._runner(
            self._project.ps_labels_argv(), self._command_timeout_s, DEFAULT_MAX_OUTPUT_BYTES
        )
        self._require_ok(raw, "docker ps")
        return ComposeProject.parse_ps_labels(raw.stdout)

    def _project_networks(self) -> tuple[str, ...]:
        raw = self._runner(
            self._project.network_ls_argv(), self._command_timeout_s, DEFAULT_MAX_OUTPUT_BYTES
        )
        self._require_ok(raw, "docker network ls")
        return tuple(line for line in raw.stdout.splitlines() if line.strip())

    def _await_services_running(self) -> None:
        deadline = self._monotonic() + self._up_timeout_s
        while True:
            rows = self._project_containers()
            running = {svc for _cid, svc, state in rows if state.lower() == "running"}
            if all(svc in running for svc in self._services):
                return
            if self._monotonic() >= deadline:
                raise LabBackendError(
                    f"services {self._services!r} not all running before deadline "
                    f"(running={sorted(running)!r})"
                )
            self._sleep(self._poll_interval_s)

    @staticmethod
    def _require_ok(raw: RawResult, what: str) -> None:
        if raw.not_found:
            raise LabBackendError(f"{what}: docker binary not found on PATH")
        if raw.timed_out:
            raise LabBackendError(f"{what}: timed out")
        if raw.exit_code != 0:
            raise LabBackendError(f"{what}: exit {raw.exit_code}: {raw.stderr.strip()}")
