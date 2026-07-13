"""BGP summary collector: ``show ip bgp summary json``.

Provenance: parser behavior adapted from neuronoc-network-ops-assistant
``backend/app/lab/collector.py`` (MIT, commit 5f24447; copy with
modifications: detached from NN schemas, bounded, sorted output). FRR emits
the peer state under ``state`` in newer releases and ``peerState`` in some
variants; both are accepted (``state`` preferred). Missing structure or
malformed JSON raises ``ParserError`` — never a silent fallback.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import ClassVar

from verifiednet.collectors.base import (
    ReadOnlyExec,
    make_evidence_record,
    require_ok,
    sorted_normalized,
)
from verifiednet.common.errors import ParserError
from verifiednet.common.runctx import RunContext
from verifiednet.schemas.evidence import EvidenceRecord, Phase


class BgpSummaryCollector:
    """Collect and normalize the FRR IPv4-unicast BGP summary.

    ``expected_peers`` (Gate 5.1, additive): for each explicitly-requested peer
    address the collector ALSO emits ``bgp.peer.<ip>.present`` = ``"true"`` or
    ``"false"`` — mirroring ``RoutePresenceCollector``'s requested-prefix
    discipline. A removed peer thereby becomes an affirmative ``"false"``
    observation (FAILable evidence) instead of a silently missing metric
    (INSUFFICIENT). With the default empty tuple the emitted metrics are
    byte-identical to Gate 4 behavior.
    """

    name: str = "frr.bgp_summary"
    _ARGV: ClassVar[tuple[str, ...]] = ("vtysh", "-c", "show ip bgp summary json")

    def __init__(
        self,
        executor: ReadOnlyExec,
        target: str,
        run_ctx: RunContext,
        timeout_s: float = 10.0,
        expected_peers: Sequence[str] = (),
    ) -> None:
        self._executor = executor
        self._target = target
        self._run_ctx = run_ctx
        self._timeout_s = timeout_s
        self._expected_peers = tuple(expected_peers)

    def collect(self, phase: Phase) -> EvidenceRecord:
        result = self._executor.run(self._target, self._ARGV, self._timeout_s)
        require_ok(self.name, result)
        normalized = self._parse(result.stdout)
        return make_evidence_record(
            collector=self.name,
            target=self._target,
            command=self._ARGV,
            transcript_seq=result.seq,
            trusted=True,
            phase=phase,
            raw_payload=result.stdout,
            normalized=normalized,
            run_ctx=self._run_ctx,
        )

    def _parse(self, stdout: str) -> dict[str, str]:
        normalized = dict(
            parse_bgp_summary(
                stdout,
                name=self.name,
                allow_missing_af=bool(self._expected_peers),
            )
        )
        for peer_ip in sorted(set(self._expected_peers)):
            present = f"bgp.peer.{peer_ip}.state" in normalized
            normalized[f"bgp.peer.{peer_ip}.present"] = "true" if present else "false"
        return sorted_normalized(normalized)


def parse_bgp_summary(
    stdout: str, *, name: str = "frr.bgp_summary", allow_missing_af: bool = False
) -> dict[str, str]:
    """Parse ``show ip bgp summary json`` output into normalized keys.

    Module-level so the live BGP convergence helper (Gate 4) can reuse the
    exact same parsing/validation as the collector — one parser, one behavior.
    Raises ``ParserError`` on malformed or structurally missing output.

    ``allow_missing_af`` (Gate 5.2, live-verified): FRR 8.4.1 OMITS the
    ``ipv4Unicast`` object entirely once the last IPv4-unicast neighbor is
    removed (observed live on the canonical host during the neighbor-removal
    incident). In expected-peers mode that absence IS the observation — zero
    configured peers — so the caller may opt in to receiving ``{}`` instead of
    a ``ParserError``. The DEFAULT (used by Gate 3/4 collectors and the
    convergence helper) is unchanged: a missing address family stays a loud
    parse failure.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ParserError(f"{name}: malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ParserError(f"{name}: top-level JSON is not an object")
    ipv4 = data.get("ipv4Unicast")
    if not isinstance(ipv4, dict):
        if allow_missing_af and ipv4 is None:
            return {}  # zero IPv4-unicast peers configured — real evidence
        raise ParserError(f"{name}: missing ipv4Unicast object")
    local_as = ipv4.get("as")
    if not isinstance(local_as, int) or isinstance(local_as, bool):
        raise ParserError(f"{name}: missing/invalid ipv4Unicast.as")
    peers = ipv4.get("peers")
    if not isinstance(peers, dict):
        raise ParserError(f"{name}: missing ipv4Unicast.peers object")
    normalized: dict[str, str] = {"bgp.local_as": str(local_as)}
    for ip, peer in peers.items():
        if not isinstance(peer, dict):
            raise ParserError(f"{name}: peer entry {ip!r} is not an object")
        state = peer.get("state")
        if state is None:
            state = peer.get("peerState")
        if not isinstance(state, str):
            raise ParserError(f"{name}: peer {ip!r} has no state/peerState")
        remote_as = peer.get("remoteAs")
        if not isinstance(remote_as, int) or isinstance(remote_as, bool):
            raise ParserError(f"{name}: peer {ip!r} has no remoteAs")
        normalized[f"bgp.peer.{ip}.state"] = state
        normalized[f"bgp.peer.{ip}.remote_as"] = str(remote_as)
    return sorted_normalized(normalized)
