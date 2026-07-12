"""Reachability collector: sequential single-packet ping probes.

Policy (Gate 2.5 W8): ALL probes must succeed for ``all_success`` — the 3/3
rule. The 4/15 "success floor" seen in reference material was REJECTED per
owner decision: partial reachability is a fault symptom, not a pass.

Provenance: deterministic exit-code semantics from evpn-vxlan-frr-lab
``validate/checks.py::loopback_reachable`` (MIT, commit 5b5a479; copy with
modifications: probe loop detached, per-probe transcripts, bounded raw).

Error policy: a failed or timed-out probe is EVIDENCE (counted as a failed
probe, never raised) — a failed ping is data, not a parse failure. But
``DENIED_COMMAND``/``DENIED_TARGET`` raise ``ParserError``: a policy
misconfiguration must be loud, never recorded as unreachability.
"""

from __future__ import annotations

from verifiednet.collectors.base import (
    ReadOnlyExec,
    make_evidence_record,
    sorted_normalized,
)
from verifiednet.common.errors import ParserError
from verifiednet.common.runctx import RunContext
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.schemas.evidence import EvidenceRecord, Phase

_DENIED = (ExecStatus.DENIED_COMMAND, ExecStatus.DENIED_TARGET)


class ReachabilityCollector:
    """Probe *dst_ip* from the target node; all-probes-must-succeed (3/3 rule)."""

    name: str = "frr.reachability"

    def __init__(
        self,
        executor: ReadOnlyExec,
        target: str,
        run_ctx: RunContext,
        dst_ip: str,
        probes: int = 3,
        timeout_s: float = 10.0,
    ) -> None:
        if probes < 1:
            raise ValueError(f"probes must be >= 1, got {probes}")
        self._executor = executor
        self._target = target
        self._run_ctx = run_ctx
        self._dst_ip = dst_ip
        self._probes = probes
        self._timeout_s = timeout_s

    def collect(self, phase: Phase) -> EvidenceRecord:
        argv = ("ping", "-c", "1", "-W", "2", self._dst_ip)
        lines: list[str] = []
        successes = 0
        last: ExecResult | None = None
        for index in range(1, self._probes + 1):
            result = self._executor.run(self._target, argv, self._timeout_s)
            if result.status in _DENIED:
                raise ParserError(
                    f"collector {self.name}: exec status {result.status.value} — "
                    "policy misconfiguration must be loud, not recorded as evidence"
                )
            if result.status is ExecStatus.OK and result.exit_code == 0:
                successes += 1
            code = "none" if result.exit_code is None else str(result.exit_code)
            lines.append(f"probe={index} exit={code}")
            last = result
        assert last is not None  # probes >= 1
        raw_payload = "\n".join(lines) + "\n" + last.stdout
        normalized = sorted_normalized(
            {
                f"ping.{self._dst_ip}.probe_count": str(self._probes),
                f"ping.{self._dst_ip}.success_count": str(successes),
                f"ping.{self._dst_ip}.all_success": (
                    "true" if successes == self._probes else "false"
                ),
            }
        )
        return make_evidence_record(
            collector=self.name,
            target=self._target,
            command=argv,
            transcript_seq=last.seq,
            trusted=True,
            phase=phase,
            raw_payload=raw_payload,
            normalized=normalized,
            run_ctx=self._run_ctx,
        )
