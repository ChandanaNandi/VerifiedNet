"""One deliberately-rejected precondition run on a HEALTHY lab (Gate 4 Step 4).

This is the honest rejection path: an intentionally impossible precondition
(the presence of an RFC 5737 documentation route that the two-router topology
can never carry) is evaluated by the EXISTING ``ClaimVerifier`` against real
baseline evidence. The check fails deterministically, ``PreconditionResultsError``
is raised, the ledger stays ``PENDING``, and a rejected ``IncidentRecord`` is
built through the existing ``build_rejected_record``.

Nothing here mutates the lab: no ``MutationExecutor`` is constructed or invoked,
no ``FaultInjection`` is created, no ground truth is assembled, and the lab
remains healthy throughout. The rejection comes ONLY from a real failed verdict
— an impossible route that is unexpectedly PRESENT, or missing evidence, is a
setup/health error and is raised, never silently turned into a rejection.

The impossible route is emitted as ``route.<prefix>.present == "false"`` by the
existing ``RoutePresenceCollector`` (a requested-but-absent prefix), so the
``route_present`` check observes a real ``"false"`` and returns FAIL — distinct
from INSUFFICIENT, which is what a missing observation would yield.
"""

from __future__ import annotations

from collections.abc import Sequence

from verifiednet.collectors.base import ReadOnlyExec
from verifiednet.collectors.frr import RoutePresenceCollector
from verifiednet.common.errors import PhaseTransitionError, VerifiedNetError
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.faults.scenario import PreconditionResultsError
from verifiednet.incidents.builder import build_rejected_record
from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
from verifiednet.schemas.evidence import EvidenceBundle, Phase
from verifiednet.schemas.incident import IncidentRecord, ProvenanceInfo, RejectionCode
from verifiednet.schemas.scenario import ScenarioDefinition
from verifiednet.schemas.topology import TopologySpec
from verifiednet.schemas.verification import Verdict, VerificationResult
from verifiednet.verifiers import checks
from verifiednet.verifiers.claims import Verifier

#: RFC 5737 TEST-NET-3 documentation address — never routable in this lab.
DEFAULT_IMPOSSIBLE_PREFIX = "203.0.113.99/32"


class ImpossiblePreconditionSatisfiedError(VerifiedNetError):
    """The impossible precondition was unexpectedly SATISFIED.

    This means the selected route was actually present (a topology/health
    surprise), so there is no honest deterministic rejection to record. It is a
    loud setup error — never converted into a ``PRECONDITION_FAILED`` record.
    """


class NonDeterministicRejectionError(VerifiedNetError):
    """The impossible-route check did not return a deterministic PASS or FAIL.

    An INSUFFICIENT or UNKNOWN verdict means the evidence itself was missing or
    unusable (an infrastructure/collection problem), NOT a real precondition
    failure. It is raised loudly and never silently converted into a
    ``PRECONDITION_FAILED`` record — only a deterministic FAIL may reject.
    """


