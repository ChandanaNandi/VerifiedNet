"""Live reverse-orientation spot-checks (Gate 5.5/5.6): all four families on router_b.

One reverse-orientation catalog case per family, executed live through
``run_accepted_case`` into ONE shared run index, proving the abstractions are
orientation-independent (no ``router_a`` assumption remains). The existing
``router_a`` reference cases stay covered by the per-family live tests; this
adds the reverse direction plus a shared-index + cleanup proof in one run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.artifacts import load_run_index, load_verified_run_from_index, verify_run_index
from verifiednet.common.hashing import sha256_file
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LifecyclePhase
from verifiednet.labs.frr.compose_project import project_name_for_run
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.orchestrator import run_accepted_case
from verifiednet.orchestrator.catalog import case_by_id
from verifiednet.runtime.process import default_runner

pytestmark = pytest.mark.integration

# One reverse-orientation (router_b) case per family.
_REVERSE_CASES = ("ras-rev", "nr-rev", "if-rev", "pf-rev")


def _git_rev() -> str:
    return default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip() or "unknown"


def test_reverse_orientation_all_families_live(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    commit = _git_rev()
    lock = Path("uv.lock")
    lock_hash = sha256_file(lock) if lock.is_file() else "0" * 64
    out_root = tmp_path / "runs"  # ONE shared index for all reverse cases

    run_ids: dict[str, str] = {}
    for case_id in _REVERSE_CASES:
        case = case_by_id(case_id)
        run_id = unique_run_id(f"it-rev-{case_id}")
        project = project_name_for_run(run_id)
        result = run_accepted_case(
            case=case,
            out_root=out_root,
            work_dir=tmp_path / case_id,
            run_ctx=RunContext(run_id),
            topology=topology,
            git_rev=commit,
            lock_hash=lock_hash,
            convergence_timeout_s=60.0,
        )
        run_ids[case_id] = result.assembled.run_id

        # per-case deterministic proof
        record = result.assembled.loaded.incident
        assert record.status == "accepted"
        assert record.scenario.template_id == case.template_id
        assert str(record.scenario.parameters["target_node"]) == "router_b"
        assert record.ground_truth is not None
        assert record.ground_truth.root_cause_label == case.template_id
        assert record.fault is not None and record.fault.target_node == "router_b"
        assert result.assembled.loaded.ledger[-1].phase is LifecyclePhase.RECOVERY_VERIFIED
        # every pending mutation is paired; the peer (router_a) is never mutated
        muts = [e for e in result.assembled.loaded.transcript if e.mode == "mutation"]
        pend = [e for e in muts if e.stage == "pending"]
        done = [e for e in muts if e.stage == "completed"]
        assert len(pend) == len(done)
        assert all(e.target == "router_b" for e in muts)
        # per-case isolation: zero project-labeled resources after teardown
        assert project_containers(project) == []
        assert project_networks(project) == []

    # shared index holds all four reverse runs, each reload-verified
    index = load_run_index(out_root)
    assert {e.run_id for e in index.entries} == set(run_ids.values())
    assert verify_run_index(out_root).verified is True
    templates = {e.run_id: e.template_id for e in index.entries}
    for case_id, run_id in run_ids.items():
        assert templates[run_id] == case_by_id(case_id).template_id
        assert load_verified_run_from_index(out_root, run_id).incident.status == "accepted"
