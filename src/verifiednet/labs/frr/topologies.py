"""Canonical FRR lab topologies (ADR 0006).

The approved two-router routed-eBGP topology lives here as configuration data
built through ``TopologySpec`` — a single factory shared by tests, the live
fixture capture script, and integration runs, so the approved values (names,
ASNs, addresses) are never duplicated as ad hoc constants.

``PINNED_FRR_IMAGE`` is the approved immutable image reference (manifest-list
digest); ``PINNED_FRR_IMAGE_ARM64_DIGEST`` is the platform-specific digest
resolved for ``linux/arm64`` hosts, recorded for fixture provenance.
"""

from __future__ import annotations

from verifiednet.schemas.topology import (
    BgpSessionSpec,
    ImageSpec,
    LinkEndpoint,
    LinkSpec,
    NodeSpec,
    SessionEndpoint,
    TopologySpec,
)

#: Approved immutable FRR image (manifest-list digest), pinned at Gate 4.
PINNED_FRR_IMAGE = (
    "frrouting/frr:v8.4.1@sha256:"
    "0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e"
)

#: Platform-specific digest of the same image for linux/arm64 (provenance).
PINNED_FRR_IMAGE_ARM64_DIGEST = (
    "sha256:9602a0697e261e29b82fdf4819cd8850355851b71b80dafadd4aa4ce983355eb"
)


def two_router_frr_topology(image_ref: str = "frrouting/frr:v8.4.1") -> TopologySpec:
    """The approved two-router eBGP lab (ADR 0006): AS65001 <-> AS65002."""
    return TopologySpec(
        name="verifiednet-frr-2r",
        backend="frr-compose",
        nodes=(
            NodeSpec(name="router_a", asn=65001, loopback="10.255.0.1/32"),
            NodeSpec(name="router_b", asn=65002, loopback="10.255.0.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node="router_a", iface="eth1", ip="172.30.0.1/30"),
                b=LinkEndpoint(node="router_b", iface="eth1", ip="172.30.0.2/30"),
            ),
        ),
        sessions=(
            BgpSessionSpec(
                session_id="a-b",
                a=SessionEndpoint(node="router_a", peer_ip="172.30.0.2", remote_as=65002),
                b=SessionEndpoint(node="router_b", peer_ip="172.30.0.1", remote_as=65001),
            ),
        ),
        images=ImageSpec(frr=image_ref),
    )
