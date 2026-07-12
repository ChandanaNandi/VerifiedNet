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
