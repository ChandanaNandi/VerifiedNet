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
    """Collect and normalize the FRR IPv4-unicast BGP summary."""

    name: str = "frr.bgp_summary"
    _ARGV: ClassVar[tuple[str, ...]] = ("vtysh", "-c", "show ip bgp summary json")

    def __init__(
        self,
        executor: ReadOnlyExec,
        target: str,
        run_ctx: RunContext,
        timeout_s: float = 10.0,
    ) -> None:
        self._executor = executor
        self._target = target
        self._run_ctx = run_ctx
        self._timeout_s = timeout_s

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
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ParserError(f"{self.name}: malformed JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ParserError(f"{self.name}: top-level JSON is not an object")
        ipv4 = data.get("ipv4Unicast")
        if not isinstance(ipv4, dict):
            raise ParserError(f"{self.name}: missing ipv4Unicast object")
        local_as = ipv4.get("as")
        if not isinstance(local_as, int) or isinstance(local_as, bool):
            raise ParserError(f"{self.name}: missing/invalid ipv4Unicast.as")
        peers = ipv4.get("peers")
        if not isinstance(peers, dict):
            raise ParserError(f"{self.name}: missing ipv4Unicast.peers object")
        normalized: dict[str, str] = {"bgp.local_as": str(local_as)}
        for ip, peer in peers.items():
            if not isinstance(peer, dict):
                raise ParserError(f"{self.name}: peer entry {ip!r} is not an object")
            state = peer.get("state")
            if state is None:
                state = peer.get("peerState")
            if not isinstance(state, str):
                raise ParserError(f"{self.name}: peer {ip!r} has no state/peerState")
            remote_as = peer.get("remoteAs")
            if not isinstance(remote_as, int) or isinstance(remote_as, bool):
                raise ParserError(f"{self.name}: peer {ip!r} has no remoteAs")
            normalized[f"bgp.peer.{ip}.state"] = state
            normalized[f"bgp.peer.{ip}.remote_as"] = str(remote_as)
        return sorted_normalized(normalized)
