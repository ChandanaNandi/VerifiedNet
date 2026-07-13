"""Check factories for the Wave A scenario family (Gate 3 Step 7).

Metric keys MUST match the collector normalization conventions exactly; a
factory here and a collector parser elsewhere agreeing on the key IS the
contract. Check ids are deterministic:
``f"{template}:{node}:{metric}:{phase}"``.
"""

from __future__ import annotations

from verifiednet.schemas.evidence import Phase
from verifiednet.schemas.verification import Predicate, VerificationCheck


def _check_id(template: str, node: str, metric: str, phase: Phase) -> str:
    return f"{template}:{node}:{metric}:{phase}"


def iface_operational(node: str, iface: str, phase: Phase) -> VerificationCheck:
    metric = f"iface.{iface}.oper"
    return VerificationCheck(
        check_id=_check_id("iface_operational", node, metric, phase),
        claim=f"interface {iface} on {node} is operationally up",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("up",),
        phase=phase,
    )


def iface_admin_up(node: str, iface: str, phase: Phase) -> VerificationCheck:
    metric = f"iface.{iface}.admin"
    return VerificationCheck(
        check_id=_check_id("iface_admin_up", node, metric, phase),
        claim=f"interface {iface} on {node} is administratively up",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("up",),
        phase=phase,
    )


def iface_admin_down(node: str, iface: str, phase: Phase) -> VerificationCheck:
    metric = f"iface.{iface}.admin"
    return VerificationCheck(
        check_id=_check_id("iface_admin_down", node, metric, phase),
        claim=f"interface {iface} on {node} is administratively down",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("down",),
        phase=phase,
    )


def iface_oper_down(node: str, iface: str, phase: Phase) -> VerificationCheck:
    metric = f"iface.{iface}.oper"
    return VerificationCheck(
        check_id=_check_id("iface_oper_down", node, metric, phase),
        claim=f"interface {iface} on {node} is operationally down",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("down",),
        phase=phase,
    )


def reachability_ok(node: str, dst_ip: str, phase: Phase) -> VerificationCheck:
    """3/3 ping policy: every probe must succeed."""
    metric = f"ping.{dst_ip}.all_success"
    return VerificationCheck(
        check_id=_check_id("reachability_ok", node, metric, phase),
        claim=f"{node} reaches {dst_ip} on every ping probe (3/3)",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("true",),
        phase=phase,
    )


def reachability_fails(node: str, dst_ip: str, phase: Phase) -> VerificationCheck:
    """Every probe failed is a fault SYMPTOM proof (affirmative ``"false"``)."""
    metric = f"ping.{dst_ip}.all_success"
    return VerificationCheck(
        check_id=_check_id("reachability_fails", node, metric, phase),
        claim=f"{node} cannot reach {dst_ip} on every ping probe",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("false",),
        phase=phase,
    )


def bgp_peer_present(node: str, peer_ip: str, phase: Phase) -> VerificationCheck:
    """The peer object EXISTS in the BGP summary (affirmative presence).

    Reads the ``bgp.peer.<ip>.present`` metric that ``BgpSummaryCollector``
    emits ONLY when the peer was explicitly requested via ``expected_peers`` —
    presence/absence is an affirmative observation, never inferred from a
    missing metric.
    """
    metric = f"bgp.peer.{peer_ip}.present"
    return VerificationCheck(
        check_id=_check_id("bgp_peer_present", node, metric, phase),
        claim=f"{node} has BGP peer {peer_ip} configured",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("true",),
        phase=phase,
    )


def bgp_peer_absent(node: str, peer_ip: str, phase: Phase) -> VerificationCheck:
    """The peer object DOES NOT exist in the BGP summary (affirmative absence)."""
    metric = f"bgp.peer.{peer_ip}.present"
    return VerificationCheck(
        check_id=_check_id("bgp_peer_absent", node, metric, phase),
        claim=f"{node} has no BGP peer {peer_ip} configured",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("false",),
        phase=phase,
    )


def bgp_established(node: str, peer_ip: str, phase: Phase) -> VerificationCheck:
    metric = f"bgp.peer.{peer_ip}.state"
    return VerificationCheck(
        check_id=_check_id("bgp_established", node, metric, phase),
        claim=f"BGP session {node}->{peer_ip} is Established",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("Established",),
        phase=phase,
    )


def bgp_not_established(node: str, peer_ip: str, phase: Phase) -> VerificationCheck:
    metric = f"bgp.peer.{peer_ip}.state"
    return VerificationCheck(
        check_id=_check_id("bgp_not_established", node, metric, phase),
        claim=f"BGP session {node}->{peer_ip} is down (Idle/Active/Connect)",
        subject=node,
        metric=metric,
        predicate=Predicate.IN_SET,
        expected=("Idle", "Active", "Connect"),
        phase=phase,
    )


def remote_as_equals(node: str, peer_ip: str, expected_asn: int, phase: Phase) -> VerificationCheck:
    metric = f"bgp.peer.{peer_ip}.remote_as"
    return VerificationCheck(
        check_id=_check_id("remote_as_equals", node, metric, phase),
        claim=f"{node} configures remote-as {expected_asn} for peer {peer_ip}",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=(str(expected_asn),),
        phase=phase,
    )


def remote_as_differs(
    node: str, peer_ip: str, actual_peer_asn: int, phase: Phase
) -> VerificationCheck:
    metric = f"bgp.peer.{peer_ip}.remote_as"
    return VerificationCheck(
        check_id=_check_id("remote_as_differs", node, metric, phase),
        claim=f"{node} remote-as for peer {peer_ip} differs from the true ASN {actual_peer_asn}",
        subject=node,
        metric=metric,
        predicate=Predicate.NOT_EQUALS,
        expected=(str(actual_peer_asn),),
        phase=phase,
    )


def config_unchanged(node: str, baseline_sha256: str, phase: Phase) -> VerificationCheck:
    metric = "config.sha256"
    return VerificationCheck(
        check_id=_check_id("config_unchanged", node, metric, phase),
        claim=f"running configuration of {node} matches its baseline hash",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=(baseline_sha256,),
        phase=phase,
    )


def route_present(node: str, prefix: str, phase: Phase) -> VerificationCheck:
    metric = f"route.{prefix}.present"
    return VerificationCheck(
        check_id=_check_id("route_present", node, metric, phase),
        claim=f"{node} has a route to {prefix}",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("true",),
        phase=phase,
    )


def route_absent(node: str, prefix: str, phase: Phase) -> VerificationCheck:
    metric = f"route.{prefix}.present"
    return VerificationCheck(
        check_id=_check_id("route_absent", node, metric, phase),
        claim=f"{node} has no route to {prefix}",
        subject=node,
        metric=metric,
        predicate=Predicate.EQUALS,
        expected=("false",),
        phase=phase,
    )
