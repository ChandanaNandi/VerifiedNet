"""Capture the live FRR healthy-lab fixture set (Gate 4 Step 2, run manually).

Boots the approved pinned two-router lab, waits for real BGP convergence,
captures raw read-only outputs + provenance manifest into
``tests/fixtures/frr/live/frr-<version>-<platform>/``, then tears the lab down
and verifies zero resources. Run from the repository root on a Docker host:

    uv run python scripts/capture_live_frr_fixtures.py

The script never executes a mutation command; capture aborts if the transcript
contains one. Existing provisional fixtures under tests/fixtures/frr/ are not
touched.
"""

from __future__ import annotations

import platform
import sys
import tempfile
import time
from pathlib import Path

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.convergence import wait_for_bgp_established
from verifiednet.labs.frr.fixture_capture import capture_live_fixture_set
from verifiednet.labs.frr.topologies import (
    PINNED_FRR_IMAGE,
    PINNED_FRR_IMAGE_ARM64_DIGEST,
    two_router_frr_topology,
)
from verifiednet.runtime.process import default_runner

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "frr" / "live" / "frr-8.4.1-linux-arm64"


def _query(argv: list[str]) -> str:
    result = default_runner(argv, 10.0, 65536)
    if result.exit_code != 0:
        raise SystemExit(f"query failed: {argv!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    source_commit = _query(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
    docker_server_arch = _query(["docker", "version", "--format", "{{.Server.Arch}}"])
    run_id = f"fixcap-{int(time.time())}"
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_ctx = RunContext(run_id)
    work_dir = Path(tempfile.mkdtemp(prefix="vn_fixcap_"))
    backend = FrrComposeBackend(topology, run_ctx, work_dir=work_dir)
    print(f"run_id={run_id} project={backend.project_name}")
    try:
        backend.start()
        print("lab started; waiting for BGP convergence…")
        report = wait_for_bgp_established(backend.readonly_executor, topology)
        print(
            f"converged: attempts={report.attempts} elapsed={report.elapsed_s:.1f}s "
            f"states={report.last_states}"
        )
        manifest = capture_live_fixture_set(
            backend,
            topology,
            run_ctx,
            FIXTURE_DIR,
            platform_digest=PINNED_FRR_IMAGE_ARM64_DIGEST,
            extra_environment={
                "host_arch": platform.machine(),
                "host_os": platform.system(),
                "host_kernel": platform.release(),
                "docker_server_arch": docker_server_arch,
            },
            source_commit=source_commit,
        )
        print(f"captured {len(manifest['files'])} files -> {FIXTURE_DIR}")
    finally:
        backend.stop()
        print("lab stopped; cleanup verified (zero containers/networks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
