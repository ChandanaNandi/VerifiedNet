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
from verifiednet.schemas import ImageSpec, TopologySpec

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
    # Gate 4 live requirement (ADR 0015): FRR 8.4.1 zebra/bgpd abort in
    # privs_init without CAP_SYS_ADMIN in the permitted set — verified live.
    assert "- NET_ADMIN" in compose
    assert "- SYS_ADMIN" in compose
    assert "image: frrouting/frr:v8.4.1" in compose
    assert "ipv4_address: 172.30.0.1\n" in compose
    assert "ipv4_address: 172.30.0.2\n" in compose
    # Docker bridge subnet is widened one bit past the /30 link so the mandatory
    # gateway has a free host (endpoints keep .1/.2; gateway pinned off them).
    assert "- subnet: 172.30.0.0/29" in compose
    assert "gateway: 172.30.0.6" in compose
    assert "  link0:" in compose
    assert "Gate 4" in compose


def test_compose_pins_link_interface_names(two_router_topology: TopologySpec) -> None:
    # With one attached network Docker would name the NIC eth0; the approved
    # topology says eth1, so the attachment pins interface_name (ADR 0015).
    compose = render_compose(two_router_topology)
    assert compose.count("interface_name: eth1") == 2


def test_compose_pins_hostnames_to_node_names(two_router_topology: TopologySpec) -> None:
    compose = render_compose(two_router_topology)
    assert "    hostname: router_a" in compose
    assert "    hostname: router_b" in compose


def test_compose_embeds_each_routers_own_config(
    two_router_topology: TopologySpec,
) -> None:
    compose = render_compose(two_router_topology)
    # top-level inline configs exist for daemons and each router
    assert "\nconfigs:" in compose or compose.startswith("configs:")
    assert "  daemons:" in compose
    assert "  frr_conf_router_a:" in compose
    assert "  frr_conf_router_b:" in compose
    # the embedded content is byte-identical to the standalone renders
    for node in ("router_a", "router_b"):
        conf = render_frr_conf(two_router_topology, node)
        embedded = "\n".join(f"      {line}" for line in conf.splitlines())
        assert embedded in compose
    daemons_embedded = "\n".join(
        f"      {line}" for line in render_daemons().splitlines()
    )
    assert daemons_embedded in compose
    # router A's stanza references router A's config (and B's references B's)
    services_only = compose.split("\nservices:\n")[1].split("\nnetworks:\n")[0]
    a_block = services_only.split("  router_a:")[1].split("  router_b:")[0]
    b_block = services_only.split("  router_b:")[1]
    assert "- source: frr_conf_router_a" in a_block
    assert "- source: frr_conf_router_b" in b_block
    assert "- source: frr_conf_router_b" not in a_block
    assert "- source: frr_conf_router_a" not in b_block


def test_compose_config_targets_and_readonly_mode(
    two_router_topology: TopologySpec,
) -> None:
    compose = render_compose(two_router_topology)
    # verified in-container FRR paths (inspected live from the pinned image)
    assert compose.count("target: /etc/frr/daemons") == 2
    assert compose.count("target: /etc/frr/frr.conf") == 2
    # read-only delivery
    assert compose.count("mode: 0444") == 4


def test_compose_has_no_host_mounts_or_ports(two_router_topology: TopologySpec) -> None:
    compose = render_compose(two_router_topology)
    # no bind mounts of the repository or any host path; no published ports
    assert "volumes" not in compose
    assert "ports" not in compose
    assert "/Users/" not in compose and "/home/" not in compose


def test_compose_retains_digest_pinned_image_reference(
    two_router_topology: TopologySpec,
) -> None:
    pinned = (
        "frrouting/frr:v8.4.1@sha256:"
        "0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e"
    )
    topo = two_router_topology.model_copy(update={"images": ImageSpec(frr=pinned)})
    compose = render_compose(topo)
    assert f"image: {pinned}" in compose


def test_compose_gateway_never_collides_with_endpoint(
    two_router_topology: TopologySpec,
) -> None:
    # Regression: a /30 link gives Docker's bridge gateway the same address as
    # endpoint A (.1), so container creation failed with "Address already in
    # use". The gateway must be pinned to a host that is neither endpoint.
    compose = render_compose(two_router_topology)
    gateway_lines = [ln.strip() for ln in compose.splitlines() if ln.strip().startswith("gateway:")]
    assert gateway_lines, "compose must declare an explicit gateway per link"
    gateways = {ln.split("gateway:")[1].strip() for ln in gateway_lines}
    assert "172.30.0.1" not in gateways
    assert "172.30.0.2" not in gateways


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
