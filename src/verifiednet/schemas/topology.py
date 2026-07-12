"""TopologySpec — deterministic topology + addressing contract.

Provenance: address-math determinism and single-source-of-truth pattern from
closcall ``domain/fabric.py`` (commit d192bf3) — grammar REIMPLEMENTED FROM
SPECIFICATION (Gate 2 App. B: FabricSpec cannot express point-to-point links;
closcall license unresolved). Explicit ``links:`` and ``sessions:`` sections per
Gate 2.5 §9 (sessions are first-class because faults target sessions).
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.schemas.base import StrictModel


class NodeSpec(StrictModel):
    name: str = Field(min_length=1, max_length=63)
    asn: int = Field(ge=1, le=4294967295)
    loopback: str  # CIDR, e.g. "10.255.0.1/32"

    @model_validator(mode="after")
    def _validate_loopback(self) -> NodeSpec:
        iface = ipaddress.ip_interface(self.loopback)
        if iface.network.prefixlen != 32:
            raise ValueError(f"loopback must be /32: {self.loopback}")
        return self


class LinkEndpoint(StrictModel):
    node: str
    iface: str = Field(min_length=1, max_length=15)
    ip: str  # CIDR, e.g. "172.30.0.1/30"


class LinkSpec(StrictModel):
    a: LinkEndpoint
    b: LinkEndpoint

    @model_validator(mode="after")
    def _validate_p2p(self) -> LinkSpec:
        ip_a = ipaddress.ip_interface(self.a.ip)
        ip_b = ipaddress.ip_interface(self.b.ip)
        if ip_a.network != ip_b.network:
            raise ValueError("link endpoints must share one subnet")
        if ip_a.ip == ip_b.ip:
            raise ValueError("link endpoints must have distinct addresses")
        if self.a.node == self.b.node:
            raise ValueError("link endpoints must be on distinct nodes")
        if ip_a.network.prefixlen != 30:
            raise ValueError("Wave A links must be /30 (Gate 2.5 W10)")
        usable = set(ip_a.network.hosts())
        if {ip_a.ip, ip_b.ip} != usable:
            raise ValueError("/30 endpoints must use exactly the two usable host addresses")
        return self


class SessionEndpoint(StrictModel):
    node: str
    peer_ip: str  # plain address of the peer
    remote_as: int = Field(ge=1, le=4294967295)


class BgpSessionSpec(StrictModel):
    session_id: str = Field(min_length=1, max_length=64)
    session_type: Literal["ebgp"] = "ebgp"
    a: SessionEndpoint
    b: SessionEndpoint


class ImageSpec(StrictModel):
    frr: str  # e.g. "frrouting/frr:v8.4.1@sha256:<digest>" (digest pinned at Gate 4)


class TopologySpec(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=63)
    backend: str = Field(min_length=1)  # backend identifier, e.g. "frr-compose"
    nodes: tuple[NodeSpec, ...] = Field(min_length=1)
    links: tuple[LinkSpec, ...] = Field(min_length=1)
    sessions: tuple[BgpSessionSpec, ...] = Field(min_length=1)
    images: ImageSpec

    @model_validator(mode="after")
    def _validate_references(self) -> TopologySpec:
        names = [n.name for n in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError("node names must be unique")
        loopbacks = [n.loopback for n in self.nodes]
        if len(loopbacks) != len(set(loopbacks)):
            raise ValueError("loopbacks must be unique")
        known = set(names)
        link_ips: list[str] = []
        for link in self.links:
            for ep in (link.a, link.b):
                if ep.node not in known:
                    raise ValueError(f"link references unknown node: {ep.node}")
                link_ips.append(ep.ip)
        if len(link_ips) != len(set(link_ips)):
            raise ValueError("link endpoint addresses must be unique")
        sids = [s.session_id for s in self.sessions]
        if len(sids) != len(set(sids)):
            raise ValueError("session ids must be unique")
        by_name = {n.name: n for n in self.nodes}
        link_addrs = {ipaddress.ip_interface(ip).ip for ip in link_ips}
        for sess in self.sessions:
            for mine, theirs in ((sess.a, sess.b), (sess.b, sess.a)):
                if mine.node not in known:
                    raise ValueError(f"session references unknown node: {mine.node}")
                if mine.remote_as != by_name[theirs.node].asn:
                    raise ValueError(
                        f"session {sess.session_id}: {mine.node} remote_as "
                        f"{mine.remote_as} != {theirs.node} asn {by_name[theirs.node].asn}"
                    )
                if ipaddress.ip_address(mine.peer_ip) not in link_addrs:
                    raise ValueError(
                        f"session {sess.session_id}: peer_ip {mine.peer_ip} "
                        "is not a link endpoint address"
                    )
        return self

    def node(self, name: str) -> NodeSpec:
        for candidate in self.nodes:
            if candidate.name == name:
                return candidate
        raise KeyError(name)
