"""Structured verifier for an exported dataset (Gate 6.2 Part 3).

``verify_dataset`` re-derives every integrity property of an exported dataset
from its bytes and returns a STRUCTURED result (never a bare bool, never a raised
exception for a verification failure). It confirms: the manifest parses (which
already re-checks the self-validating ``dataset_digest`` and the counts), the
schema/export versions are supported, exactly the expected files are present
(no missing, no unexpected), every content file's on-disk bytes match the
manifest hash, the digest re-derives, each split file holds only its partition,
the reconstructed counts match the manifest, and no ``example_id``/``run_id`` is
duplicated across the corpus.

Read-only: it never writes, never mutates a verified run, and never executes.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes
from verifiednet.datasets.export import (
    DATASET_INCOMPLETE_MARKER,
    DATASET_MANIFEST_FILE,
    EXPECTED_SPLIT_FILES,
    SPLIT_FILE_BY_PARTITION,
    parse_split_bytes,
)
from verifiednet.datasets.models import (
    DatasetManifest,
    DatasetPartition,
    compute_dataset_digest,
)
from verifiednet.schemas.base import StrictModel

#: Schema/export versions this verifier is built to consume (refuse, never coerce).
SUPPORTED_DATASET_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_DATASET_EXPORT: frozenset[int] = frozenset({1})


class DatasetCheck(StrictModel):
    """One named verification outcome."""

    schema_version: Literal[1] = 1
    rule: str
    passed: bool
    detail: str = ""


class DatasetVerificationResult(StrictModel):
    """Structured dataset integrity result (not a bool)."""

    schema_version: Literal[1] = 1
    verified: bool
    dataset_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def _result(checks: list[DatasetCheck], digest: str | None) -> DatasetVerificationResult:
    return DatasetVerificationResult(
        verified=all(c.passed for c in checks),
        dataset_digest=digest,
        checks=tuple(checks),
    )


def verify_dataset(dataset_dir: str | Path) -> DatasetVerificationResult:
    """Verify an exported dataset directory; return a structured result."""
    root = Path(dataset_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("dataset_dir_present", False, f"not a directory: {root}"))
        return _result(checks, None)
    checks.append(_c("dataset_dir_present", True))

    marker_absent = not (root / DATASET_INCOMPLETE_MARKER).exists()
    checks.append(_c("incomplete_marker_absent", marker_absent,
                    "" if marker_absent else ".INCOMPLETE present"))

    manifest_path = root / DATASET_MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False, f"missing {DATASET_MANIFEST_FILE}"))
        return _result(checks, None)
    checks.append(_c("manifest_present", True))

    try:
        manifest = DatasetManifest.model_validate_json(manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return _result(checks, None)
    checks.append(_c("manifest_parses", True))
    digest = manifest.dataset_digest

    schema_ok = manifest.schema_version in SUPPORTED_DATASET_SCHEMA
    export_ok = manifest.export_version in SUPPORTED_DATASET_EXPORT
    checks.append(_c("schema_supported", schema_ok,
                    "" if schema_ok else f"schema_version {manifest.schema_version}"))
    checks.append(_c("export_supported", export_ok,
                    "" if export_ok else f"export_version {manifest.export_version}"))

    # The manifest must list exactly the expected split files.
    listed = {f.relative_path for f in manifest.files}
    checks.append(_c("manifest_lists_expected_files", listed == EXPECTED_SPLIT_FILES,
                    "" if listed == EXPECTED_SPLIT_FILES else f"listed={sorted(listed)}"))

    # Exactly manifest.json plus the expected split files exist on disk. The
    # ``.INCOMPLETE`` control marker is handled by its own check above and is not
    # counted as content here (so an in-progress write is flagged by that check,
    # not by a spurious "unexpected file").
    on_disk = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.name != DATASET_INCOMPLETE_MARKER
    }
    allowed = EXPECTED_SPLIT_FILES | {DATASET_MANIFEST_FILE}
    missing = sorted(allowed - on_disk)
    unexpected = sorted(on_disk - allowed)
    checks.append(_c("no_missing_files", not missing,
                    "" if not missing else f"missing={missing}"))
    checks.append(_c("no_unexpected_files", not unexpected,
                    "" if not unexpected else f"unexpected={unexpected}"))

    # Per-file hash + size match the manifest.
    hash_ok = True
    hash_detail = ""
    for fh in manifest.files:
        fpath = root / fh.relative_path
        if not fpath.is_file():
            hash_ok = False
            hash_detail = f"missing {fh.relative_path}"
            break
        raw = fpath.read_bytes()
        if len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok = False
            hash_detail = f"hash/size mismatch for {fh.relative_path}"
            break
    checks.append(_c("file_hashes_match", hash_ok, hash_detail))

    # Independent digest re-derivation (defence in depth beyond the model check).
    recomputed = compute_dataset_digest(
        schema_version=manifest.schema_version,
        export_version=manifest.export_version,
        dataset_version=manifest.dataset_version,
        generated_by=manifest.generated_by,
        source_index_digest=manifest.source_index_digest,
        split_policy_id=manifest.split_policy_id,
        partition_counts=manifest.partition_counts,
        files=manifest.files,
    )
    checks.append(_c("dataset_digest_matches", recomputed == manifest.dataset_digest,
                    "" if recomputed == manifest.dataset_digest else "digest mismatch"))

    # Reconstruct examples; confirm partition placement, counts, and uniqueness.
    counts: dict[DatasetPartition, int] = defaultdict(int)
    seen_examples: set[str] = set()
    seen_runs: set[str] = set()
    reconstruct_ok = True
    reconstruct_detail = ""
    partition_placement_ok = True
    for part, rel in SPLIT_FILE_BY_PARTITION.items():
        fpath = root / rel
        if not fpath.is_file():
            reconstruct_ok = False
            reconstruct_detail = f"missing {rel}"
            break
        try:
            examples = parse_split_bytes(fpath.read_bytes())
        except (VerifiedNetError, ValidationError) as exc:
            reconstruct_ok = False
            reconstruct_detail = f"{rel}: {str(exc).splitlines()[0]}"
            break
        for a in examples:
            if a.partition is not part:
                partition_placement_ok = False
            if a.example.example_id in seen_examples:
                reconstruct_ok = False
                reconstruct_detail = f"duplicate example_id {a.example.example_id}"
            if a.example.run_id in seen_runs:
                reconstruct_ok = False
                reconstruct_detail = f"duplicate run_id {a.example.run_id}"
            seen_examples.add(a.example.example_id)
            seen_runs.add(a.example.run_id)
            counts[part] += 1
    checks.append(_c("split_files_parse", reconstruct_ok, reconstruct_detail))
    checks.append(_c("partition_placement", partition_placement_ok,
                    "" if partition_placement_ok else "example in wrong split file"))

    if reconstruct_ok:
        pc = manifest.partition_counts
        counts_ok = (
            counts[DatasetPartition.TRAIN] == pc.train
            and counts[DatasetPartition.VALIDATION] == pc.validation
            and counts[DatasetPartition.TEST] == pc.test
            and counts[DatasetPartition.ABSTENTION] == pc.abstention
        )
        checks.append(_c("counts_match_manifest", counts_ok,
                        "" if counts_ok else "reconstructed counts differ"))

    return _result(checks, digest)
