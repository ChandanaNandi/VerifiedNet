"""Unit tests for the pure FRR renderers (Gate 3 Step 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.labs.frr.render import (
    render_all,
    render_compose,
    render_daemons,
    render_frr_conf,
    write_rendered,
)
from verifiednet.schemas import TopologySpec

pytestmark = pytest.mark.unit


def test_render_all_is_byte_identical_across_calls(
    two_router_topology: TopologySpec,
) -> None:
    first = render_all(two_router_topology)
    second = render_all(two_router_topology)
    assert first == second
    assert list(first) == list(second)
    for key in first:
        assert first[key].encode("utf-8") == second[key].encode("utf-8")


def test_render_all_keys(two_router_topology: TopologySpec) -> None:
    rendered = render_all(two_router_topology)
    assert set(rendered) == {
        "daemons",
        "router_a/frr.conf",
        "router_b/frr.conf",
        "docker-compose.yml",
    }


def test_daemons_file_idiom() -> None:
    daemons = render_daemons()
    lines = daemons.splitlines()
    assert lines[0] == "vtysh_enable=yes"
    assert lines[1] == "bgpd=yes"
    assert "ospfd=no" in lines
    assert "bfdd=no" in lines
    assert daemons.endswith("\n")
    assert render_daemons() == daemons


def test_frr_conf_contains_exact_idiom_lines_for_router_a(
    two_router_topology: TopologySpec,
) -> None:
    conf = render_frr_conf(two_router_topology, "router_a")
    lines = conf.splitlines()
    assert "hostname router_a" in lines
    assert "router bgp 65001" in lines
    assert " no bgp default ipv4-unicast" in lines
    assert " no bgp ebgp-requires-policy" in lines
    assert " neighbor 172.30.0.2 remote-as 65002" in lines
    assert "  neighbor 172.30.0.2 activate" in lines
    assert "  network 10.255.0.1/32" in lines
    assert " exit-address-family" in lines
    assert "line vty" in lines
    assert "interface lo" in lines
    assert " ip address 10.255.0.1/32" in lines
    assert "interface eth1" in lines
    assert " ip address 172.30.0.1/30" in lines


def test_frr_conf_unknown_node_raises_key_error(
    two_router_topology: TopologySpec,
) -> None:
    with pytest.raises(KeyError):
        render_frr_conf(two_router_topology, "router_zz")


def test_compose_shape(two_router_topology: TopologySpec) -> None:
    compose = render_compose(two_router_topology)
    assert "container_name" not in compose
    assert "SYS_ADMIN" not in compose
    assert "- NET_ADMIN" in compose
    assert "image: frrouting/frr:v8.4.1" in compose
    assert "ipv4_address: 172.30.0.1\n" in compose
    assert "ipv4_address: 172.30.0.2\n" in compose
    assert "- subnet: 172.30.0.0/30" in compose
    assert "  link0:" in compose
    # config mounts are a Gate 4 concern, noted in a comment
    assert "Gate 4" in compose


def test_write_rendered_writes_into_tmp_path(
    tmp_path: Path, two_router_topology: TopologySpec
) -> None:
    rendered = render_all(two_router_topology)
    written = write_rendered(rendered, tmp_path)
    assert written == [tmp_path / name for name in sorted(rendered)]
    for path in written:
        assert path.is_file()
        assert tmp_path in path.parents
    assert (tmp_path / "daemons").read_text(encoding="utf-8") == render_daemons()
    assert (tmp_path / "router_a" / "frr.conf").read_text(encoding="utf-8") == (
        render_frr_conf(two_router_topology, "router_a")
    )
