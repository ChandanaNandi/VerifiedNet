"""Pure vtysh argv builders for the FRR BGP fault family.

Provenance: vtysh grammar from sonic-troubleshooting-agent
``_apply_inject``/``_apply_restore`` (MIT, commit eb4c818) — copy with
modifications: re-targeted at plain FRR (no SONiC config_db indirection).
``clear bgp`` is retained as load-bearing: after reverting remote-as the
session may otherwise take minutes to renegotiate, so the forced reset is
part of the restoration contract (recorded in RestorationMetadata).

These builders are pure: no execution, no subprocess, no I/O.
"""

from __future__ import annotations


def set_remote_as_argv(local_asn: int, peer_ip: str, remote_as: int) -> tuple[str, ...]:
    """Argv that (re)configures a neighbor's remote-as under the local BGP router."""
    return (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        f"router bgp {local_asn}",
        "-c",
        f"neighbor {peer_ip} remote-as {remote_as}",
    )


def clear_bgp_argv(peer_ip: str) -> tuple[str, ...]:
    """Argv that hard-resets the BGP session with *peer_ip*."""
    return ("vtysh", "-c", f"clear bgp {peer_ip}")


def withdraw_network_argv(local_asn: int, prefix: str) -> tuple[str, ...]:
    """Argv that withdraws one advertised IPv4-unicast prefix (Gate 5.4).

    The BGP session stays Established throughout; only the advertisement of
    *prefix* is removed, so the peer withdraws that route while everything else
    (session, other routes, reachability) is unaffected.
    """
    return (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        f"router bgp {local_asn}",
        "-c",
        "address-family ipv4 unicast",
        "-c",
        f"no network {prefix}",
    )


def restore_network_argv(local_asn: int, prefix: str) -> tuple[str, ...]:
    """Argv that re-advertises the withdrawn prefix, exactly as rendered."""
    return (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        f"router bgp {local_asn}",
        "-c",
        "address-family ipv4 unicast",
        "-c",
        f"network {prefix}",
    )


def iface_shutdown_argv(iface: str) -> tuple[str, ...]:
    """Argv that administratively shuts down *iface* via FRR (Gate 5.3).

    Control point verified by the mandatory Gate 5.3 probe: FRR zebra drives
    the kernel link admin-down (the container holds NET_ADMIN), so both
    ``administrativeStatus`` and ``operationalStatus`` become ``down``.
    """
    return (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        f"interface {iface}",
        "-c",
        "shutdown",
    )


def iface_no_shutdown_argv(iface: str) -> tuple[str, ...]:
    """Argv that reverts the administrative shutdown of *iface* (Gate 5.3)."""
    return (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        f"interface {iface}",
        "-c",
        "no shutdown",
    )


def remove_neighbor_argv(local_asn: int, peer_ip: str) -> tuple[str, ...]:
    """Argv that removes the whole neighbor object under the local BGP router.

    FRR's ``no neighbor <ip>`` deletes the peer INCLUDING its address-family
    activation — restoration must re-issue both (Gate 5.2).
    """
    return (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        f"router bgp {local_asn}",
        "-c",
        f"no neighbor {peer_ip}",
    )


def restore_neighbor_argv(local_asn: int, peer_ip: str, remote_as: int) -> tuple[str, ...]:
    """Argv that recreates the neighbor exactly as the rendered baseline does.

    The lab renders ``no bgp default ipv4-unicast``, so the recreated neighbor
    exchanges NO IPv4 routes until ``neighbor <ip> activate`` is re-issued
    under ``address-family ipv4 unicast`` — the activate step is load-bearing,
    and recovery route checks loudly catch its omission.
    """
    return (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        f"router bgp {local_asn}",
        "-c",
        f"neighbor {peer_ip} remote-as {remote_as}",
        "-c",
        "address-family ipv4 unicast",
        "-c",
        f"neighbor {peer_ip} activate",
    )
