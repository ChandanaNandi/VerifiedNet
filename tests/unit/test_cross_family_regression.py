"""Gate 5.6 cross-family regression: isolation, repeatability, shared index.

Offline (deterministic sim). Runs catalog cases sequentially with a FRESH lab
per case, proving no state carries across cases, that truth-bearing outputs are
repeatable across two run_ids, and that a tampered run invalidates the index
while untouched runs still verify independently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.artifacts import (
    ArtifactIntegrityError,
    load_run,
    load_verified_run_from_index,
    verify_run_dir,
    verify_run_index,
)
from verifiednet.faults.ledger import LifecyclePhase
from verifiednet.orchestrator import SCENARIO_CATALOG
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.unit


def test_sequential_isolation_fresh_lab_per_case(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    out_root = tmp_path / "runs"
    for i, case in enumerate(SCENARIO_CATALOG):
        sim = catalog_sim_cls()  # fresh lab ownership per case
        result = run_catalog_case(case, out_root, tmp_path, run_id=f"iso-{i}-{case.case_id}",
                                  sim=sim)
        loaded = result.assembled.loaded
        assert loaded.ledger[-1].phase is LifecyclePhase.RECOVERY_VERIFIED
        pend = [e for e in loaded.transcript if e.mode == "mutation" and e.stage == "pending"]
        done = [e for e in loaded.transcript if e.mode == "mutation" and e.stage == "completed"]
        assert len(pend) == len(done)
        # lab returned to full health after each case; no state carried forward
        assert sim._session_up is True
        assert sim.a.eth1_up and sim.b.eth1_up
        assert sim.a.has_neighbor and sim.b.has_neighbor
        assert sim.a.advertised and sim.b.advertised
    assert verify_run_index(out_root).verified is True


def test_repeatability_same_truth_bearing_outputs(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    for case_id in ("ras-ref", "nr-ref", "if-ref", "pf-ref"):
        case = case_by_id(case_id)
        r1 = run_catalog_case(case, tmp_path / "a", tmp_path, run_id=f"{case_id}-a",
                              sim=catalog_sim_cls())
        r2 = run_catalog_case(case, tmp_path / "b", tmp_path, run_id=f"{case_id}-b",
                              sim=catalog_sim_cls())
        inc1, inc2 = r1.assembled.loaded.incident, r2.assembled.loaded.incident

        def truth(inc):
            assert inc.ground_truth is not None
            assert inc.fault is not None
            return {
                "template": inc.scenario.template_id,
                "root_cause": inc.ground_truth.root_cause_label,
                "target": str(inc.scenario.parameters["target_node"]),
                "fault": (inc.fault.parameter_name, inc.fault.before_value, inc.fault.after_value),
                "checks": sorted(v.check_id for v in inc.ground_truth.verdicts),
                "verdicts": sorted((v.check_id, v.verdict.value, v.observed)
                                   for v in inc.ground_truth.verdicts),
                "forced_reset": inc.restoration.forced_reset_used if inc.restoration else None,
            }

        assert truth(inc1) == truth(inc2), case_id
        # run digests DO differ (run-local ids/timestamps) — never byte identity
        assert r1.assembled.run_digest != r2.assembled.run_digest


def test_tamper_one_run_isolates_from_others(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    out_root = tmp_path / "runs"
    victim = run_catalog_case(case_by_id("ras-ref"), out_root, tmp_path, run_id="run-victim",
                              sim=catalog_sim_cls())
    healthy = run_catalog_case(case_by_id("pf-rev"), out_root, tmp_path, run_id="run-healthy",
                               sim=catalog_sim_cls())
    assert verify_run_index(out_root).verified is True

    # tamper the victim's incident; index verification must fail...
    path = out_root / victim.assembled.run_id / "incident.json"
    path.write_bytes(path.read_bytes() + b" ")
    assert verify_run_index(out_root).verified is False
    # ...but the untouched run still verifies + loads independently on its own dir
    assert verify_run_dir(out_root / healthy.assembled.run_id).verified is True
    assert load_run(out_root / healthy.assembled.run_id).incident.status == "accepted"
    # loading the tampered run through the index raises
    with pytest.raises(ArtifactIntegrityError):
        load_verified_run_from_index(out_root, victim.assembled.run_id)
