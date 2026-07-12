"""Unit tests for live fixture capture + manifest verification (offline, faked).

A fake backend yields deterministic raw outputs; capture must write them
byte-exactly, produce a canonical manifest binding every file to its hashes and
commands, and verification must pass on the intact set.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.fixture_capture import (
    FIXTURE_SCHEMA_VERSION,
    capture_live_fixture_set,
    verify_fixture_manifest,
)
from verifiednet.labs.frr.topologies import (
    PINNED_FRR_IMAGE,
    PINNED_FRR_IMAGE_ARM64_DIGEST,
    two_router_frr_topology,
)
from verifiednet.runtime.invocation import CommandInvocation
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.runtime.transcript import InMemoryTranscript

pytestmark = pytest.mark.unit

VERSION_OUT = "FRRouting 8.4.1_git (router) on Linux(6.12.54-linuxkit).\n"


class FakeCaptureBackend:
    """Deterministic stand-in for FrrComposeBackend during capture tests."""

    def __init__(self) -> None:
        self.transcript = InMemoryTranscript()
        self._seq = 0

    def capture_environment_metadata(self) -> dict[str, str]:
        return {"container_runtime": "docker", "image_reference": PINNED_FRR_IMAGE}

    def execute_readonly(
        self, target: str, argv: Sequence[str], timeout_s: float
    ) -> ExecResult:
        self._seq += 1
        command = argv[-1]
        stdout = (
            VERSION_OUT
            if command == "show version"
            else f"OUTPUT[{target}][{command}]\n"
        )
        transport = ("docker", "compose", "exec", "-T", target, *argv)
        invocation = CommandInvocation(
            command_id=f"cmd-{self._seq:016d}",
            target=target,
            logical_argv=tuple(argv),
            transport_argv=transport,
        )
        return ExecResult(
            status=ExecStatus.OK,
            target=target,
            argv=transport,
            exit_code=0,
            stdout=stdout,
            stderr="",
            duration_s=0.01,
            seq=self._seq,
            invocation=invocation,
        )


def capture(tmp_path: Path, run_ctx: RunContext) -> dict[str, object]:
    return capture_live_fixture_set(
        FakeCaptureBackend(),  # type: ignore[arg-type]
        two_router_frr_topology(image_ref=PINNED_FRR_IMAGE),
        run_ctx,
        tmp_path / "capture",
        platform_digest=PINNED_FRR_IMAGE_ARM64_DIGEST,
        extra_environment={"host_arch": "arm64", "host_os": "Darwin"},
        source_commit="4af05744ce81743f01e551e42091e06d3330618e",
    )


def test_capture_writes_expected_files_and_manifest(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    manifest = capture(tmp_path, run_ctx)
    out = tmp_path / "capture"
    expected = {
        "router_a_bgp_summary_established.json",
        "router_a_interfaces.json",
        "router_a_routes.json",
        "router_a_running_config.txt",
        "router_b_bgp_summary_established.json",
        "router_b_interfaces.json",
        "router_b_routes.json",
        "router_b_running_config.txt",
    }
    assert {p.name for p in out.iterdir()} == expected | {"manifest.json"}
    assert set(manifest["files"]) == expected  # type: ignore[arg-type]
    # raw output written byte-exactly
    raw = (out / "router_a_routes.json").read_text(encoding="utf-8")
    assert raw == "OUTPUT[router_a][show ip route json]\n"


def test_manifest_binds_full_provenance(tmp_path: Path, run_ctx: RunContext) -> None:
    manifest = capture(tmp_path, run_ctx)
    assert manifest["schema_version"] == FIXTURE_SCHEMA_VERSION
    assert manifest["frr_version"] == "8.4.1_git"
    assert manifest["image_reference"] == PINNED_FRR_IMAGE
    assert manifest["manifest_list_digest"] == PINNED_FRR_IMAGE.split("@", 1)[1]
    assert manifest["platform_digest"] == PINNED_FRR_IMAGE_ARM64_DIGEST
    env = manifest["environment"]
    assert env["host_arch"] == "arm64" and env["container_runtime"] == "docker"  # type: ignore[index]
    assert manifest["topology_sha256"]
    assert manifest["source_commit"].startswith("4af0574")  # type: ignore[union-attr]
    assert manifest["captured_at"].startswith("2026-")  # type: ignore[union-attr]
    statements = manifest["statements"]
    assert statements["produced_from_live_two_router_healthy_lab"] is True  # type: ignore[index]
    assert statements["no_mutation_command_executed"] is True  # type: ignore[index]
    files = manifest["files"]
    entry = files["router_b_bgp_summary_established.json"]  # type: ignore[index]
    assert entry["logical_argv"] == ["vtysh", "-c", "show ip bgp summary json"]
    assert entry["transport_argv"][:2] == ["docker", "compose"]
    assert entry["target"] == "router_b"
    assert len(entry["sha256"]) == 64


def test_manifest_is_canonical_json_on_disk(tmp_path: Path, run_ctx: RunContext) -> None:
    capture(tmp_path, run_ctx)
    text = (tmp_path / "capture" / "manifest.json").read_text(encoding="utf-8")
    data = json.loads(text)
    # canonical form: sorted keys, compact separators (via common.canonical)
    assert list(data) == sorted(data)
    assert list(data["files"]) == sorted(data["files"])


def test_verify_passes_on_intact_capture(tmp_path: Path, run_ctx: RunContext) -> None:
    capture(tmp_path, run_ctx)
    assert verify_fixture_manifest(tmp_path / "capture") == []
