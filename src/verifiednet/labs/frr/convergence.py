"""Bounded live BGP convergence verification (Gate 4 Step 2).

Backend *readiness* (containers running, FRR answering a harmless ``vtysh``)
and BGP *convergence* (the eBGP session Established on BOTH sides) are distinct
concepts. ``FrrComposeBackend.start()`` proves only readiness; this module
proves convergence, live, with a bounded deadline — never a blind sleep as the
correctness mechanism.

Rules:
- All observation flows through the read-only execution path (policy-checked,
  transcripted); the parser is the SAME one the BGP collector uses
  (``parse_bgp_summary``) — one parser, one behavior.
- Monotonic deadline + fixed polling interval.
- Convergence requires ``required_consecutive`` (default 2) consecutive polls
  in which EVERY session endpoint reports ``Established``.
- A ``ParserError`` during a poll (e.g. bgpd still booting emits ``{}``) is an
  UNHEALTHY OBSERVATION — recorded in ``last_states``, resets the consecutive
  counter, never silently swallowed and never terminal by itself. Every other
  exception propagates.
- Timeout raises the typed ``BgpConvergenceTimeoutError`` carrying the full
  report (attempts, elapsed, last observed per-endpoint states).

Default bounds (bounded operational defaults for the first live slice — NOT
benchmark-derived): convergence timeout 60 s, poll interval 1 s, 2 consecutive
confirmations. The observed live convergence on the reference host was ~20 s
from ``compose up``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from verifiednet.collectors.base import ReadOnlyExec
from verifiednet.collectors.frr.bgp import parse_bgp_summary
from verifiednet.common.errors import ParserError, VerifiedNetError
from verifiednet.runtime.results import ExecStatus
from verifiednet.schemas.topology import TopologySpec

DEFAULT_CONVERGENCE_TIMEOUT_S = 60.0
DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_REQUIRED_CONSECUTIVE = 2
_BGP_SUMMARY_ARGV = ("vtysh", "-c", "show ip bgp summary json")
_ESTABLISHED = "Established"


class BgpConvergenceTimeoutError(VerifiedNetError):
    """BGP did not reach Established on all session endpoints in time."""

    def __init__(self, report: ConvergenceReport) -> None:
        super().__init__(
            "BGP convergence timeout after "
            f"{report.attempts} attempts / {report.elapsed_s:.1f}s; "
            f"last states: {report.last_states!r}"
        )
        self.report = report


@dataclass(frozen=True)
class ConvergenceReport:
    """Outcome of a convergence wait: attempts, elapsed, last observations.

    ``last_states`` maps ``"<node>:<peer_ip>"`` to the last observed BGP state
    string (or ``"parse-error: …"`` / ``"missing-peer"`` when the observation
    itself was unusable).
    """

    converged: bool
    attempts: int
    elapsed_s: float
    last_states: dict[str, str] = field(default_factory=dict)


def _observe_endpoint(
    executor: ReadOnlyExec, node: str, peer_ip: str, timeout_s: float
) -> str:
    """One endpoint observation: state string, or a diagnostic pseudo-state."""
    result = executor.run(node, _BGP_SUMMARY_ARGV, timeout_s)
    if result.status is not ExecStatus.OK:
        return f"exec-{result.status.value}"
    try:
        normalized = parse_bgp_summary(result.stdout)
    except ParserError as exc:
        return f"parse-error: {exc}"
    state = normalized.get(f"bgp.peer.{peer_ip}.state")
    if state is None:
        return "missing-peer"
    return state


def wait_for_bgp_established(
    executor: ReadOnlyExec,
    topology: TopologySpec,
    *,
    timeout_s: float = DEFAULT_CONVERGENCE_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    required_consecutive: int = DEFAULT_REQUIRED_CONSECUTIVE,
    command_timeout_s: float = 10.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ConvergenceReport:
    """Poll until every session endpoint is Established, twice consecutively.

    Returns the successful :class:`ConvergenceReport`; raises
    :class:`BgpConvergenceTimeoutError` (carrying the report) on deadline.
    """
    if required_consecutive < 1:
        raise ValueError(f"required_consecutive must be >= 1, got {required_consecutive}")
    endpoints = [
        (ep.node, ep.peer_ip)
        for session in topology.sessions
        for ep in (session.a, session.b)
    ]
    start = monotonic()
    deadline = start + timeout_s
    attempts = 0
    consecutive = 0
    last_states: dict[str, str] = {}
    while True:
        attempts += 1
        last_states = {
            f"{node}:{peer_ip}": _observe_endpoint(
                executor, node, peer_ip, command_timeout_s
            )
            for node, peer_ip in endpoints
        }
        if all(state == _ESTABLISHED for state in last_states.values()):
            consecutive += 1
            if consecutive >= required_consecutive:
                return ConvergenceReport(
                    converged=True,
                    attempts=attempts,
                    elapsed_s=monotonic() - start,
                    last_states=dict(last_states),
                )
        else:
            consecutive = 0
        if monotonic() >= deadline:
            raise BgpConvergenceTimeoutError(
                ConvergenceReport(
                    converged=False,
                    attempts=attempts,
                    elapsed_s=monotonic() - start,
                    last_states=dict(last_states),
                )
            )
        sleep(poll_interval_s)
