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


def two_router_frr_topology_v2(
    image_ref: str = "frrouting/frr:v8.4.1",
) -> TopologySpec:
    """Gate 14 expansion topology variant 2: same approved two-router shape,
    DIFFERENT network context (AS 65101<->65102, 172.31.0.0/30 link,
    10.255.1.x loopbacks). A distinct ``topology_hash`` yields distinct stable
    scenario identities — meaningful context variation, not a text duplicate."""
    return TopologySpec(
        name="verifiednet-frr-2r-v2",
        backend="frr-compose",
        nodes=(
            NodeSpec(name="router_a", asn=65101, loopback="10.255.1.1/32"),
            NodeSpec(name="router_b", asn=65102, loopback="10.255.1.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node="router_a", iface="eth1", ip="172.31.0.1/30"),
                b=LinkEndpoint(node="router_b", iface="eth1", ip="172.31.0.2/30"),
            ),
        ),
        sessions=(
            BgpSessionSpec(
                session_id="a-b",
                a=SessionEndpoint(node="router_a", peer_ip="172.31.0.2", remote_as=65102),
                b=SessionEndpoint(node="router_b", peer_ip="172.31.0.1", remote_as=65101),
            ),
        ),
        images=ImageSpec(frr=image_ref),
    )


def two_router_frr_topology_v3(
    image_ref: str = "frrouting/frr:v8.4.1",
) -> TopologySpec:
    """Gate 14 expansion topology variant 3 (AS 64601<->64602, 172.29.0.0/30,
    10.255.2.x loopbacks). See ``two_router_frr_topology_v2``."""
    return TopologySpec(
        name="verifiednet-frr-2r-v3",
        backend="frr-compose",
        nodes=(
            NodeSpec(name="router_a", asn=64601, loopback="10.255.2.1/32"),
            NodeSpec(name="router_b", asn=64602, loopback="10.255.2.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node="router_a", iface="eth1", ip="172.29.0.1/30"),
                b=LinkEndpoint(node="router_b", iface="eth1", ip="172.29.0.2/30"),
            ),
        ),
        sessions=(
            BgpSessionSpec(
                session_id="a-b",
                a=SessionEndpoint(node="router_a", peer_ip="172.29.0.2", remote_as=64602),
                b=SessionEndpoint(node="router_b", peer_ip="172.29.0.1", remote_as=64601),
            ),
        ),
        images=ImageSpec(frr=image_ref),
    )


def two_router_frr_topology_v4(
    image_ref: str = "frrouting/frr:v8.4.1",
) -> TopologySpec:
    """Gate 14B expansion topology variant 4 (AS 65201<->65202, 172.28.0.0/30,
    10.255.3.1/32 / 10.255.3.2/32 loopbacks). Same approved two-router shape, distinct
    network semantics -> distinct ``topology_hash``."""
    return TopologySpec(
        name="verifiednet-frr-2r-v4",
        backend="frr-compose",
        nodes=(
            NodeSpec(name="router_a", asn=65201, loopback="10.255.3.1/32"),
            NodeSpec(name="router_b", asn=65202, loopback="10.255.3.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node="router_a", iface="eth1", ip="172.28.0.1/30"),
                b=LinkEndpoint(node="router_b", iface="eth1", ip="172.28.0.2/30"),
            ),
        ),
        sessions=(
            BgpSessionSpec(
                session_id="a-b",
                a=SessionEndpoint(node="router_a", peer_ip="172.28.0.2", remote_as=65202),
                b=SessionEndpoint(node="router_b", peer_ip="172.28.0.1", remote_as=65201),
            ),
        ),
        images=ImageSpec(frr=image_ref),
    )


def two_router_frr_topology_v5(
    image_ref: str = "frrouting/frr:v8.4.1",
) -> TopologySpec:
    """Gate 14B expansion topology variant 5 (AS 65301<->65302, 172.27.0.0/30,
    10.255.4.1/32 / 10.255.4.2/32 loopbacks). Same approved two-router shape, distinct
    network semantics -> distinct ``topology_hash``."""
    return TopologySpec(
        name="verifiednet-frr-2r-v5",
        backend="frr-compose",
        nodes=(
            NodeSpec(name="router_a", asn=65301, loopback="10.255.4.1/32"),
            NodeSpec(name="router_b", asn=65302, loopback="10.255.4.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node="router_a", iface="eth1", ip="172.27.0.1/30"),
                b=LinkEndpoint(node="router_b", iface="eth1", ip="172.27.0.2/30"),
            ),
        ),
        sessions=(
            BgpSessionSpec(
                session_id="a-b",
                a=SessionEndpoint(node="router_a", peer_ip="172.27.0.2", remote_as=65302),
                b=SessionEndpoint(node="router_b", peer_ip="172.27.0.1", remote_as=65301),
            ),
        ),
        images=ImageSpec(frr=image_ref),
    )


def two_router_frr_topology_v6(
    image_ref: str = "frrouting/frr:v8.4.1",
) -> TopologySpec:
    """Gate 14B expansion topology variant 6 (AS 65401<->65402, 172.26.0.0/30,
    10.255.5.1/32 / 10.255.5.2/32 loopbacks). Same approved two-router shape, distinct
    network semantics -> distinct ``topology_hash``."""
    return TopologySpec(
        name="verifiednet-frr-2r-v6",
        backend="frr-compose",
        nodes=(
            NodeSpec(name="router_a", asn=65401, loopback="10.255.5.1/32"),
            NodeSpec(name="router_b", asn=65402, loopback="10.255.5.2/32"),
        ),
        links=(
            LinkSpec(
                a=LinkEndpoint(node="router_a", iface="eth1", ip="172.26.0.1/30"),
                b=LinkEndpoint(node="router_b", iface="eth1", ip="172.26.0.2/30"),
            ),
        ),
        sessions=(
            BgpSessionSpec(
                session_id="a-b",
                a=SessionEndpoint(node="router_a", peer_ip="172.26.0.2", remote_as=65402),
                b=SessionEndpoint(node="router_b", peer_ip="172.26.0.1", remote_as=65401),
            ),
        ),
        images=ImageSpec(frr=image_ref),
    )
