"""Failure-path tests for live fixture capture: every failure is loud.

Covers: command failure aborts capture, mutation taint aborts capture, fixture
write failure propagates, and manifest verification detects tampering (hash
mismatch), missing files, stray files, and missing provenance statements.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.fixture_capture import (
    FixtureCaptureError,
    capture_live_fixture_set,
    verify_fixture_manifest,
)
from verifiednet.labs.frr.topologies import (
    PINNED_FRR_IMAGE,
    PINNED_FRR_IMAGE_ARM64_DIGEST,
    two_router_frr_topology,
)
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.runtime.transcript import InMemoryTranscript, TranscriptEntry

pytestmark = pytest.mark.failure

VERSION_OUT = "FRRouting 8.4.1_git (router) on Linux(6.12.54-linuxkit).\n"


class FakeCaptureBackend:
    def __init__(self, *, fail_command: str | None = None) -> None:
        self.transcript = InMemoryTranscript()
        self._fail_command = fail_command
        self._seq = 0

    def capture_environment_metadata(self) -> dict[str, str]:
        return {"container_runtime": "docker"}

    def execute_readonly(
        self, target: str, argv: Sequence[str], timeout_s: float
    ) -> ExecResult:
        self._seq += 1
        command = argv[-1]
        failed = self._fail_command is not None and command == self._fail_command
        stdout = VERSION_OUT if command == "show version" else f"OUT[{target}][{command}]\n"
        return ExecResult(
            status=ExecStatus.NONZERO_EXIT if failed else ExecStatus.OK,
            target=target,
            argv=tuple(argv),
            exit_code=1 if failed else 0,
            stdout="" if failed else stdout,
            stderr="boom" if failed else "",
            duration_s=0.01,
            seq=self._seq,
        )


def capture_with(backend: FakeCaptureBackend, out_dir: Path, run_ctx: RunContext) -> dict:
    return capture_live_fixture_set(
        backend,  # type: ignore[arg-type]
        two_router_frr_topology(image_ref=PINNED_FRR_IMAGE),
        run_ctx,
        out_dir,
        platform_digest=PINNED_FRR_IMAGE_ARM64_DIGEST,
        extra_environment={},
        source_commit="deadbeef",
    )


def _mutation_entry() -> TranscriptEntry:
    return TranscriptEntry(
        seq=1,
        mode="mutation",
        stage="completed",
        target="router_a",
        argv=("vtysh", "-c", "configure terminal"),
        status="ok",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_command_failure_aborts_capture(tmp_path: Path, run_ctx: RunContext) -> None:
    backend = FakeCaptureBackend(fail_command="show ip route json")
    with pytest.raises(FixtureCaptureError, match="show ip route json"):
        capture_with(backend, tmp_path / "cap", run_ctx)


def test_mutation_tainted_transcript_aborts_capture(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    backend = FakeCaptureBackend()
    backend.transcript.append(_mutation_entry())
    with pytest.raises(FixtureCaptureError, match="mutation"):
        capture_with(backend, tmp_path / "cap", run_ctx)
    # capture refused BEFORE writing anything
    assert not (tmp_path / "cap").exists()


def test_transcript_without_entries_surface_aborts(
    tmp_path: Path, run_ctx: RunContext
) -> None:
    backend = FakeCaptureBackend()
    backend.transcript = object()  # type: ignore[assignment]
    with pytest.raises(FixtureCaptureError, match="cannot prove"):
        capture_with(backend, tmp_path / "cap", run_ctx)


def test_fixture_write_failure_propagates(tmp_path: Path, run_ctx: RunContext) -> None:
    blocker = tmp_path / "cap"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    with pytest.raises(OSError):
        capture_with(FakeCaptureBackend(), blocker, run_ctx)


def test_verify_detects_hash_mismatch(tmp_path: Path, run_ctx: RunContext) -> None:
    out = tmp_path / "cap"
    capture_with(FakeCaptureBackend(), out, run_ctx)
    (out / "router_a_routes.json").write_text("tampered\n", encoding="utf-8")
    problems = verify_fixture_manifest(out)
    assert any("sha256 mismatch for router_a_routes.json" in p for p in problems)


def test_verify_detects_missing_listed_file(tmp_path: Path, run_ctx: RunContext) -> None:
    out = tmp_path / "cap"
    capture_with(FakeCaptureBackend(), out, run_ctx)
    (out / "router_b_interfaces.json").unlink()
    problems = verify_fixture_manifest(out)
    assert any("listed file missing: router_b_interfaces.json" in p for p in problems)


def test_verify_detects_stray_file(tmp_path: Path, run_ctx: RunContext) -> None:
    out = tmp_path / "cap"
    capture_with(FakeCaptureBackend(), out, run_ctx)
    (out / "unexpected.json").write_text("{}", encoding="utf-8")
    problems = verify_fixture_manifest(out)
    assert any("stray unlisted file: unexpected.json" in p for p in problems)


def test_verify_detects_missing_statements(tmp_path: Path, run_ctx: RunContext) -> None:
    out = tmp_path / "cap"
    capture_with(FakeCaptureBackend(), out, run_ctx)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["statements"]["no_mutation_command_executed"] = False
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_fixture_manifest(out)
    assert any("no_mutation_command_executed" in p for p in problems)


def test_verify_reports_missing_manifest(tmp_path: Path) -> None:
    problems = verify_fixture_manifest(tmp_path)
    assert problems and "missing manifest" in problems[0]
