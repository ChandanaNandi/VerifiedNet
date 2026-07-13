"""Discovery — yield ONLY verified, immutable runs from the run index (Gate 6.1).

Read-only. Every run is re-verified before it is yielded; any failure raises
loudly (never a warning, never a silent skip). This is the integrity gate that
guarantees the dataset engine consumes only trustworthy inputs. It never writes,
never mutates a run, never contacts Docker, and never re-derives truth.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from verifiednet.artifacts import load_run, verify_run_dir, verify_run_index
from verifiednet.artifacts.index import load_run_index
from verifiednet.artifacts.reader import LoadedRun
from verifiednet.common.errors import VerifiedNetError

#: Schema versions the Gate 6.1 dataset engine is built to consume. A run
#: outside this set is refused loudly, never silently coerced.
SUPPORTED_INCIDENT_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_LAYOUT_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_GROUND_TRUTH_SCHEMA: frozenset[int] = frozenset({1})


class DatasetDiscoveryError(VerifiedNetError):
    """A run failed the discovery integrity gate (corruption, mismatch, dup)."""


@dataclass(frozen=True)
class DiscoveredRun:
    """A verified run plus the index-recorded identity it was matched against."""

    loaded: LoadedRun
    indexed_run_digest: str
    source_index_digest: str


def discover_verified_runs(index_root: str | Path) -> Iterator[DiscoveredRun]:
    """Yield every verified run in *index_root*, or raise on the first failure.

    Pipeline (all mandatory): verify the whole index → for each entry, verify
    the run directory, re-load it (refusing ``.INCOMPLETE`` and re-checking every
    hash), confirm the recomputed ``run_digest`` equals the indexed value, reject
    duplicate ``run_id``/``run_digest``, and confirm schema compatibility.
    """
    index_root = Path(index_root)

    index_result = verify_run_index(index_root)
    if not index_result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in index_result.failures)
        raise DatasetDiscoveryError(f"run index failed verification: {detail}")
    source_index_digest = index_result.index_digest

    index = load_run_index(index_root)
    seen_run_ids: set[str] = set()
    seen_digests: set[str] = set()

    for entry in index.entries:
        run_dir = index_root / entry.run_dir

        # Independent per-run verification (also refuses .INCOMPLETE).
        dir_result = verify_run_dir(run_dir)
        if not dir_result.verified:
            fails = "; ".join(f"{c.rule}: {c.detail}" for c in dir_result.failures)
            raise DatasetDiscoveryError(f"run {entry.run_id} failed verification: {fails}")

        # Recompute + digest equality against the indexed value.
        if dir_result.run_digest != entry.run_digest:
            raise DatasetDiscoveryError(
                f"run {entry.run_id} digest mismatch: dir={dir_result.run_digest} "
                f"index={entry.run_digest}"
            )

        loaded = load_run(run_dir)  # re-verifies + returns the typed run

        if loaded.run_digest != entry.run_digest:
            raise DatasetDiscoveryError(
                f"run {entry.run_id} loaded digest mismatch: {loaded.run_digest}"
            )

        # Duplicate detection.
        if loaded.run_id in seen_run_ids:
            raise DatasetDiscoveryError(f"duplicate run_id in index: {loaded.run_id}")
        if loaded.run_digest in seen_digests:
            raise DatasetDiscoveryError(f"duplicate run_digest in index: {loaded.run_digest}")

        # Schema compatibility (refuse, never coerce).
        _check_schema(loaded)

        seen_run_ids.add(loaded.run_id)
        seen_digests.add(loaded.run_digest)
        yield DiscoveredRun(
            loaded=loaded,
            indexed_run_digest=entry.run_digest,
            source_index_digest=source_index_digest,
        )


def _check_schema(loaded: LoadedRun) -> None:
    if loaded.incident.schema_version not in SUPPORTED_INCIDENT_SCHEMA:
        raise DatasetDiscoveryError(
            f"run {loaded.run_id}: unsupported IncidentRecord schema_version "
            f"{loaded.incident.schema_version}"
        )
    if loaded.layout.layout_schema_version not in SUPPORTED_LAYOUT_SCHEMA:
        raise DatasetDiscoveryError(
            f"run {loaded.run_id}: unsupported layout_schema_version "
            f"{loaded.layout.layout_schema_version}"
        )
    gt = loaded.incident.ground_truth
    if gt is not None and gt.schema_version not in SUPPORTED_GROUND_TRUTH_SCHEMA:
        raise DatasetDiscoveryError(
            f"run {loaded.run_id}: unsupported GroundTruth schema_version {gt.schema_version}"
        )
