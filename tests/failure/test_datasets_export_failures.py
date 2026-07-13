"""Gate 6.2 Part 3 export failures: corruption is rejected loudly, fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets import (
    DATASET_INCOMPLETE_MARKER,
    DATASET_MANIFEST_FILE,
    AssignedDatasetExample,
    DatasetPartition,
    build_dataset,
    project_verified_run,
    read_dataset,
    split_policy_id,
    verify_dataset,
    write_dataset,
)
from verifiednet.datasets.discovery import discover_verified_runs
from verifiednet.datasets.export import DatasetExportError
from verifiednet.datasets.reader import DatasetReadError
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.failure

_ACCEPTED = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _written(tmp_path: Path, export_corpus) -> Path:
    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    ds = build_dataset(assigned, policy=policy, dataset_version="v1",
                       source_index_digest=digest)
    write_dataset(ds, tmp_path / "dataset")
    return tmp_path / "dataset"


def test_corrupted_split_file_is_rejected(tmp_path: Path, export_corpus) -> None:
    root = _written(tmp_path, export_corpus)
    victim = root / "splits" / "abstention.jsonl"
    victim.write_bytes(victim.read_bytes() + b" ")
    result = verify_dataset(root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    with pytest.raises(DatasetReadError):
        read_dataset(root)


def test_corrupted_manifest_is_rejected(tmp_path: Path, export_corpus) -> None:
    root = _written(tmp_path, export_corpus)
    manifest = root / DATASET_MANIFEST_FILE
    text = manifest.read_text().replace('"accepted_count":2', '"accepted_count":3')
    assert '"accepted_count":3' in text  # the substitution actually happened
    manifest.write_text(text)
    result = verify_dataset(root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)
    with pytest.raises(DatasetReadError):
        read_dataset(root)


def test_tampered_digest_is_rejected(tmp_path: Path, export_corpus) -> None:
    root = _written(tmp_path, export_corpus)
    manifest = root / DATASET_MANIFEST_FILE
    import json

    data = json.loads(manifest.read_text())
    data["dataset_digest"] = "0" * 64
    manifest.write_text(json.dumps(data))
    # The self-validating manifest refuses to parse with a wrong digest.
    result = verify_dataset(root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)


def test_missing_split_file_is_rejected(tmp_path: Path, export_corpus) -> None:
    root = _written(tmp_path, export_corpus)
    (root / "splits" / "test.jsonl").unlink()
    result = verify_dataset(root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)
    with pytest.raises(DatasetReadError):
        read_dataset(root)


def test_unexpected_file_is_rejected(tmp_path: Path, export_corpus) -> None:
    root = _written(tmp_path, export_corpus)
    (root / "splits" / "extra.jsonl").write_bytes(b"")
    result = verify_dataset(root)
    assert result.verified is False
    assert any(c.rule == "no_unexpected_files" for c in result.failures)


def test_incomplete_marker_blocks_read(tmp_path: Path, export_corpus) -> None:
    root = _written(tmp_path, export_corpus)
    (root / DATASET_INCOMPLETE_MARKER).write_bytes(b"incomplete\n")
    result = verify_dataset(root)
    assert result.verified is False
    assert any(c.rule == "incomplete_marker_absent" for c in result.failures)
    with pytest.raises(DatasetReadError):
        read_dataset(root)


def test_unsupported_export_version_does_not_parse(
    tmp_path: Path, export_corpus,
) -> None:
    root = _written(tmp_path, export_corpus)
    manifest = root / DATASET_MANIFEST_FILE
    import json

    data = json.loads(manifest.read_text())
    data["export_version"] = 2  # Literal[1] -> refuses to parse
    manifest.write_text(json.dumps(data))
    result = verify_dataset(root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    result = verify_dataset(tmp_path / "nope")
    assert result.verified is False
    assert any(c.rule == "dataset_dir_present" for c in result.failures)
    with pytest.raises(DatasetReadError):
        read_dataset(tmp_path / "nope")


def test_build_rejects_duplicate_example(tmp_path: Path, export_corpus) -> None:
    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    with pytest.raises(DatasetExportError):
        build_dataset((*assigned, assigned[0]), policy=policy, dataset_version="v1",
                      source_index_digest=digest)


def test_build_rejects_leaky_corpus(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    # Two runs of one scenario forced into different partitions -> leak; export
    # must refuse to build.
    out_root = tmp_path / "runs"
    for run_id in ("run-1", "run-2"):
        run_catalog_case(case_by_id("ras-ref"), out_root, tmp_path, run_id=run_id,
                         sim=catalog_sim_cls())
    e1, e2 = sorted(
        (project_verified_run(d) for d in discover_verified_runs(out_root)),
        key=lambda e: e.run_id,
    )
    assert e1.group_id == e2.group_id
    from verifiednet.datasets import SplitPolicy

    policy = SplitPolicy(salt="s", train_buckets=8000, validation_buckets=1000,
                         test_buckets=1000)
    pid = split_policy_id(policy)
    leaky = (
        AssignedDatasetExample(example=e1, partition=DatasetPartition.TRAIN,
                               split_policy_id=pid),
        AssignedDatasetExample(example=e2, partition=DatasetPartition.TEST,
                               split_policy_id=pid),
    )
    with pytest.raises(DatasetExportError):
        build_dataset(leaky, policy=policy, dataset_version="v1",
                      source_index_digest="a" * 64)


def test_writer_refuses_non_empty_target(tmp_path: Path, export_corpus) -> None:
    from verifiednet.datasets.writer import DatasetWriteError

    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    ds = build_dataset(assigned, policy=policy, dataset_version="v1",
                       source_index_digest=digest)
    target = tmp_path / "dataset"
    target.mkdir()
    (target / "sentinel").write_bytes(b"x")
    with pytest.raises(DatasetWriteError):
        write_dataset(ds, target)
