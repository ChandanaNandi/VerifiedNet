"""Property tests for TopologySpec (Gate 3): generated valid/invalid topologies."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas import (
    BgpSessionSpec,
    ImageSpec,
    LinkEndpoint,
    LinkSpec,
    NodeSpec,
    ScenarioDefinition,
    SessionEndpoint,
    TopologySpec,
)

pytestmark = pytest.mark.property

SETTINGS = settings(max_examples=25, deadline=None, derandomize=True)

_NAME = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=10)


def _build_two_node(
    name_a: str, name_b: str, asn_a: int, asn_b: int, octet: int
) -> TopologySpec:
    ip_a = f"172.30.{octet}.1/30"
    ip_b = f"172.30.{octet}.2/30"
    return TopologySpec(
        name="prop-topo",
        backend="frr-compose",
        nodes=(
            NodeSpec(name=name_a, asn=asn_a, loopback="10.255.0.1/32"),
            NodeSpec(name=name_b, asn=asn_b, loopback="10.255.0.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node=name_a, iface="eth1", ip=ip_a),
                b=LinkEndpoint(node=name_b, iface="eth1", ip=ip_b),
            ),
        ),
        sessions=(
            BgpSessionSpec(
                session_id="a-b",
                a=SessionEndpoint(
                    node=name_a, peer_ip=f"172.30.{octet}.2", remote_as=asn_b
                ),
                b=SessionEndpoint(
                    node=name_b, peer_ip=f"172.30.{octet}.1", remote_as=asn_a
                ),
            ),
        ),
        images=ImageSpec(frr="frrouting/frr:v8.4.1"),
    )


@st.composite
def two_node_params(draw: st.DrawFn) -> tuple[str, str, int, int, int]:
    name_a = draw(_NAME)
    name_b = draw(_NAME.filter(lambda n: n != name_a))
    asn_a = draw(st.integers(min_value=64512, max_value=65534))
    asn_b = draw(st.integers(min_value=64512, max_value=65534).filter(lambda a: a != asn_a))
    octet = draw(st.integers(min_value=0, max_value=255))
    return name_a, name_b, asn_a, asn_b, octet


@SETTINGS
@given(params=two_node_params())
def test_generated_topologies_accepted_and_hash_stable(
    params: tuple[str, str, int, int, int],
) -> None:
    topo = _build_two_node(*params)
    rebuilt = _build_two_node(*params)
    assert topo == rebuilt
    assert sha256_canonical(topo) == sha256_canonical(rebuilt)
    # canonical hash is sensitive to content
    name_a, name_b, asn_a, asn_b, octet = params
    other_octet = (octet + 1) % 256
    different = _build_two_node(name_a, name_b, asn_a, asn_b, other_octet)
    assert sha256_canonical(topo) != sha256_canonical(different)


@SETTINGS
@given(params=two_node_params())
def test_duplicate_node_names_rejected(
    params: tuple[str, str, int, int, int],
) -> None:
    name_a, name_b, asn_a, asn_b, octet = params
    valid = _build_two_node(name_a, name_b, asn_a, asn_b, octet)
    duplicate = NodeSpec(name=name_b, asn=asn_b, loopback="10.255.0.3/32")
    with pytest.raises(ValidationError, match="unique"):
        TopologySpec(
            name=valid.name,
            backend=valid.backend,
            nodes=(*valid.nodes, duplicate),
            links=valid.links,
            sessions=valid.sessions,
            images=valid.images,
        )


def test_remote_as_mismatch_rejected() -> None:
    """A session remote_as that disagrees with the peer node's ASN is rejected."""
    with pytest.raises(ValidationError, match="remote_as"):
        TopologySpec(
            name="bad-remote-as",
            backend="frr-compose",
            nodes=(
                NodeSpec(name="ra", asn=65001, loopback="10.255.0.1/32"),
                NodeSpec(name="rb", asn=65002, loopback="10.255.0.2/32"),
            ),
            links=(
                LinkSpec(
                    a=LinkEndpoint(node="ra", iface="eth1", ip="172.30.0.1/30"),
                    b=LinkEndpoint(node="rb", iface="eth1", ip="172.30.0.2/30"),
                ),
            ),
            sessions=(
                BgpSessionSpec(
                    session_id="a-b",
                    a=SessionEndpoint(node="ra", peer_ip="172.30.0.2", remote_as=65999),
                    b=SessionEndpoint(node="rb", peer_ip="172.30.0.1", remote_as=65001),
                ),
            ),
            images=ImageSpec(frr="frrouting/frr:v8.4.1"),
        )


def test_slash_29_link_rejected() -> None:
    with pytest.raises(ValidationError, match="/30"):
        LinkSpec(
            a=LinkEndpoint(node="ra", iface="eth1", ip="172.30.0.1/29"),
            b=LinkEndpoint(node="rb", iface="eth1", ip="172.30.0.2/29"),
        )


def test_link_same_endpoint_ip_rejected() -> None:
    with pytest.raises(ValidationError, match="distinct"):
        LinkSpec(
            a=LinkEndpoint(node="ra", iface="eth1", ip="172.30.0.1/30"),
            b=LinkEndpoint(node="rb", iface="eth1", ip="172.30.0.1/30"),
        )


def test_session_peer_ip_not_on_any_link_rejected() -> None:
    with pytest.raises(ValidationError, match="not a link endpoint"):
        TopologySpec(
            name="bad-peer-ip",
            backend="frr-compose",
            nodes=(
                NodeSpec(name="ra", asn=65001, loopback="10.255.0.1/32"),
                NodeSpec(name="rb", asn=65002, loopback="10.255.0.2/32"),
            ),
            links=(
                LinkSpec(
                    a=LinkEndpoint(node="ra", iface="eth1", ip="172.30.0.1/30"),
                    b=LinkEndpoint(node="rb", iface="eth1", ip="172.30.0.2/30"),
                ),
            ),
            sessions=(
                BgpSessionSpec(
                    session_id="a-b",
                    a=SessionEndpoint(node="ra", peer_ip="192.0.2.1", remote_as=65002),
                    b=SessionEndpoint(node="rb", peer_ip="172.30.0.1", remote_as=65001),
                ),
            ),
            images=ImageSpec(frr="frrouting/frr:v8.4.1"),
        )


def test_scenario_wrong_asn_differs_from_both_node_asns(
    scenario: ScenarioDefinition, two_router_topology: TopologySpec
) -> None:
    """Wrong-ASN equality guard for the fixture scenario (Gate 3)."""
    wrong_asn = scenario.parameters["wrong_asn"]
    for node in two_router_topology.nodes:
        assert wrong_asn != node.asn
