"""Evidence collectors. Read-only by construction; parse failures are loud.

Boundary (AST-enforced): this package never imports
``verifiednet.runtime.mutation`` nor ``verifiednet.faults``.
"""

from verifiednet.collectors.base import (
    EvidenceCollector,
    ReadOnlyExec,
    make_evidence_record,
)

__all__ = [
    "EvidenceCollector",
    "ReadOnlyExec",
    "make_evidence_record",
]
