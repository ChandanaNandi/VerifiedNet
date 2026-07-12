"""FRR lab artifacts and live Compose backend.

Gate 3 shipped the pure deterministic renderers. Gate 4 adds the live two-router
FRR-on-Compose backend, its deterministic Compose-project abstraction, and the
logical/transport execution adapters. Command execution still flows exclusively
through ``verifiednet.runtime`` — nothing here imports ``subprocess``.
"""

from verifiednet.labs.frr.backend import (
    DEFAULT_COMMAND_TIMEOUT_S,
    DEFAULT_DOWN_TIMEOUT_S,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_UP_TIMEOUT_S,
    FrrComposeBackend,
    LabBackendError,
)
from verifiednet.labs.frr.compose_project import (
    ComposeProject,
    ServiceResolutionError,
    project_name_for_run,
)
from verifiednet.labs.frr.convergence import (
    BgpConvergenceTimeoutError,
    ConvergenceReport,
    wait_for_bgp_established,
)
from verifiednet.labs.frr.exec_adapter import (
    FrrMutationTransportAdapter,
    FrrReadOnlyTransportAdapter,
)
from verifiednet.labs.frr.fixture_capture import (
    FixtureCaptureError,
    capture_live_fixture_set,
    verify_fixture_manifest,
)
from verifiednet.labs.frr.rejected_scenario import (
    DEFAULT_IMPOSSIBLE_PREFIX,
    ImpossiblePreconditionSatisfiedError,
    NonDeterministicRejectionError,
    RejectedPreconditionRun,
)
from verifiednet.labs.frr.render import (
    render_all,
    render_compose,
    render_daemons,
    render_frr_conf,
    write_rendered,
)
from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
from verifiednet.labs.frr.topologies import (
    PINNED_FRR_IMAGE,
    PINNED_FRR_IMAGE_ARM64_DIGEST,
    two_router_frr_topology,
)

__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_S",
    "DEFAULT_DOWN_TIMEOUT_S",
    "DEFAULT_IMPOSSIBLE_PREFIX",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_UP_TIMEOUT_S",
    "PINNED_FRR_IMAGE",
    "PINNED_FRR_IMAGE_ARM64_DIGEST",
    "BgpConvergenceTimeoutError",
    "ComposeProject",
    "ConvergenceReport",
    "FixtureCaptureError",
    "FrrComposeBackend",
    "FrrMutationTransportAdapter",
    "FrrReadOnlyTransportAdapter",
    "ImpossiblePreconditionSatisfiedError",
    "LabBackendError",
    "LiveScenarioEvidenceProvider",
    "NonDeterministicRejectionError",
    "RejectedPreconditionRun",
    "ServiceResolutionError",
    "capture_live_fixture_set",
    "project_name_for_run",
    "render_all",
    "render_compose",
    "render_daemons",
    "render_frr_conf",
    "two_router_frr_topology",
    "verify_fixture_manifest",
    "wait_for_bgp_established",
    "write_rendered",
]
