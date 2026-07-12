"""Property tests for the pure renderers over generated valid topologies."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.labs.frr.render import render_compose, render_frr_conf
from verifiednet.schemas import (
    BgpSessionSpec,
    ImageSpec,
    LinkEndpoint,
    LinkSpec,
    NodeSpec,
    SessionEndpoint,
    TopologySpec,
)

pytestmark = pytest.mark.property

SETTINGS = settings(max_examples=25, deadline=None, derandomize=True)

_NAME = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=10)


@st.composite
def two_node_topologies(draw: st.DrawFn) -> TopologySpec:
    name_a = draw(_NAME)
    name_b = draw(_NAME.filter(lambda n: n != name_a))
    asn_a = draw(st.integers(min_value=64512, max_value=65534))
    asn_b = draw(st.integers(min_value=64512, max_value=65534).filter(lambda a: a != asn_a))
    octet = draw(st.integers(min_value=0, max_value=255))
    return TopologySpec(
        name="prop-topo",
        backend="frr-compose",
        nodes=(
            NodeSpec(name=name_a, asn=asn_a, loopback="10.255.0.1/32"),
            NodeSpec(name=name_b, asn=asn_b, loopback="10.255.0.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node=name_a, iface="eth1", ip=f"172.30.{octet}.1/30"),
                b=LinkEndpoint(node=name_b, iface="eth1", ip=f"172.30.{octet}.2/30"),
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


@SETTINGS
@given(topo=two_node_topologies())
def test_frr_conf_deterministic_and_has_session_neighbors(topo: TopologySpec) -> None:
    for node in topo.nodes:
        first = render_frr_conf(topo, node.name)
        second = render_frr_conf(topo, node.name)
        assert first == second
        assert f"router bgp {node.asn}" in first.splitlines()
    for sess in topo.sessions:
        for endpoint in (sess.a, sess.b):
            conf = render_frr_conf(topo, endpoint.node)
            neighbor = f" neighbor {endpoint.peer_ip} remote-as {endpoint.remote_as}"
            assert neighbor in conf.splitlines()


@SETTINGS
@given(topo=two_node_topologies())
def test_compose_deterministic_and_contains_each_endpoint_ip_once(
    topo: TopologySpec,
) -> None:
    compose = render_compose(topo)
    assert compose == render_compose(topo)
    for link in topo.links:
        for endpoint in (link.a, link.b):
            address = endpoint.ip.split("/")[0]
            assert compose.count(f"ipv4_address: {address}\n") == 1
