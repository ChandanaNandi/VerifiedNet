"""Running-config collector: ``show running-config``.

Provenance: adapted from neuronoc-network-ops-assistant
``backend/app/lab/collector.py`` (MIT, commit 5f24447; copy with
modifications: bounded raw payload, hash-only normalization). The normalized
output is a single content hash used by the b-side-unchanged verification
check — configs are compared by digest, never by fuzzy text diff.

Raw text is bounded to 64 KiB (bounded-output discipline); truncation is
recorded explicitly in ``config._truncated``. An empty running-config is a
parse failure (a live FRR node always has at least a version banner).
"""

from __future__ import annotations

from typing import ClassVar

from verifiednet.collectors.base import (
    ReadOnlyExec,
    make_evidence_record,
    require_ok,
    sorted_normalized,
)
from verifiednet.common.errors import ParserError
from verifiednet.common.hashing import sha256_bytes
from verifiednet.common.runctx import RunContext
from verifiednet.schemas.evidence import EvidenceRecord, Phase

_MAX_RAW_BYTES = 64 * 1024


class RunningConfigCollector:
    """Capture the running config as a bounded raw payload + content hash."""

    name: str = "frr.running_config"
    _ARGV: ClassVar[tuple[str, ...]] = ("vtysh", "-c", "show running-config")

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
        if not result.stdout.strip():
            raise ParserError(f"{self.name}: empty running-config output")
        raw_bytes = result.stdout.encode("utf-8")
        truncated = len(raw_bytes) > _MAX_RAW_BYTES
        raw_payload = (
            raw_bytes[:_MAX_RAW_BYTES].decode("utf-8", errors="ignore")
            if truncated
            else result.stdout
        )
        normalized: dict[str, str] = {
            "config.sha256": sha256_bytes(raw_payload.encode("utf-8"))
        }
        if truncated:
            normalized["config._truncated"] = "true"
        return make_evidence_record(
            collector=self.name,
            target=self._target,
            command=self._ARGV,
            transcript_seq=result.seq,
            trusted=True,
            phase=phase,
            raw_payload=raw_payload,
            normalized=sorted_normalized(normalized),
            run_ctx=self._run_ctx,
        )
