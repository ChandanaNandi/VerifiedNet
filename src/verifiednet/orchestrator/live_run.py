"""Live composition root — the thin production entry points (Gate 4 + 5.1).

Two functions own one full run each:

    start lab → wait for healthy convergence → execute ONE approved path
    → build the incident record → (finally: restore if injected, stop backend)
    → assemble + index + verify the run artifacts.

Gate 5.1: the accepted path is parameterized by an explicit, immutable
``FaultFamilyBinding`` (default: the Gate 4 remote-AS binding, byte-identical
behavior). The binding is data — there is no plugin system, registration,
discovery, or dynamic import.

This is the ONLY place that composes the live backend, the scenario, and the
artifact assembly. It is not a DAG engine, agent framework, scheduler, or
plugin layer. It performs no natural-language planning, no dynamic fault
selection, no retries, and no model invocation. Lower-level packages never
import it (AST-enforced).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.incidents.builder import build_accepted_record
from verifiednet.incidents.oracle import build_ground_truth
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.convergence import ConvergenceReport, wait_for_bgp_established
from verifiednet.labs.frr.rejected_scenario import (
    DEFAULT_IMPOSSIBLE_PREFIX,
    RejectedPreconditionRun,
)
from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
from verifiednet.orchestrator.assembly import AssembledRun, assemble_verified_run
from verifiednet.orchestrator.catalog import (
    ScenarioCase,
    case_by_id,
    validate_scenario_case,
)
from verifiednet.orchestrator.families import (
    REMOTE_AS_MISMATCH_BINDING,
    FaultFamilyBinding,
    binding_for_template,
)
from verifiednet.runtime.process import ProcessRunner, default_runner
from verifiednet.runtime.transcript import InMemoryTranscript
from verifiednet.schemas.evidence import Phase
from verifiednet.schemas.incident import ProvenanceInfo
from verifiednet.schemas.scenario import ScenarioDefinition
from verifiednet.schemas.topology import TopologySpec
from verifiednet.verifiers.claims import ClaimVerifier

_REJECTED_GENERATOR = "verifiednet.labs.frr.rejected_scenario"
_GENERATOR_VERSION = "0.1.0"


class LiveRunError(VerifiedNetError):
    """A live composed run failed to reach a valid terminal outcome."""


@dataclass(frozen=True)
class LiveRunResult:
    """Outcome of one composed live run: the assembled artifacts + metrics."""

    assembled: AssembledRun
    convergence: ConvergenceReport


def _peer_node(topology: TopologySpec, target_node: str) -> str:
    others = [n.name for n in topology.nodes if n.name != target_node]
    if len(others) != 1:
        raise LiveRunError(f"expected exactly one peer of {target_node!r}, got {others!r}")
    return others[0]


def _environment_metadata(backend: FrrComposeBackend, node: str) -> dict[str, str]:
    meta = dict(backend.capture_environment_metadata())
    result = backend.execute_readonly(node, ["vtysh", "-c", "show version"], 10.0)
    match = re.search(r"FRRouting (\S+)", result.stdout)
    if match:
        meta["frr_version"] = match.group(1)
    return meta


def run_accepted_incident(
    *,
    out_root: str | Path,
    work_dir: str | Path,
    run_ctx: RunContext,
    topology: TopologySpec,
    scenario: ScenarioDefinition,
    git_rev: str,
    lock_hash: str,
    runner: ProcessRunner = default_runner,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    convergence_timeout_s: float = 60.0,
    binding: FaultFamilyBinding = REMOTE_AS_MISMATCH_BINDING,
) -> LiveRunResult:
    """Compose one accepted incident end to end for the bound fault family."""
    if scenario.template_id != binding.template_id:
        raise LiveRunError(
            f"scenario template {scenario.template_id!r} does not match the "
            f"family binding {binding.template_id!r}"
        )
    target_node = str(scenario.parameters["target_node"])
    peer_node = _peer_node(topology, target_node)
    transcript = InMemoryTranscript()
    backend = FrrComposeBackend(
        topology, run_ctx, work_dir=work_dir, runner=runner, monotonic=monotonic,
        sleep=sleep, transcript=transcript,
    )
    phase_plans = None
    if binding.build_phase_plans is not None:
        phase_plans = binding.build_phase_plans(topology, target_node, peer_node)
    provider = LiveScenarioEvidenceProvider(
        executor=backend.readonly_executor, topology=topology, run_ctx=run_ctx,
        target_node=target_node, peer_node=peer_node, phase_plans=phase_plans,
    )
    mutation = backend.build_mutation_adapter(
        allowed_targets=(target_node,), allowed_shapes=binding.mutation_shapes()
    )
    ledger = Ledger(run_ctx)
    sc = binding.build_scenario(
        topology=topology, scenario=scenario, mutation=mutation, ledger=ledger,
        run_ctx=run_ctx, evidence_provider=provider, verifier=ClaimVerifier(run_ctx),
        monotonic=monotonic, sleep=sleep,
    )

    started_at = run_ctx.now()
    payload: dict[str, object] = {}
    convergence: ConvergenceReport | None = None
    try:
        backend.start()
        convergence = wait_for_bgp_established(
            backend.readonly_executor, topology, timeout_s=convergence_timeout_s,
            monotonic=monotonic, sleep=sleep,
        )
        baseline = provider(Phase.BASELINE)[0]
        try:
            pre = sc.validate_preconditions()
            fault = sc.inject()
            onset = sc.verify_onset()
            onset_bundle = provider(Phase.ONSET)[0]
            restoration = sc.restore()
            recovery = sc.verify_recovery()
            recovery_bundle = provider(Phase.RECOVERY)[0]
        finally:
            if ledger.current in (
                LifecyclePhase.INJECTING, LifecyclePhase.INJECTED, LifecyclePhase.ONSET_VERIFIED
            ):
                sc.restore()

        if ledger.current is not LifecyclePhase.RECOVERY_VERIFIED:
            raise LiveRunError(f"accepted run did not reach RECOVERY_VERIFIED: {ledger.current}")
        all_results = (*pre, *onset, *recovery)
        if not all(r.committable for r in all_results):
            failing = [(r.check_id, r.verdict.value) for r in all_results if not r.committable]
            raise LiveRunError(f"non-committable verdicts on the accepted path: {failing!r}")

        ground_truth = build_ground_truth(
            fault=fault, verdicts=(*onset, *recovery),
            accepted_evidence_ids=(*onset_bundle.evidence_ids, *recovery_bundle.evidence_ids),
            root_cause_label=binding.root_cause,
        )
        record = build_accepted_record(
            run_ctx=run_ctx, scenario=scenario, topology=topology, fault=fault,
            ground_truth=ground_truth, baseline=baseline, onset=onset_bundle,
            recovery=recovery_bundle, precondition_results=pre, onset_results=onset,
            recovery_results=recovery, restoration=restoration,
            provenance=ProvenanceInfo(
                generator=binding.generator,
                generator_version=_GENERATOR_VERSION,
                code_commit=git_rev,
            ),
            completed_phases=("precondition", "inject", "onset", "restore", "recovery"),
            cleanup_status="clean",
        )
        payload = {
            "incident": record,
            "environment_metadata": _environment_metadata(backend, target_node),
            "transcript": transcript.entries,
            "ledger": tuple(ledger.records),
            "finished_at": run_ctx.now(),
        }
    finally:
        backend.stop()

    assert convergence is not None
    assembled = assemble_verified_run(
        out_root=out_root,
        incident=payload["incident"],  # type: ignore[arg-type]
        environment_metadata=payload["environment_metadata"],  # type: ignore[arg-type]
        transcript_entries=payload["transcript"],  # type: ignore[arg-type]
        ledger_records=payload["ledger"],  # type: ignore[arg-type]
        git_rev=git_rev, lock_hash=lock_hash, started_at=started_at,
        finished_at=payload["finished_at"],  # type: ignore[arg-type]
    )
    return LiveRunResult(assembled=assembled, convergence=convergence)


def run_accepted_case(
    *,
    case: ScenarioCase,
    out_root: str | Path,
    work_dir: str | Path,
    run_ctx: RunContext,
    topology: TopologySpec,
    git_rev: str,
    lock_hash: str,
    runner: ProcessRunner = default_runner,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    convergence_timeout_s: float = 60.0,
) -> LiveRunResult:
    """Run one CATALOG-APPROVED scenario case through its family's live path.

    Only cases present in ``SCENARIO_CATALOG`` are executable: the case is
    validated deterministically against the topology first (raising before any
    lab action), then dispatched to the existing family binding and the existing
    ``run_accepted_incident`` path. This adds no new execution logic and no
    free-form command generation — the case carries plain scalars only.
    """
    try:
        approved = case_by_id(case.case_id)
    except KeyError as exc:
        raise LiveRunError(str(exc)) from exc
    if approved is not case:
        raise LiveRunError(f"case {case.case_id!r} is not the approved catalog instance")
    validate_scenario_case(case, topology)
    binding = binding_for_template(case.template_id)
    return run_accepted_incident(
        out_root=out_root,
        work_dir=work_dir,
        run_ctx=run_ctx,
        topology=topology,
        scenario=case.scenario,
        git_rev=git_rev,
        lock_hash=lock_hash,
        runner=runner,
        monotonic=monotonic,
        sleep=sleep,
        convergence_timeout_s=convergence_timeout_s,
        binding=binding,
    )


def run_precondition_rejected_incident(
    *,
    out_root: str | Path,
    work_dir: str | Path,
    run_ctx: RunContext,
    topology: TopologySpec,
    scenario: ScenarioDefinition,
    git_rev: str,
    lock_hash: str,
    impossible_prefix: str = DEFAULT_IMPOSSIBLE_PREFIX,
    runner: ProcessRunner = default_runner,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    convergence_timeout_s: float = 60.0,
) -> LiveRunResult:
    """Compose one deliberately-rejected precondition run end to end (zero mutation)."""
    target_node = str(scenario.parameters["target_node"])
    peer_node = _peer_node(topology, target_node)
    transcript = InMemoryTranscript()
    backend = FrrComposeBackend(
        topology, run_ctx, work_dir=work_dir, runner=runner, monotonic=monotonic,
        sleep=sleep, transcript=transcript,
    )
    ledger = Ledger(run_ctx)

    started_at = run_ctx.now()
    payload: dict[str, object] = {}
    convergence: ConvergenceReport | None = None
    try:
        backend.start()
        convergence = wait_for_bgp_established(
            backend.readonly_executor, topology, timeout_s=convergence_timeout_s,
            monotonic=monotonic, sleep=sleep,
        )
        rejected = RejectedPreconditionRun(
            executor=backend.readonly_executor, topology=topology, scenario=scenario,
            run_ctx=run_ctx, ledger=ledger, verifier=ClaimVerifier(run_ctx),
            target_node=target_node, peer_node=peer_node, impossible_prefix=impossible_prefix,
        )
        record = rejected.execute(
            provenance=ProvenanceInfo(
                generator=_REJECTED_GENERATOR,
                generator_version=_GENERATOR_VERSION,
                code_commit=git_rev,
            )
        )
        if any(e.mode == "mutation" for e in transcript.entries):
            raise LiveRunError("rejected precondition run unexpectedly produced a mutation")
        if ledger.current is not LifecyclePhase.PENDING:
            raise LiveRunError(f"rejected run left the ledger at {ledger.current}, not PENDING")
        payload = {
            "incident": record,
            "environment_metadata": _environment_metadata(backend, target_node),
            "transcript": transcript.entries,
            "ledger": tuple(ledger.records),
            "finished_at": run_ctx.now(),
        }
    finally:
        backend.stop()

    assert convergence is not None
    assembled = assemble_verified_run(
        out_root=out_root,
        incident=payload["incident"],  # type: ignore[arg-type]
        environment_metadata=payload["environment_metadata"],  # type: ignore[arg-type]
        transcript_entries=payload["transcript"],  # type: ignore[arg-type]
        ledger_records=payload["ledger"],  # type: ignore[arg-type]
        git_rev=git_rev, lock_hash=lock_hash, started_at=started_at,
        finished_at=payload["finished_at"],  # type: ignore[arg-type]
    )
    return LiveRunResult(assembled=assembled, convergence=convergence)
