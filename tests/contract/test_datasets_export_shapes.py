"""Contract tests: Gate 6.2 Part 3 export models round-trip and stay frozen."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.common.hashing import sha256_bytes
from verifiednet.datasets.export import EXPECTED_SPLIT_FILES
from verifiednet.datasets.models import (
    DATASET_EXPORT_VERSION,
    DATASET_GENERATOR,
    DatasetFileHash,
    DatasetManifest,
    DatasetPartitionCounts,
    SplitPolicy,
    compute_dataset_digest,
)
from verifiednet.datasets.verifier import DatasetCheck, DatasetVerificationResult

pytestmark = pytest.mark.contract

_POLICY = SplitPolicy(salt="s", train_buckets=8000, validation_buckets=1000,
                      test_buckets=1000)


def _files() -> tuple[DatasetFileHash, ...]:
    return tuple(sorted(
        (DatasetFileHash(relative_path=p, sha256=sha256_bytes(p.encode()), size=len(p))
         for p in EXPECTED_SPLIT_FILES),
        key=lambda f: f.relative_path,
    ))


def _manifest(**overrides) -> DatasetManifest:
    counts = DatasetPartitionCounts(train=2, validation=1, test=1, abstention=1)
    files = _files()
    fields = {
        "dataset_version": "v1",
        "generated_by": DATASET_GENERATOR,
        "source_index_digest": "a" * 64,
        "split_policy": _POLICY,
        "split_policy_id": _POLICY.policy_id,
        "accepted_count": 4,
        "rejected_count": 1,
        "example_count": 5,
        "partition_counts": counts,
        "files": files,
    }
    fields.update(overrides)
    digest = compute_dataset_digest(
        schema_version=1, export_version=DATASET_EXPORT_VERSION,
        dataset_version=fields["dataset_version"], generated_by=fields["generated_by"],
        source_index_digest=fields["source_index_digest"],
        split_policy_id=fields["split_policy_id"],
        partition_counts=fields["partition_counts"], files=fields["files"],
    )
    return DatasetManifest(dataset_digest=digest, **fields)


def test_manifest_round_trip_and_frozen() -> None:
    m = _manifest()
    assert DatasetManifest.model_validate_json(m.model_dump_json()) == m
    with pytest.raises(ValidationError):
        m.accepted_count = 9  # frozen
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(m.model_dump() | {"surprise": 1})  # extra forbid


def test_manifest_rejects_wrong_digest() -> None:
    m = _manifest()
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(m.model_dump() | {"dataset_digest": "0" * 64})


def test_manifest_rejects_count_inconsistency() -> None:
    with pytest.raises(ValidationError):
        _manifest(accepted_count=3)  # 3 + 1 != example_count 5 and != partition total


def test_manifest_rejects_policy_id_mismatch() -> None:
    with pytest.raises(ValidationError):
        _manifest(split_policy_id="split-0000000000000000")


def test_file_hash_validation() -> None:
    good = DatasetFileHash(relative_path="splits/train.jsonl", sha256="a" * 64, size=0)
    assert DatasetFileHash.model_validate_json(good.model_dump_json()) == good
    with pytest.raises(ValidationError):
        DatasetFileHash(relative_path="/abs", sha256="a" * 64, size=0)
    with pytest.raises(ValidationError):
        DatasetFileHash(relative_path="splits/x", sha256="nothex", size=0)
    with pytest.raises(ValidationError):
        DatasetFileHash(relative_path="splits/x", sha256="a" * 64, size=-1)


def test_partition_counts_round_trip() -> None:
    c = DatasetPartitionCounts(train=3, validation=1, test=1, abstention=2)
    assert DatasetPartitionCounts.model_validate_json(c.model_dump_json()) == c
    assert c.accepted_total == 5
    assert c.total == 7


def test_verification_result_round_trip() -> None:
    checks = (
        DatasetCheck(rule="manifest_present", passed=True),
        DatasetCheck(rule="file_hashes_match", passed=False, detail="bad"),
    )
    result = DatasetVerificationResult(verified=False, dataset_digest="a" * 64,
                                       checks=checks)
    assert DatasetVerificationResult.model_validate_json(result.model_dump_json()) == result
    assert len(result.failures) == 1
    assert result.failures[0].rule == "file_hashes_match"


def test_manifest_schema_and_export_versions_are_exact() -> None:
    m = _manifest()
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(m.model_dump() | {"schema_version": 2})
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(m.model_dump() | {"export_version": 2})
