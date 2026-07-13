"""BGP prefix-advertisement withdrawal fault scenario (Gate 5.4).

Routing-intent fault family — deliberately distinct from every earlier family:
the BGP session stays ESTABLISHED throughout. Only one advertised IPv4-unicast
prefix (the target's own loopback) is withdrawn, so the peer loses exactly that
route while the session, all other routes, and link reachability are
unaffected. There is NO session flap and therefore NO forced reset
(``RestorationMetadata.forced_reset_used = False`` — the first family to
exercise that path).

Deterministic truth: the PEER's ``show ip route json`` no longer carries the
withdrawn prefix (affirmative ``route.<prefix>.present = "false"`` from the
requested-prefix collector), while ``bgp_established`` on BOTH nodes is an
onset INVARIANT — the signature of this family. Recovery re-advertises the
prefix and proves byte-identical running-config on the target.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from verifiednet.common.errors import (
    InjectFailedError,
    OnsetNotVerifiedError,
    PhaseTransitionError,
    RecoveryNotVerifiedError,
    RestoreFailedError,
)
from verifiednet.common.runctx import RunContext
from verifiednet.faults.frr_commands import restore_network_argv, withdraw_network_argv
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


class BgpPrefixWithdrawalScenario:
    """Withdraw / verify / restore one advertised prefix on the target node."""

    method = "vtysh-no-network"
    restore_method = "vtysh-network-readvertise"

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

        mine, theirs = self._session_endpoints()
        self._peer_ip = mine.peer_ip
        self._peer_node = theirs.node
        self._local_asn = topology.node(self._target_node).asn
        self._target_loopback = topology.node(self._target_node).loopback
        self._peer_loopback = topology.node(self._peer_node).loopback
        # The withdrawn prefix defaults to the target's own advertised loopback.
        self._prefix = str(params.get("prefix", self._target_loopback))

        self._fault: FaultInjection | None = None
        self._restoration: RestorationMetadata | None = None
        self._peer_config_sha: str | None = None
        self._target_config_sha: str | None = None

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

    def _find_config_sha(self, bundles: Sequence[EvidenceBundle], node: str) -> str | None:
        for bundle in bundles:
            for record in bundle.records:
                if (
                    record.source.target == node
                    and record.source.trusted
                    and _CONFIG_METRIC in record.normalized
                ):
                    return str(record.normalized[_CONFIG_METRIC])
        return None

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
            # the advertised prefix must be visible on the PEER before withdrawal
            checks.route_present(self._peer_node, self._prefix, Phase.PRECONDITION),
        )
        results = tuple(self._verifier.verify(check, bundles) for check in check_list)
        if not all(result.committable for result in results):
            failing = [r.check_id for r in results if not r.committable]
            raise PreconditionResultsError(f"preconditions failed: {failing}", results)
        self._peer_config_sha = self._find_config_sha(bundles, self._peer_node)
        self._target_config_sha = self._find_config_sha(bundles, self._target_node)
        self._ledger.append(LifecyclePhase.PRECHECKED, "all precondition checks passed")
        return results

    def inject(self) -> FaultInjection:
        if self._ledger.current is not LifecyclePhase.PRECHECKED:
            raise PhaseTransitionError(
                f"inject requires PRECHECKED, ledger is {self._ledger.current}"
            )
        self._ledger.append(LifecyclePhase.INJECTING, f"withdraw network {self._prefix}")
        result = self._mutation.run(
            self._target_node,
            withdraw_network_argv(self._local_asn, self._prefix),
            timeout_s=self._scenario.timeouts.command_s,
        )
        if result.status is not ExecStatus.OK:
            raise InjectFailedError(
                f"injection command failed with {result.status}: "
                f"{result.detail or result.stderr}"
            )
        self._ledger.append(LifecyclePhase.INJECTED, "prefix withdrawn")
        self._fault = FaultInjection(
            scenario_id=self._scenario.scenario_id,
            template_id=self._scenario.template_id,
            target_node=self._target_node,
            target_session=self._target_session,
            method=self.method,
            parameter_name="network",
            before_value=f"{self._prefix} advertised",
            after_value="withdrawn",
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
        # Signature of this family: the route disappears on the peer while the
        # session stays Established on BOTH sides (onset invariant).
        onset_checks = (
            checks.route_absent(self._peer_node, self._prefix, Phase.ONSET),
            checks.bgp_established(self._target_node, self._peer_ip, Phase.ONSET),
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
        # Invariants on the final sample: the peer still sees the session up
        # (its neighbor address for the target), the peer's OWN advertisement
        # is unaffected (unrelated-route invariant), and its config is intact.
        _, theirs = self._session_endpoints()
        post_checks = [
            checks.bgp_established(self._peer_node, theirs.peer_ip, Phase.ONSET),
            checks.route_present(self._target_node, self._peer_loopback, Phase.ONSET),
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
        self._ledger.append(LifecyclePhase.RESTORING, f"re-advertise network {self._prefix}")
        revert = self._mutation.run(
            self._target_node,
            restore_network_argv(self._local_asn, self._prefix),
            timeout_s=self._scenario.timeouts.command_s,
        )
        if revert.status is not ExecStatus.OK:
            raise RestoreFailedError(
                f"restore command failed with {revert.status}: "
                f"{revert.detail or revert.stderr}"
            )
        # NO forced reset: the session never dropped, so re-advertisement alone
        # restores the route. This is the distinguishing restoration path.
        self._ledger.append(LifecyclePhase.RESTORED, "prefix re-advertised")
        self._restoration = RestorationMetadata(
            method=self.restore_method,
            forced_reset_used=False,
            forced_reset_command="",
            transcript_refs=(revert.seq,),
            completed=True,
            attempted=True,
        )
        return self._restoration

    def verify_recovery(self) -> tuple[VerificationResult, ...]:
        if self._ledger.current is not LifecyclePhase.RESTORED:
            raise PhaseTransitionError(
                f"verify_recovery requires RESTORED, ledger is {self._ledger.current}"
            )
        recovery_checks = (
            checks.route_present(self._peer_node, self._prefix, Phase.RECOVERY),
            checks.bgp_established(self._target_node, self._peer_ip, Phase.RECOVERY),
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
        final_checks = [
            checks.reachability_ok(self._target_node, self._peer_ip, Phase.RECOVERY),
        ]
        if self._target_config_sha is not None:
            final_checks.append(
                checks.config_unchanged(
                    self._target_node, self._target_config_sha, Phase.RECOVERY
                )
            )
        final_results = [self._verifier.verify(check, last_bundles) for check in final_checks]
        self._ledger.append(LifecyclePhase.RECOVERY_VERIFIED, "recovery checks satisfied twice")
        return tuple(last_results) + tuple(final_results)
