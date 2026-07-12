"""BGP remote-as mismatch fault scenario for the two-router lab (Wave A).

Provenance: fault shape from sonic-troubleshooting-agent
``faults/bgp_asn_mismatch.py`` (MIT, commit eb4c818) — copy with
modifications: re-targeted at plain FRR, driven through the append-only
lifecycle ledger, verified with deterministic evidence-based checks instead
of ad-hoc CLI greps, and made fully injectable (executor, evidence provider,
clock, sleep) so Gate 3 tests run without any lab.

Evidence arrives through ``evidence_provider: Callable[[Phase],
Sequence[EvidenceBundle]]`` — the Gate 4 orchestrator wires it to real
collectors; Gate 3 tests inject fakes. Faults therefore never import the
collectors package (allowed by policy but avoided for parallel-build safety).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Sequence

from verifiednet.common.errors import (
    InjectFailedError,
    OnsetNotVerifiedError,
    PhaseTransitionError,
    RecoveryNotVerifiedError,
    RestoreFailedError,
)
from verifiednet.common.runctx import RunContext
from verifiednet.faults.frr_commands import clear_bgp_argv, set_remote_as_argv
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.faults.scenario import MutationExec, PreconditionResultsError
from verifiednet.runtime.results import ExecStatus
from verifiednet.schemas.evidence import EvidenceBundle, Phase
from verifiednet.schemas.fault import FaultInjection
from verifiednet.schemas.incident import RestorationMetadata
from verifiednet.schemas.scenario import ScenarioDefinition
from verifiednet.schemas.topology import SessionEndpoint, TopologySpec
from verifiednet.schemas.verification import VerificationResult
from verifiednet.verifiers import checks
from verifiednet.verifiers.claims import ClaimVerifier
from verifiednet.verifiers.polling import poll_until

EvidenceProvider = Callable[[Phase], Sequence[EvidenceBundle]]

_CONFIG_METRIC = "config.sha256"


class BgpRemoteAsMismatchScenario:
    """Inject/verify/restore a wrong ``remote-as`` on one eBGP session."""

    method = "vtysh-remote-as"
    restore_method = "vtysh-remote-as-revert"

    def __init__(
        self,
        *,
        topology: TopologySpec,
        scenario: ScenarioDefinition,
        mutation: MutationExec,
        ledger: Ledger,
        run_ctx: RunContext,
        evidence_provider: EvidenceProvider,
        verifier: ClaimVerifier,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._topology = topology
        self._scenario = scenario
        self._mutation = mutation
        self._ledger = ledger
        self._run_ctx = run_ctx
        self._evidence_provider = evidence_provider
        self._verifier = verifier
        self._monotonic = monotonic
        self._sleep = sleep

        params = scenario.parameters
        self._target_node = str(params["target_node"])
        self._target_session = str(params["target_session"])
        self._wrong_asn = int(params["wrong_asn"])

        mine, theirs = self._session_endpoints()
        self._peer_ip = mine.peer_ip
        self._correct_remote_as = mine.remote_as  # topology-declared truth
        self._peer_node = theirs.node
        self._local_asn = topology.node(self._target_node).asn
        self._actual_peer_asn = topology.node(self._peer_node).asn
        self._iface = self._target_iface()
        self._peer_loopback = topology.node(self._peer_node).loopback
        self._target_loopback = topology.node(self._target_node).loopback

        self._fault: FaultInjection | None = None
        self._restoration: RestorationMetadata | None = None
        self._peer_config_sha: str | None = None

    # ------------------------------------------------------------------ setup

    def _session_endpoints(self) -> tuple[SessionEndpoint, SessionEndpoint]:
        for session in self._topology.sessions:
            if session.session_id != self._target_session:
                continue
            if session.a.node == self._target_node:
                return session.a, session.b
            if session.b.node == self._target_node:
                return session.b, session.a
            raise ValueError(
                f"node {self._target_node!r} is not an endpoint of session "
                f"{self._target_session!r}"
            )
        raise ValueError(f"unknown session: {self._target_session!r}")

    def _target_iface(self) -> str:
        peer_addr = ipaddress.ip_address(self._peer_ip)
        for link in self._topology.links:
            for mine, theirs in ((link.a, link.b), (link.b, link.a)):
                if mine.node == self._target_node and (
                    ipaddress.ip_interface(theirs.ip).ip == peer_addr
                ):
                    return mine.iface
        raise ValueError(
            f"no link on {self._target_node!r} faces peer {self._peer_ip!r}"
        )

    # ------------------------------------------------------------- lifecycle

    def validate_preconditions(self) -> tuple[VerificationResult, ...]:
        if self._ledger.current is not LifecyclePhase.PENDING:
            raise PhaseTransitionError(
                f"preconditions require PENDING, ledger is {self._ledger.current}"
            )
        bundles = self._evidence_provider(Phase.PRECONDITION)
        check_list = (
            checks.bgp_established(self._target_node, self._peer_ip, Phase.PRECONDITION),
            checks.reachability_ok(self._target_node, self._peer_ip, Phase.PRECONDITION),
            checks.iface_operational(self._target_node, self._iface, Phase.PRECONDITION),
            checks.route_present(self._target_node, self._peer_loopback, Phase.PRECONDITION),
        )
        results = tuple(self._verifier.verify(check, bundles) for check in check_list)
        if not all(result.committable for result in results):
            failing = [r.check_id for r in results if not r.committable]
            raise PreconditionResultsError(
                f"preconditions failed: {failing}", results
            )
        self._peer_config_sha = self._find_peer_config_sha(bundles)
        self._ledger.append(LifecyclePhase.PRECHECKED, "all precondition checks passed")
        return results

    def _find_peer_config_sha(self, bundles: Sequence[EvidenceBundle]) -> str | None:
        for bundle in bundles:
            for record in bundle.records:
                if (
                    record.source.target == self._peer_node
                    and record.source.trusted
                    and _CONFIG_METRIC in record.normalized
                ):
                    return str(record.normalized[_CONFIG_METRIC])
        return None

    def inject(self) -> FaultInjection:
        if self._ledger.current is not LifecyclePhase.PRECHECKED:
            raise PhaseTransitionError(
                f"inject requires PRECHECKED, ledger is {self._ledger.current}"
            )
        self._ledger.append(LifecyclePhase.INJECTING, f"set remote-as {self._wrong_asn}")
        result = self._mutation.run(
            self._target_node,
            set_remote_as_argv(self._local_asn, self._peer_ip, self._wrong_asn),
            timeout_s=self._scenario.timeouts.command_s,
        )
        if result.status is not ExecStatus.OK:
            # Ledger deliberately stays in INJECTING: the failure is visible.
            raise InjectFailedError(
                f"injection command failed with {result.status}: "
                f"{result.detail or result.stderr}"
            )
        self._ledger.append(LifecyclePhase.INJECTED, "remote-as mutated")
        self._fault = FaultInjection(
            scenario_id=self._scenario.scenario_id,
            template_id=self._scenario.template_id,
            target_node=self._target_node,
            target_session=self._target_session,
            method=self.method,
            parameter_name="remote_as",
            before_value=str(self._correct_remote_as),
            after_value=str(self._wrong_asn),
            transcript_refs=(result.seq,),
            injected_at_seq=self._run_ctx.next_seq(),
            injected_at=self._run_ctx.now(),
        )
        return self._fault

    def verify_onset(self) -> tuple[VerificationResult, ...]:
        if self._ledger.current is not LifecyclePhase.INJECTED:
            raise PhaseTransitionError(
                f"verify_onset requires INJECTED, ledger is {self._ledger.current}"
            )
        onset_checks = (
            checks.bgp_not_established(self._target_node, self._peer_ip, Phase.ONSET),
            checks.remote_as_equals(self._target_node, self._peer_ip, self._wrong_asn, Phase.ONSET),
        )
        last_results: list[VerificationResult] = []
        last_bundles: Sequence[EvidenceBundle] = ()

        def sample() -> bool:
            nonlocal last_results, last_bundles
            last_bundles = self._evidence_provider(Phase.ONSET)
            last_results = [self._verifier.verify(check, last_bundles) for check in onset_checks]
            return all(result.committable for result in last_results)

        outcome = poll_until(
            sample,
            timeout_s=self._scenario.timeouts.onset_s,
            interval_s=self._scenario.timeouts.poll_interval_s,
            monotonic=self._monotonic,
            sleep=self._sleep,
            consecutive=2,
        )
        if not outcome.satisfied:
            raise OnsetNotVerifiedError(
                f"onset not verified after {outcome.attempts} attempts "
                f"({outcome.elapsed_s:.1f}s): {outcome.last_detail}"
            )
        post_checks = [
            checks.iface_operational(self._target_node, self._iface, Phase.ONSET),
            checks.reachability_ok(self._target_node, self._peer_ip, Phase.ONSET),
        ]
        if self._peer_config_sha is not None:
            post_checks.append(
                checks.config_unchanged(self._peer_node, self._peer_config_sha, Phase.ONSET)
            )
        post_results = [self._verifier.verify(check, last_bundles) for check in post_checks]
        self._ledger.append(LifecyclePhase.ONSET_VERIFIED, "onset checks satisfied twice")
        return tuple(last_results) + tuple(post_results)

    def restore(self) -> RestorationMetadata:
        if self._ledger.current in (LifecyclePhase.RESTORED, LifecyclePhase.RECOVERY_VERIFIED):
            assert self._restoration is not None
            return self._restoration  # safe no-op: no further mutation commands
        # Legal from INJECTING (mutation-failure recovery), INJECTED and
        # ONSET_VERIFIED; the ledger enforces this.
        self._ledger.append(
            LifecyclePhase.RESTORING, f"revert remote-as to {self._correct_remote_as}"
        )
        revert = self._mutation.run(
            self._target_node,
            set_remote_as_argv(self._local_asn, self._peer_ip, self._correct_remote_as),
            timeout_s=self._scenario.timeouts.command_s,
        )
        if revert.status is not ExecStatus.OK:
            # Ledger deliberately stays in RESTORING: visible, rejected downstream.
            raise RestoreFailedError(
                f"restore command failed with {revert.status}: "
                f"{revert.detail or revert.stderr}"
            )
        forced = self._mutation.run(
            self._target_node,
            clear_bgp_argv(self._peer_ip),
            timeout_s=self._scenario.timeouts.command_s,
        )
        failure_reason = ""
        if forced.status is not ExecStatus.OK:
            # Non-fatal for configuration state; recorded for the caller.
            failure_reason = (
                f"forced reset failed with {forced.status}: {forced.detail or forced.stderr}"
            )
        self._ledger.append(LifecyclePhase.RESTORED, "remote-as reverted")
        self._restoration = RestorationMetadata(
            method=self.restore_method,
            forced_reset_used=True,
            forced_reset_command=f"clear bgp {self._peer_ip}",
            transcript_refs=(revert.seq, forced.seq),
            completed=True,
            failure_reason=failure_reason,
            attempted=True,
        )
        return self._restoration

    def verify_recovery(self) -> tuple[VerificationResult, ...]:
        if self._ledger.current is not LifecyclePhase.RESTORED:
            raise PhaseTransitionError(
                f"verify_recovery requires RESTORED, ledger is {self._ledger.current}"
            )
        recovery_checks = (
            checks.bgp_established(self._target_node, self._peer_ip, Phase.RECOVERY),
            checks.remote_as_equals(
                self._target_node, self._peer_ip, self._correct_remote_as, Phase.RECOVERY
            ),
        )
        last_results: list[VerificationResult] = []
        last_bundles: Sequence[EvidenceBundle] = ()

        def sample() -> bool:
            nonlocal last_results, last_bundles
            last_bundles = self._evidence_provider(Phase.RECOVERY)
            last_results = [
                self._verifier.verify(check, last_bundles) for check in recovery_checks
            ]
            return all(result.committable for result in last_results)

        outcome = poll_until(
            sample,
            timeout_s=self._scenario.timeouts.recovery_s,
            interval_s=self._scenario.timeouts.poll_interval_s,
            monotonic=self._monotonic,
            sleep=self._sleep,
            consecutive=2,
        )
        if not outcome.satisfied:
            raise RecoveryNotVerifiedError(
                f"recovery not verified after {outcome.attempts} attempts "
                f"({outcome.elapsed_s:.1f}s): {outcome.last_detail}"
            )
        route_checks = (
            checks.route_present(self._target_node, self._peer_loopback, Phase.RECOVERY),
            checks.route_present(self._peer_node, self._target_loopback, Phase.RECOVERY),
        )
        route_results = [self._verifier.verify(check, last_bundles) for check in route_checks]
        self._ledger.append(LifecyclePhase.RECOVERY_VERIFIED, "recovery checks satisfied twice")
        return tuple(last_results) + tuple(route_results)
