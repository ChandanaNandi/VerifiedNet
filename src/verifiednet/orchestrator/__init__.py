"""Gate 4 composition root: assemble, index, and run verified live incidents.

This package is the ONLY place that composes the live FRR backend, a scenario,
and the canonical artifact + index persistence into one end-to-end run. It is a
thin composition root — NOT a DAG engine, workflow engine, scheduler, agent
framework, or plugin layer. It performs no natural-language planning, no dynamic
fault selection, no retries, and no model invocation. No lower-level package
imports it (AST-enforced).
"""

from verifiednet.orchestrator.assembly import AssembledRun, assemble_verified_run
from verifiednet.orchestrator.families import (
    APPROVED_FAMILY_BINDINGS,
    BGP_NEIGHBOR_REMOVAL_BINDING,
    BGP_PREFIX_WITHDRAWAL_BINDING,
    IFACE_ADMIN_SHUTDOWN_BINDING,
    REMOTE_AS_MISMATCH_BINDING,
    FaultFamilyBinding,
    binding_for_template,
)
from verifiednet.orchestrator.live_run import (
    LiveRunError,
    LiveRunResult,
    run_accepted_incident,
    run_precondition_rejected_incident,
)
from verifiednet.orchestrator.manifests import (
    build_environment_manifest,
    build_run_manifest,
    transcript_sha256,
)

__all__ = [
    "APPROVED_FAMILY_BINDINGS",
    "BGP_NEIGHBOR_REMOVAL_BINDING",
    "BGP_PREFIX_WITHDRAWAL_BINDING",
    "IFACE_ADMIN_SHUTDOWN_BINDING",
    "REMOTE_AS_MISMATCH_BINDING",
    "AssembledRun",
    "FaultFamilyBinding",
    "LiveRunError",
    "LiveRunResult",
    "assemble_verified_run",
    "binding_for_template",
    "build_environment_manifest",
    "build_run_manifest",
    "run_accepted_incident",
    "run_precondition_rejected_incident",
    "transcript_sha256",
]
