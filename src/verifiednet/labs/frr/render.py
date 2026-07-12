"""Pure deterministic FRR config + compose renderers (Gate 3 Step 5).

Provenance: FRR configuration idioms (daemons file shape, ``no bgp default
ipv4-unicast`` / ``no bgp ebgp-requires-policy`` eBGP session idiom) follow
neuronoc-network-ops-assistant infra/lab configs (MIT, commit 5f24447) as an
architectural reference; the rendering grammar itself is generated from
``TopologySpec`` — no NN config text is copied.

Rules (Gate 3):
- Renderers are PURE: same ``TopologySpec`` in, byte-identical text out.
  No file writes, no Docker invocation, no clocks, no randomness.
- Compose services get ``cap_add: [NET_ADMIN]`` ONLY. NN's ``SYS_ADMIN``
  grant was reviewed and REJECTED (provenance note): FRR needs NET_ADMIN for
  interface/route manipulation; SYS_ADMIN is an unnecessary privilege.
- No ``container_name`` in compose output — project-scoped naming is a
  Gate 4 concern.
- Config volumes/mounts are a Gate 4 concern; the compose text carries a
  comment noting that.

``write_rendered`` is a separate side-effecting helper for tests; the render
functions themselves never touch the filesystem.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

from verifiednet.schemas.topology import TopologySpec

_DAEMONS = (
    "vtysh_enable=yes\n"
    "bgpd=yes\n"
    "ospfd=no\n"
    "ospf6d=no\n"
    "ripd=no\n"
    "ripngd=no\n"
    "isisd=no\n"
    "pimd=no\n"
    "ldpd=no\n"
    "nhrpd=no\n"
    "eigrpd=no\n"
    "babeld=no\n"
    "sharpd=no\n"
    "pbrd=no\n"
    "bfdd=no\n"
    "fabricd=no\n"
    "vrrpd=no\n"
)


def render_daemons() -> str:
    """Render the FRR ``daemons`` file: bgpd only (zebra always runs)."""
    return _DAEMONS


def render_frr_conf(topo: TopologySpec, node_name: str) -> str:
    """Render the deterministic ``frr.conf`` for *node_name*.

    Ordering is fully determined by the topology: the loopback stanza first,
    then link interfaces in topology link order, then the BGP stanza with
    sessions in topology session order. Raises ``KeyError`` for an unknown
    node (via ``TopologySpec.node``).
    """
    node = topo.node(node_name)
    lines: list[str] = [
        "frr defaults traditional",
        f"hostname {node.name}",
        "!",
        "interface lo",
        f" ip address {node.loopback}",
        "!",
    ]
    for link in topo.links:
        for ep in (link.a, link.b):
            if ep.node == node_name:
                lines.extend([f"interface {ep.iface}", f" ip address {ep.ip}", "!"])
    lines.extend(
        [
            f"router bgp {node.asn}",
            " no bgp default ipv4-unicast",
            " no bgp ebgp-requires-policy",
        ]
    )
    my_sessions = [
        ep for sess in topo.sessions for ep in (sess.a, sess.b) if ep.node == node_name
    ]
    for sep in my_sessions:
        lines.append(f" neighbor {sep.peer_ip} remote-as {sep.remote_as}")
    lines.append(" address-family ipv4 unicast")
    for sep in my_sessions:
        lines.append(f"  neighbor {sep.peer_ip} activate")
    lines.append(f"  network {node.loopback}")
    lines.extend([" exit-address-family", "!", "line vty", "!"])
    return "\n".join(lines) + "\n"


def render_compose(topo: TopologySpec) -> str:
    """Render docker-compose YAML text by deterministic string assembly.

    One bridge network per link, named ``link0..linkN`` in topology link
    order, with static ``ipv4_address`` per endpoint. No ``container_name``
    (Gate 3 rule), ``cap_add`` is NET_ADMIN only (NN's SYS_ADMIN rejected — see
    module docstring). Byte-identical output for identical topologies.

    Docker bridge gateway (Gate 4 live-execution fix): a point-to-point link is
    addressed from a /30 in the topology, which has no spare host for Docker's
    mandatory bridge gateway — Docker would claim the first usable host (the
    same address as endpoint A) and container creation fails with "Address
    already in use". The *Docker* ipam subnet is therefore widened by one prefix
    bit and an explicit ``gateway`` is pinned to the highest host that is not an
    endpoint. The FRR interface addressing in ``frr.conf`` is unaffected and
    stays /30.
    """
    lines: list[str] = [
        "# Rendered by VerifiedNet (Gate 3). Node configs are mounted at Gate 4.",
        "services:",
    ]
    for node in topo.nodes:
        lines.extend(
            [
                f"  {node.name}:",
                f"    image: {topo.images.frr}",
                "    cap_add:",
                "      - NET_ADMIN",
                "    networks:",
            ]
        )
        for index, link in enumerate(topo.links):
            for ep in (link.a, link.b):
                if ep.node == node.name:
                    address = ipaddress.ip_interface(ep.ip).ip
                    lines.extend(
                        [f"      link{index}:", f"        ipv4_address: {address}"]
                    )
    lines.append("networks:")
    for index, link in enumerate(topo.links):
        link_net = ipaddress.ip_interface(link.a.ip).network
        docker_net = link_net.supernet(new_prefix=link_net.prefixlen - 1)
        endpoints = {
            ipaddress.ip_interface(link.a.ip).ip,
            ipaddress.ip_interface(link.b.ip).ip,
        }
        gateway = next(
            host for host in reversed(list(docker_net.hosts())) if host not in endpoints
        )
        lines.extend(
            [
                f"  link{index}:",
                "    driver: bridge",
                "    ipam:",
                "      config:",
                f"        - subnet: {docker_net}",
                f"          gateway: {gateway}",
            ]
        )
    return "\n".join(lines) + "\n"


def render_all(topo: TopologySpec) -> dict[str, str]:
    """Render every artifact for *topo*: daemons, per-node frr.conf, compose."""
    rendered: dict[str, str] = {"daemons": render_daemons()}
    for node in topo.nodes:
        rendered[f"{node.name}/frr.conf"] = render_frr_conf(topo, node.name)
    rendered["docker-compose.yml"] = render_compose(topo)
    return rendered


def write_rendered(rendered: dict[str, str], out_dir: Path) -> list[Path]:
    """Write *rendered* artifacts under *out_dir*; the ONLY side-effecting helper.

    Exists for tests (tmp_path) and Gate 4 assembly. Returns written paths in
    sorted-key order.
    """
    written: list[Path] = []
    for name in sorted(rendered):
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered[name], encoding="utf-8")
        written.append(path)
    return written
