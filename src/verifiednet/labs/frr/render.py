"""Pure deterministic FRR config + compose renderers (Gate 3 Step 5).

Provenance: FRR configuration idioms (daemons file shape, ``no bgp default
ipv4-unicast`` / ``no bgp ebgp-requires-policy`` eBGP session idiom) follow
neuronoc-network-ops-assistant infra/lab configs (MIT, commit 5f24447) as an
architectural reference; the rendering grammar itself is generated from
``TopologySpec`` — no NN config text is copied.

Rules (Gate 3, amended Gate 4):
- Renderers are PURE: same ``TopologySpec`` in, byte-identical text out.
  No file writes, no Docker invocation, no clocks, no randomness.
- Compose services get ``cap_add: [NET_ADMIN, SYS_ADMIN]``. Gate 3 had
  rejected ``SYS_ADMIN`` as unnecessary; Gate 4 live execution DISPROVED that:
  FRR 8.4.1 ``zebra``/``bgpd`` request ``cap_sys_admin`` in their permitted set
  during ``privs_init`` and abort without it ("Failed to start zebra!").
  NN's grant was load-bearing. See ADR 0015 for the recorded live evidence.
- No ``container_name`` in compose output — container identity is resolved by
  project + service labels.
- Generated FRR configuration (``daemons``, per-node ``frr.conf``) is embedded
  as Compose ``configs`` with inline content and delivered read-only (0444) to
  the verified in-container paths ``/etc/frr/daemons`` and ``/etc/frr/frr.conf``
  via the Docker API — never host bind mounts, which Docker Desktop file-sharing
  policies can deny (verified live on macOS). See ADR 0015.
- The link interface name inside each container is pinned with the Compose
  ``interface_name`` attachment option to the topology's ``LinkEndpoint.iface``
  (approved topology: ``eth1``) — with a single attached network Docker would
  otherwise name it ``eth0``. See ADR 0015.

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


def _embed_block(text: str, indent: str) -> list[str]:
    """Embed *text* (no empty lines, first line unindented) as YAML block lines."""
    return [f"{indent}{line}" for line in text.splitlines()]


def render_compose(topo: TopologySpec) -> str:
    """Render docker-compose YAML text by deterministic string assembly.

    One bridge network per link, named ``link0..linkN`` in topology link
    order, with static ``ipv4_address`` per endpoint and the container-side
    interface pinned to the topology's ``iface`` name via ``interface_name``.
    No ``container_name``; ``hostname`` is pinned to the node name so live
    captures are deterministic (otherwise FRR reports the random container id).
    ``cap_add`` is NET_ADMIN + SYS_ADMIN (live requirement — module docstring,
    ADR 0015). Generated ``daemons`` and per-node ``frr.conf`` are embedded as
    inline Compose ``configs`` targeting the verified ``/etc/frr`` paths,
    read-only (0444). Byte-identical output for identical topologies.

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
        "# Rendered by VerifiedNet (Gate 4). Generated FRR configs are delivered",
        "# as read-only inline Compose configs (API-delivered; no host bind mounts).",
        "configs:",
        "  daemons:",
        "    content: |",
        *_embed_block(render_daemons(), "      "),
    ]
    for node in topo.nodes:
        lines.extend(
            [
                f"  frr_conf_{node.name}:",
                "    content: |",
                *_embed_block(render_frr_conf(topo, node.name), "      "),
            ]
        )
    lines.append("services:")
    for node in topo.nodes:
        lines.extend(
            [
                f"  {node.name}:",
                f"    image: {topo.images.frr}",
                f"    hostname: {node.name}",
                "    cap_add:",
                "      - NET_ADMIN",
                "      - SYS_ADMIN",
                "    networks:",
            ]
        )
        for index, link in enumerate(topo.links):
            for ep in (link.a, link.b):
                if ep.node == node.name:
                    address = ipaddress.ip_interface(ep.ip).ip
                    lines.extend(
                        [
                            f"      link{index}:",
                            f"        ipv4_address: {address}",
                            f"        interface_name: {ep.iface}",
                        ]
                    )
        lines.extend(
            [
                "    configs:",
                "      - source: daemons",
                "        target: /etc/frr/daemons",
                "        mode: 0444",
                f"      - source: frr_conf_{node.name}",
                "        target: /etc/frr/frr.conf",
                "        mode: 0444",
            ]
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
