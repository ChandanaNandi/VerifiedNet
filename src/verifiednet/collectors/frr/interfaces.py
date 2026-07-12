"""Interface state collector: ``show interface json``.

Provenance: parser behavior adapted from neuronoc-network-ops-assistant
``backend/app/lab/collector.py`` (MIT, commit 5f24447; copy with
modifications: detached from NN schemas, bounded, sorted output). All
interfaces are included (loopback too), sorted by name, capped at 64
(bounded-output discipline) with an explicit ``iface._truncated`` marker.
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

_MAX_INTERFACES = 64


class InterfaceStateCollector:
    """Collect admin/oper status for every interface on the target."""

    name: str = "frr.interfaces"
    _ARGV: ClassVar[tuple[str, ...]] = ("vtysh", "-c", "show interface json")

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
        names = sorted(data)
        truncated = len(names) > _MAX_INTERFACES
        normalized: dict[str, str] = {}
        for ifname in names[:_MAX_INTERFACES]:
            entry = data[ifname]
            if not isinstance(entry, dict):
                raise ParserError(f"{self.name}: interface {ifname!r} is not an object")
            admin = entry.get("administrativeStatus")
            if admin is None:
                admin = entry.get("adminStatus")
            oper = entry.get("operationalStatus")
            if oper is None:
                oper = entry.get("operStatus")
            if not isinstance(admin, str) or not isinstance(oper, str):
                raise ParserError(
                    f"{self.name}: interface {ifname!r} missing admin/oper status"
                )
            normalized[f"iface.{ifname}.admin"] = admin.lower()
            normalized[f"iface.{ifname}.oper"] = oper.lower()
        if truncated:
            normalized["iface._truncated"] = "true"
        return sorted_normalized(normalized)
