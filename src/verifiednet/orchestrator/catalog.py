"""Immutable, explicit scenario catalog for the four verified fault families.

Gate 5.5. A hand-maintained static tuple of ``ScenarioCase`` records defines a
SMALL, bounded variation matrix (2-4 cases per family). There is NO plugin
discovery, NO reflection, NO YAML/DSL, NO runtime registration, NO Cartesian
auto-generation, and NO randomness. Adding a case means editing this file in a
reviewed commit.

Each case is validated deterministically against the topology BEFORE any live
execution (``validate_scenario_case``): an invalid case fails loudly, never
gets silently normalized into a different valid case, and can never reach a
mutation. Case data is plain scalars — it can carry no callable and can inject
no arbitrary command (mutations still flow only through the exact per-family
``MutationCommandShape`` allow-list).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from verifiednet.orchestrator.families import binding_for_template
from verifiednet.schemas.scenario import ScenarioDefinition, ScenarioTimeouts
from verifiednet.schemas.topology import SessionEndpoint, TopologySpec

_ASN_MIN, _ASN_MAX = 1, 4294967295
_CIDR_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}$")
_ETH_RE = re.compile(r"^eth\d{1,3}$")

_LIVE_TIMEOUTS = ScenarioTimeouts(
    precondition_s=30.0, onset_s=30.0, recovery_s=60.0, command_s=10.0, poll_interval_s=1.0
)


class ScenarioValidationError(ValueError):
    """A catalog scenario case is invalid for the given topology."""


@dataclass(frozen=True)
class ScenarioCase:
    """One approved, parameterized scenario — pure data, deterministic identity."""

    case_id: str
    scenario: ScenarioDefinition
    expected_target: str
    #: One-line human description (documentation only; never executed).
    description: str = ""

    @property
    def template_id(self) -> str:
        return self.scenario.template_id


def _session_endpoints(
    topology: TopologySpec, target_node: str, target_session: str
) -> tuple[SessionEndpoint, SessionEndpoint]:
    for session in topology.sessions:
        if session.session_id != target_session:
            continue
        if session.a.node == target_node:
            return session.a, session.b
        if session.b.node == target_node:
            return session.b, session.a
        raise ScenarioValidationError(
            f"target {target_node!r} is not an endpoint of session {target_session!r}"
        )
    raise ScenarioValidationError(f"unknown session: {target_session!r}")


def _link_iface(topology: TopologySpec, target_node: str, peer_ip: str) -> str:
    peer_addr = ipaddress.ip_address(peer_ip)
    for link in topology.links:
        for mine, theirs in ((link.a, link.b), (link.b, link.a)):
            if mine.node == target_node and ipaddress.ip_interface(theirs.ip).ip == peer_addr:
                return mine.iface
    raise ScenarioValidationError(
        f"no link on {target_node!r} faces peer {peer_ip!r}"
    )


def validate_scenario_case(case: ScenarioCase, topology: TopologySpec) -> None:
    """Deterministically validate *case* against *topology*, or raise.

    Family-specific, visible, and never-normalizing: an invalid case is a loud
    ``ScenarioValidationError`` before any lab action, not a silent fixup.
    """
    scenario = case.scenario
    params = scenario.parameters
    # The template must resolve to an approved family binding.
    try:
        binding_for_template(scenario.template_id)
    except KeyError as exc:
        raise ScenarioValidationError(str(exc)) from exc

    target_node = str(params.get("target_node", ""))
    target_session = str(params.get("target_session", ""))
    if not target_node:
        raise ScenarioValidationError("missing target_node")
    try:
        topology.node(target_node)
    except KeyError as exc:
        raise ScenarioValidationError(f"unknown target_node: {target_node!r}") from exc
    if case.expected_target != target_node:
        raise ScenarioValidationError(
            f"expected_target {case.expected_target!r} != target_node {target_node!r}"
        )
    # Resolve endpoints (raises on unknown session / bad membership).
    mine, theirs = _session_endpoints(topology, target_node, target_session)
    local_asn = topology.node(target_node).asn
    actual_peer_asn = topology.node(theirs.node).asn

    template = scenario.template_id
    if template == "bgp_remote_as_mismatch":
        _validate_remote_as(params, local_asn, actual_peer_asn)
    elif template == "bgp_neighbor_removal":
        # peer_ip must be derivable; remote-as baseline must exist (topology-declared)
        if not mine.peer_ip:
            raise ScenarioValidationError("peer_ip not derivable for neighbor removal")
        if not isinstance(mine.remote_as, int):
            raise ScenarioValidationError("missing remote-as baseline")
    elif template == "iface_admin_shutdown":
        iface = _link_iface(topology, target_node, mine.peer_ip)
        if not _ETH_RE.match(iface):
            raise ScenarioValidationError(f"interface {iface!r} is not an approved ethN link")
        if iface == "lo":  # pragma: no cover - defensive; _ETH_RE already excludes it
            raise ScenarioValidationError("refusing to shut down the loopback interface")
    elif template == "bgp_prefix_withdrawal":
        _validate_prefix(params, topology, target_node)
    else:  # pragma: no cover - guarded by binding_for_template above
        raise ScenarioValidationError(f"no validation for template {template!r}")


def _validate_remote_as(
    params: dict[str, str | int], local_asn: int, actual_peer_asn: int
) -> None:
    if "wrong_asn" not in params:
        raise ScenarioValidationError("missing wrong_asn")
    try:
        wrong = int(params["wrong_asn"])
    except (TypeError, ValueError) as exc:
        raise ScenarioValidationError(f"wrong_asn is not an integer: {params['wrong_asn']!r}") \
            from exc
    if not (_ASN_MIN <= wrong <= _ASN_MAX):
        raise ScenarioValidationError(f"wrong_asn out of range: {wrong}")
    if wrong == local_asn:
        raise ScenarioValidationError(f"wrong_asn equals local ASN {local_asn}")
    if wrong == actual_peer_asn:
        raise ScenarioValidationError(f"wrong_asn equals the actual peer ASN {actual_peer_asn}")


def _validate_prefix(
    params: dict[str, str | int], topology: TopologySpec, target_node: str
) -> None:
    advertised = topology.node(target_node).loopback  # the only prefix the node advertises
    prefix = str(params.get("prefix", advertised))
    if not _CIDR_RE.match(prefix):
        raise ScenarioValidationError(f"malformed CIDR prefix: {prefix!r}")
    if prefix != advertised:
        raise ScenarioValidationError(
            f"prefix {prefix!r} is not {target_node!r}'s advertised loopback {advertised!r}"
        )


def _remote_as_case(case_id: str, target: str, wrong_asn: int, desc: str) -> ScenarioCase:
    return ScenarioCase(
        case_id=case_id,
        expected_target=target,
        description=desc,
        scenario=ScenarioDefinition(
            scenario_id=f"bgp-remote-as-mismatch-{case_id}",
            family="bgp",
            template_id="bgp_remote_as_mismatch",
            version=1,
            parameters={"wrong_asn": wrong_asn, "target_node": target, "target_session": "a-b"},
            timeouts=_LIVE_TIMEOUTS,
        ),
    )


def _param_case(case_id: str, template: str, family: str, target: str, desc: str,
                extra: dict[str, str | int] | None = None) -> ScenarioCase:
    params: dict[str, str | int] = {"target_node": target, "target_session": "a-b"}
    if extra:
        params.update(extra)
    return ScenarioCase(
        case_id=case_id,
        expected_target=target,
        description=desc,
        scenario=ScenarioDefinition(
            scenario_id=f"{template.replace('_', '-')}-{case_id}",
            family=family,
            template_id=template,
            version=1,
            parameters=params,
            timeouts=_LIVE_TIMEOUTS,
        ),
    )


#: The complete, hand-maintained, bounded scenario catalog (few, deep, restorable).
SCENARIO_CATALOG: tuple[ScenarioCase, ...] = (
    # Family 1 — BGP remote-AS mismatch (3 cases: reference + reverse + alt value)
    _remote_as_case("ras-ref", "router_a", 65999, "reference: wrong remote-as on router_a"),
    _remote_as_case("ras-rev", "router_b", 65998, "reverse orientation on router_b"),
    _remote_as_case("ras-alt", "router_a", 65123, "alternate wrong-ASN value on router_a"),
    # Family 2 — BGP neighbor removal (reference + reverse)
    _param_case("nr-ref", "bgp_neighbor_removal", "bgp", "router_a", "reference on router_a"),
    _param_case("nr-rev", "bgp_neighbor_removal", "bgp", "router_b", "reverse orientation"),
    # Family 3 — interface administrative shutdown (reference + reverse)
    _param_case("if-ref", "iface_admin_shutdown", "interface", "router_a", "reference on router_a"),
    _param_case("if-rev", "iface_admin_shutdown", "interface", "router_b", "reverse orientation"),
    # Family 4 — prefix withdrawal (reference + reverse; prefix = node's own loopback)
    _param_case("pf-ref", "bgp_prefix_withdrawal", "bgp", "router_a",
                "reference: withdraw 10.255.0.1/32", {"prefix": "10.255.0.1/32"}),
    _param_case("pf-rev", "bgp_prefix_withdrawal", "bgp", "router_b",
                "reverse: withdraw 10.255.0.2/32", {"prefix": "10.255.0.2/32"}),
)


#: Gate 14 corpus-expansion additions — approved, bounded, hand-maintained.
#: Kept SEPARATE from ``SCENARIO_CATALOG`` because the per-topology PF cases
#: validate against their own approved topology VARIANT (the prefix must be
#: the target's advertised loopback in that variant), not the default v1
#: topology. ``ras-alt2`` adds a fourth valid wrong-ASN orientation (64700
#: collides with no approved topology ASN).
EXPANSION_SCENARIO_CATALOG: tuple[ScenarioCase, ...] = (
    _remote_as_case("ras-alt2", "router_b", 64700,
                    "expansion: fourth wrong-ASN orientation on router_b"),
    _param_case("pf-t2-ref", "bgp_prefix_withdrawal", "bgp", "router_a",
                "expansion: withdraw 10.255.1.1/32 on 2r-v2",
                {"prefix": "10.255.1.1/32"}),
    _param_case("pf-t2-rev", "bgp_prefix_withdrawal", "bgp", "router_b",
                "expansion: withdraw 10.255.1.2/32 on 2r-v2",
                {"prefix": "10.255.1.2/32"}),
    _param_case("pf-t3-ref", "bgp_prefix_withdrawal", "bgp", "router_a",
                "expansion: withdraw 10.255.2.1/32 on 2r-v3",
                {"prefix": "10.255.2.1/32"}),
    _param_case("pf-t3-rev", "bgp_prefix_withdrawal", "bgp", "router_b",
                "expansion: withdraw 10.255.2.2/32 on 2r-v3",
                {"prefix": "10.255.2.2/32"}),
    # --- Gate 14B additions: identity-first v3 coverage campaign ---
    _remote_as_case("ras-alt3", "router_a", 64800,
                    "expansion: fifth wrong-ASN value on router_a"),
    _remote_as_case("ras-alt4", "router_b", 64900,
                    "expansion: sixth wrong-ASN value on router_b"),
    _remote_as_case("ras-alt5", "router_a", 65450,
                    "expansion: seventh wrong-ASN value on router_a"),
    _remote_as_case("ras-alt6", "router_b", 65550,
                    "expansion: eighth wrong-ASN value on router_b"),
    _remote_as_case("ras-alt7", "router_a", 65650,
                    "expansion: ninth wrong-ASN value on router_a"),
    _remote_as_case("ras-alt8", "router_b", 65750,
                    "expansion: tenth wrong-ASN value on router_b"),
    _param_case("pf-t4-ref", "bgp_prefix_withdrawal", "bgp", "router_a",
                "expansion: withdraw 10.255.3.1/32 on 2r-v4",
                {"prefix": "10.255.3.1/32"}),
    _param_case("pf-t4-rev", "bgp_prefix_withdrawal", "bgp", "router_b",
                "expansion: withdraw 10.255.3.2/32 on 2r-v4",
                {"prefix": "10.255.3.2/32"}),
    _param_case("pf-t5-ref", "bgp_prefix_withdrawal", "bgp", "router_a",
                "expansion: withdraw 10.255.4.1/32 on 2r-v5",
                {"prefix": "10.255.4.1/32"}),
    _param_case("pf-t5-rev", "bgp_prefix_withdrawal", "bgp", "router_b",
                "expansion: withdraw 10.255.4.2/32 on 2r-v5",
                {"prefix": "10.255.4.2/32"}),
    _param_case("pf-t6-ref", "bgp_prefix_withdrawal", "bgp", "router_a",
                "expansion: withdraw 10.255.5.1/32 on 2r-v6",
                {"prefix": "10.255.5.1/32"}),
    _param_case("pf-t6-rev", "bgp_prefix_withdrawal", "bgp", "router_b",
                "expansion: withdraw 10.255.5.2/32 on 2r-v6",
                {"prefix": "10.255.5.2/32"}),
)


def _build_case_index() -> dict[str, ScenarioCase]:
    index: dict[str, ScenarioCase] = {}
    for case in SCENARIO_CATALOG + EXPANSION_SCENARIO_CATALOG:
        if case.case_id in index:
            raise ValueError(f"duplicate case_id in catalog: {case.case_id!r}")
        index[case.case_id] = case
    return index


_CASE_INDEX = _build_case_index()


def case_by_id(case_id: str) -> ScenarioCase:
    """Resolve an approved case by id (only catalog cases are executable)."""
    try:
        return _CASE_INDEX[case_id]
    except KeyError as exc:
        raise KeyError(f"no approved scenario case: {case_id!r}") from exc


def cases_for_template(template_id: str) -> tuple[ScenarioCase, ...]:
    """All catalog cases for one fault family, in deterministic catalog order."""
    return tuple(c for c in SCENARIO_CATALOG if c.template_id == template_id)
