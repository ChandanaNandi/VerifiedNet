"""Verified dataset engine — a read-only, deterministic projection of the Gate 5
verified run library (Gate 6.1).

This package is a CONSUMER of already-verified run artifacts, never a producer
of truth (ADR-0018). It never modifies a run, an IncidentRecord, evidence, a
transcript, a ledger, a manifest, or the run index; it never rewrites a run
digest; it never contacts Docker/FRR, re-runs the lab, re-derives evidence,
re-runs verification/oracle, infers labels, or invokes any model. Truth flows
one way: verified runs -> dataset projection -> (future) evaluation -> (future)
models, never backward.

Gate 6.1 scope: dataset models, verified-run discovery, and pure projection
(including the stable leakage ``group_id`` and the unique ``example_id``).
Splitting, dataset digests, and rejected-partition handling are later substeps.
"""

from verifiednet.datasets.discovery import (
    DatasetDiscoveryError,
    DiscoveredRun,
    discover_verified_runs,
)
from verifiednet.datasets.models import (
    ArtifactReference,
    DatasetExample,
    DatasetManifest,
)
from verifiednet.datasets.projection import (
    ProjectionError,
    compute_example_id,
    compute_group_id,
    project_verified_run,
)

__all__ = [
    "ArtifactReference",
    "DatasetDiscoveryError",
    "DatasetExample",
    "DatasetManifest",
    "DiscoveredRun",
    "ProjectionError",
    "compute_example_id",
    "compute_group_id",
    "discover_verified_runs",
    "project_verified_run",
]
