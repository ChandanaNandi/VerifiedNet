"""Route presence collector: ``show ip route json``.

Provenance: parser behavior adapted from neuronoc-network-ops-assistant
``backend/app/lab/collector.py`` (MIT, commit 5f24447; copy with
modifications: detached from NN schemas, bounded to requested prefixes,
sorted output). Only the REQUESTED prefixes are normalized (bounded-output
discipline); an absent prefix is evidence ("present": "false"), a malformed
route table entry for a requested prefix raises ``ParserError``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar

from verifiednet.collectors.base import (
    ReadOnlyExec,
    make_evidence_record,
    require_ok,
    sorted_normalized,
)
from verifiednet.common.errors import ParserError
from verifiednet.common.runctx import RunContext
from verifiednet.schemas.evidence import EvidenceRecord, Phase


class RoutePresenceCollector:
    """Report presence + originating protocols for each requested prefix."""

    name: str = "frr.routes"
    _ARGV: ClassVar[tuple[str, ...]] = ("vtysh", "-c", "show ip route json")

    def __init__(
        self,
        executor: ReadOnlyExec,
        target: str,
        run_ctx: RunContext,
        prefixes: Sequence[str],
        timeout_s: float = 10.0,
    ) -> None:
        if not prefixes:
            raise ValueError("prefixes must be non-empty")
        self._executor = executor
        self._target = target
        self._run_ctx = run_ctx
        self._prefixes = tuple(prefixes)
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
        normalized: dict[str, str] = {}
        for prefix in sorted(set(self._prefixes)):
            routes = data.get(prefix)
            protocols = self._protocols_for(prefix, routes)
            present = protocols is not None and len(protocols) > 0
            normalized[f"route.{prefix}.present"] = "true" if present else "false"
            normalized[f"route.{prefix}.protocols"] = (
                ",".join(sorted(protocols)) if protocols else ""
            )
        return sorted_normalized(normalized)

    def _protocols_for(self, prefix: str, routes: Any) -> set[str] | None:
        if routes is None:
            return None
        if not isinstance(routes, list):
            raise ParserError(f"{self.name}: routes for {prefix!r} is not a list")
        protocols: set[str] = set()
        for entry in routes:
            if not isinstance(entry, dict):
                raise ParserError(f"{self.name}: route entry for {prefix!r} not an object")
            protocol = entry.get("protocol")
            if not isinstance(protocol, str):
                raise ParserError(f"{self.name}: route entry for {prefix!r} lacks protocol")
            protocols.add(protocol)
        return protocols
