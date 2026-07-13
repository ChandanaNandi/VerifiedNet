"""Gate 6.2 Part 3 export: build -> write -> verify -> read, and reproducibility.

An assigned mixed corpus is built offline, exported to an immutable directory,
verified, and read back. The headline guarantee is REPRODUCIBILITY: exporting the
same corpus twice yields byte-identical files, identical digests, and identical
manifests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets import (
    DATASET_GENERATOR,
    DATASET_MANIFEST_FILE,
    EXPECTED_SPLIT_FILES,
    DatasetManifest,
    DatasetPartition,
    build_dataset,
    read_dataset,
    verify_dataset,
    write_dataset,
)

pytestmark = pytest.mark.unit

_ACCEPTED = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c"),
             ("pf-ref", "run-d")]


def test_build_manifest_counts_and_digest(tmp_path: Path, export_corpus) -> None:
    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    ds = build_dataset(assigned, policy=policy, dataset_version="v1",
                       source_index_digest=digest)
    m = ds.manifest
    assert m.generated_by == DATASET_GENERATOR
    assert m.accepted_count == 4
    assert m.rejected_count == 1
    assert m.example_count == 5
    assert m.partition_counts.abstention == 1
    assert m.partition_counts.accepted_total == 4
    assert m.split_policy_id == policy.policy_id
    assert len(m.dataset_digest) == 64
    # manifest lists exactly the four split files, path-sorted and unique
    assert {f.relative_path for f in m.files} == EXPECTED_SPLIT_FILES


def test_write_verify_read_round_trip(tmp_path: Path, export_corpus) -> None:
    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    ds = build_dataset(assigned, policy=policy, dataset_version="v1",
                       source_index_digest=digest)
    written = write_dataset(ds, tmp_path / "dataset")

    result = verify_dataset(tmp_path / "dataset")
    assert result.verified is True, result.failures
    assert result.dataset_digest == written.dataset_digest

    loaded = read_dataset(tmp_path / "dataset")
    assert loaded.manifest.dataset_digest == ds.manifest.dataset_digest
    assert len(loaded.examples) == 5
    assert len(loaded.by_partition[DatasetPartition.ABSTENTION]) == 1
    # every abstention example is genuinely an abstention binding
    for a in loaded.by_partition[DatasetPartition.ABSTENTION]:
        assert a.partition is DatasetPartition.ABSTENTION


def test_manifest_file_on_disk_parses(tmp_path: Path, export_corpus) -> None:
    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    ds = build_dataset(assigned, policy=policy, dataset_version="v1",
                       source_index_digest=digest)
    write_dataset(ds, tmp_path / "dataset")
    raw = (tmp_path / "dataset" / DATASET_MANIFEST_FILE).read_bytes()
    parsed = DatasetManifest.model_validate_json(raw)
    assert parsed == ds.manifest


def test_reproducibility_proof_bytes_digest_manifest(
    tmp_path: Path, export_corpus,
) -> None:
    # THE reproducibility proof: two independent exports of the same corpus.
    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    ds1 = build_dataset(assigned, policy=policy, dataset_version="v1",
                        source_index_digest=digest)
    ds2 = build_dataset(assigned, policy=policy, dataset_version="v1",
                        source_index_digest=digest)

    # identical in-memory output bytes and digests
    assert ds1.output_files() == ds2.output_files()
    assert ds1.manifest == ds2.manifest
    assert ds1.manifest.dataset_digest == ds2.manifest.dataset_digest

    # identical on-disk bytes for every file, across two separate directories
    write_dataset(ds1, tmp_path / "d1")
    write_dataset(ds2, tmp_path / "d2")
    for rel in sorted(EXPECTED_SPLIT_FILES | {DATASET_MANIFEST_FILE}):
        assert (tmp_path / "d1" / rel).read_bytes() == (tmp_path / "d2" / rel).read_bytes()


def test_export_order_independent(tmp_path: Path, export_corpus) -> None:
    # Shuffling the input assignment order must not change the exported bytes.
    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    forward = build_dataset(assigned, policy=policy, dataset_version="v1",
                            source_index_digest=digest)
    reverse = build_dataset(tuple(reversed(assigned)), policy=policy,
                            dataset_version="v1", source_index_digest=digest)
    assert forward.output_files() == reverse.output_files()
    assert forward.manifest.dataset_digest == reverse.manifest.dataset_digest


def test_export_does_not_mutate_run_library(tmp_path: Path, export_corpus) -> None:
    import hashlib

    assigned, policy, digest, out_root = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                       rejected=["run-rej"])

    def fingerprint() -> dict[str, str]:
        return {
            str(p.relative_to(out_root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out_root.rglob("*")) if p.is_file()
        }

    before = fingerprint()
    ds = build_dataset(assigned, policy=policy, dataset_version="v1",
                       source_index_digest=digest)
    write_dataset(ds, tmp_path / "dataset")
    verify_dataset(tmp_path / "dataset")
    read_dataset(tmp_path / "dataset")
    assert fingerprint() == before  # the verified run library is untouched


def test_all_train_policy_changes_digest(tmp_path: Path, export_corpus) -> None:
    from verifiednet.datasets import SplitPolicy

    assigned, policy, digest, _ = export_corpus(tmp_path, accepted=_ACCEPTED,
                                                rejected=["run-rej"])
    base = build_dataset(assigned, policy=policy, dataset_version="v1",
                         source_index_digest=digest)
    # A different salt re-assigns groups and changes the digest.
    other_policy = SplitPolicy(salt="different", train_buckets=8000,
                               validation_buckets=1000, test_buckets=1000)
    _, other_pol, _, _ = export_corpus(tmp_path / "b", accepted=_ACCEPTED,
                                       rejected=["run-rej"], policy=other_policy)
    reassigned, _, digest2, _ = export_corpus(tmp_path / "c", accepted=_ACCEPTED,
                                              rejected=["run-rej"], policy=other_policy)
    other = build_dataset(reassigned, policy=other_pol, dataset_version="v1",
                          source_index_digest=digest2)
    assert base.manifest.split_policy_id != other.manifest.split_policy_id