class RejectedPreconditionRun:
    """Evaluate one impossible precondition and build the rejected record.

    Mirrors the accepted scenario's precondition step, but the check is designed
    to fail on a healthy lab and NO ``PRECHECKED`` transition is ever appended.
    """

    def __init__(
        self,
        *,
        executor: ReadOnlyExec,
        topology: TopologySpec,
        scenario: ScenarioDefinition,
        run_ctx: RunContext,
        ledger: Ledger,
        verifier: Verifier,
        target_node: str,
        peer_node: str,
        impossible_prefix: str = DEFAULT_IMPOSSIBLE_PREFIX,
        command_timeout_s: float = 10.0,
    ) -> None:
        self._executor = executor
        self._topology = topology
        self._scenario = scenario
        self._run_ctx = run_ctx
        self._ledger = ledger
        self._verifier = verifier
        self._target_node = target_node
        self._impossible_prefix = impossible_prefix
        self._timeout_s = command_timeout_s
        self._provider = LiveScenarioEvidenceProvider(
            executor=executor,
            topology=topology,
            run_ctx=run_ctx,
            target_node=target_node,
            peer_node=peer_node,
            command_timeout_s=command_timeout_s,
        )
        self._baseline: EvidenceBundle | None = None

    @property
    def baseline(self) -> EvidenceBundle | None:
        return self._baseline

    def collect_baseline(self) -> EvidenceBundle:
        """Healthy baseline (both routers) PLUS the impossible-route observation.

        Reuses the live provider for the healthy facts and one additional
        ``RoutePresenceCollector`` for the impossible prefix; nothing is parsed
        by hand. Returns one sealed PRECONDITION bundle.
        """
        healthy = self._provider(Phase.PRECONDITION)[0]
        impossible_record = RoutePresenceCollector(
            self._executor,
            self._target_node,
            self._run_ctx,
            prefixes=(self._impossible_prefix,),
            timeout_s=self._timeout_s,
        ).collect(Phase.PRECONDITION)
        records = (*healthy.records, impossible_record)
        bundle_id = self._run_ctx.content_id(
            "bundle",
            {"phase": "precondition-rejected", "evidence": [r.evidence_id for r in records]},
        )
        return EvidenceBundle(
            bundle_id=bundle_id, phase=Phase.PRECONDITION, records=records
        ).seal()

    def validate_preconditions(self) -> None:
        """Evaluate the impossible precondition; RAISE on the intended failure.

        NEVER appends ``PRECHECKED``: on failure the ledger stays ``PENDING``.
        Raises :class:`ImpossiblePreconditionSatisfiedError` if the route is
        unexpectedly present (setup/health error), otherwise raises
        ``PreconditionResultsError`` carrying the failing verdict.
        """
        if self._ledger.current is not LifecyclePhase.PENDING:
            raise PhaseTransitionError(
                f"rejected precondition requires PENDING, ledger is {self._ledger.current}"
            )
        self._baseline = self.collect_baseline()
        check = checks.route_present(
            self._target_node, self._impossible_prefix, Phase.PRECONDITION
        )
        result = self._verifier.verify(check, (self._baseline,))
        if result.verdict is Verdict.PASS:
            raise ImpossiblePreconditionSatisfiedError(
                f"impossible precondition unexpectedly satisfied: route "
                f"{self._impossible_prefix} is present on {self._target_node}"
            )
        if result.verdict is not Verdict.FAIL:
            # INSUFFICIENT / UNKNOWN: the evidence was missing or unusable — an
            # infrastructure problem, not a real precondition failure.
            raise NonDeterministicRejectionError(
                f"impossible-route check returned {result.verdict.value}, not a "
                f"deterministic FAIL (detail: {result.detail!r}); refusing to record "
                "a PRECONDITION_FAILED rejection from non-deterministic evidence"
            )
        raise PreconditionResultsError(
            f"required route {self._impossible_prefix} was absent on {self._target_node}",
            (result,),
        )

    def execute(
        self, *, provenance: ProvenanceInfo, cleanup_status: str = "clean"
    ) -> IncidentRecord:
        """Run validation and build the honest rejected record on failure."""
        try:
            self.validate_preconditions()
        except PreconditionResultsError as exc:
            assert self._baseline is not None
            return build_rejected_record(
                run_ctx=self._run_ctx,
                scenario=self._scenario,
                topology=self._topology,
                baseline=self._baseline,
                rejection_code=RejectionCode.PRECONDITION_FAILED,
                details=str(exc),
                failed_phase="precondition",
                fault=None,
                onset=None,
                recovery=None,
                precondition_results=_results(exc),
                restoration=None,
                provenance=provenance,
                completed_phases=(),
                cleanup_status=cleanup_status,  # type: ignore[arg-type]
            )
        raise AssertionError(  # pragma: no cover - validate_preconditions always raises
            "validate_preconditions must raise on the impossible precondition"
        )


def _results(exc: PreconditionResultsError) -> Sequence[VerificationResult]:
    return exc.results
