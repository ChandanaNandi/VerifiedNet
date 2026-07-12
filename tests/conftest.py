"""Shared deterministic test fixtures. No wall clocks, no randomness, no services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.schemas import (
    BgpSessionSpec,
    ImageSpec,
    LinkEndpoint,
    LinkSpec,
    NodeSpec,
    ScenarioDefinition,
    ScenarioTimeouts,
    SessionEndpoint,
    TopologySpec,
)

EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class FakeClock:
    """Deterministic, manually-advanced clock."""

    def __init__(self, start: datetime = EPOCH) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self._now.timestamp()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def run_ctx(fake_clock: FakeClock) -> RunContext:
    return RunContext("run-test-0001", clock=fake_clock)


def make_two_router_topology() -> TopologySpec:
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
        images=ImageSpec(frr="frrouting/frr:v8.4.1"),
    )


def make_scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        family="bgp",
        template_id="bgp_remote_as_mismatch",
        version=1,
        parameters={"wrong_asn": 65999, "target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0,
            onset_s=30.0,
            recovery_s=60.0,
            command_s=10.0,
            poll_interval_s=0.5,
        ),
    )


@pytest.fixture
def two_router_topology() -> TopologySpec:
    return make_two_router_topology()


@pytest.fixture
def scenario() -> ScenarioDefinition:
    return make_scenario()


ClockFn = Callable[[], datetime]
