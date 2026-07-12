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
from verifiednet.labs.frr.exec_adapter import (
    FrrMutationTransportAdapter,
    FrrReadOnlyTransportAdapter,
)
from verifiednet.labs.frr.render import (
    render_all,
    render_compose,
    render_daemons,
    render_frr_conf,
    write_rendered,
)

__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_S",
    "DEFAULT_DOWN_TIMEOUT_S",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_UP_TIMEOUT_S",
    "ComposeProject",
    "FrrComposeBackend",
    "FrrMutationTransportAdapter",
    "FrrReadOnlyTransportAdapter",
    "LabBackendError",
    "ServiceResolutionError",
    "project_name_for_run",
    "render_all",
    "render_compose",
    "render_daemons",
    "render_frr_conf",
    "write_rendered",
]
