"""Gate 6.1 discovery integrity gate: corruption is rejected loudly (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.artifacts.layout import INCOMPLETE_MARKER
from verifiednet.datasets import DatasetDiscoveryError, discover_verified_runs
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.failure


def _one_run(tmp_path: Path, run_catalog_case, catalog_sim_cls, run_id: str = "run-1") -> Path:
    out_root = tmp_path / "runs"
    run_catalog_case(case_by_id("ras-ref"), out_root, tmp_path, run_id=run_id,
                     sim=catalog_sim_cls())
    return out_root


def test_healthy_library_discovers(tmp_path: Path, run_catalog_case, catalog_sim_cls) -> None:
    out_root = _one_run(tmp_path, run_catalog_case, catalog_sim_cls)
    assert len(list(discover_verified_runs(out_root))) == 1


def test_tampered_incident_is_rejected(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    out_root = _one_run(tmp_path, run_catalog_case, catalog_sim_cls)
    victim = out_root / "run-1" / "incident.json"
    victim.write_bytes(victim.read_bytes() + b" ")
    with pytest.raises(DatasetDiscoveryError):
        list(discover_verified_runs(out_root))


def test_incomplete_run_is_rejected(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    out_root = _one_run(tmp_path, run_catalog_case, catalog_sim_cls)
    (out_root / "run-1" / INCOMPLETE_MARKER).write_text("")
    with pytest.raises(DatasetDiscoveryError):
        list(discover_verified_runs(out_root))


def test_unindexed_run_directory_is_rejected(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    # Two runs written, but only the first is indexed: a second run dir carrying
    # a hashes.json that the index does not reference is a hidden run and the
    # whole-index verification (inside discovery) must refuse it.
    out_root = tmp_path / "runs"
    run_catalog_case(case_by_id("ras-ref"), out_root, tmp_path, run_id="indexed",
                     sim=catalog_sim_cls())
    # materialize a second run into a DIFFERENT index root, then move its dir in
    other = tmp_path / "other"
    run_catalog_case(case_by_id("nr-ref"), other, tmp_path, run_id="orphan",
                     sim=catalog_sim_cls())
    import shutil

    shutil.copytree(other / "orphan", out_root / "orphan")
    with pytest.raises(DatasetDiscoveryError):
        list(discover_verified_runs(out_root))


def test_missing_index_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DatasetDiscoveryError):
        list(discover_verified_runs(tmp_path / "does-not-exist"))
