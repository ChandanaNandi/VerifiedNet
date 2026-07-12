"""Ground-truth oracle — mechanical assembly only (Principles 11-14).

Ground truth is built EXCLUSIVELY from (a) the recorded fault injection and
(b) deterministic verifier verdicts over trusted evidence. No free text, no
model output, no operator prose is ever accepted: the only label field is a
machine label validated by the schema, and there is no code path by which an
LLM output can reach this function. This package's import policy (AST-
enforced) bans ``verifiednet.runtime``, ``.labs``, ``.collectors`` and
``.faults`` — incidents consume finished data, never live systems.
"""

from __future__ import annotations

from collections.abc import Sequence

from verifiednet.schemas.fault import FaultInjection
from verifiednet.schemas.ground_truth import GroundTruth
from verifiednet.schemas.verification import VerificationResult

ORACLE_VERSION = "1.0.0"


def build_ground_truth(
    *,
    fault: FaultInjection,
    verdicts: Sequence[VerificationResult],
    accepted_evidence_ids: Sequence[str],
    root_cause_label: str,
) -> GroundTruth:
    """Assemble ground truth from fault metadata and verifier verdicts only."""
    if not verdicts:
        raise ValueError("ground truth requires at least one verifier verdict")
    for verdict in verdicts:
        if not isinstance(verdict, VerificationResult):
            raise TypeError(
                f"verdicts must be VerificationResult instances, got {type(verdict)!r}"
            )
    if not isinstance(fault, FaultInjection):
        raise TypeError(f"fault must be a FaultInjection, got {type(fault)!r}")
    return GroundTruth(
        oracle_version=ORACLE_VERSION,
        fault=fault,
        verdicts=tuple(verdicts),
        accepted_evidence_ids=tuple(accepted_evidence_ids),
        root_cause_label=root_cause_label,
    )
