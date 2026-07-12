"""Unit tests for the Wave A check factories: metric keys are the contract."""

from __future__ import annotations

import pytest

from verifiednet.schemas import Predicate
from verifiednet.verifiers import checks

pytestmark = pytest.mark.unit


def test_iface_operational() -> None:
    check = checks.iface_operational("router_a", "eth1", "precondition")
    assert check.metric == "iface.eth1.oper"
    assert check.predicate is Predicate.EQUALS
    assert check.expected == ("up",)
    assert check.subject == "router_a"
    assert check.phase == "precondition"
    assert check.check_id == "iface_operational:router_a:iface.eth1.oper:precondition"


def test_reachability_ok() -> None:
    check = checks.reachability_ok("router_a", "172.30.0.2", "onset")
    assert check.metric == "ping.172.30.0.2.all_success"
    assert check.predicate is Predicate.EQUALS
    assert check.expected == ("true",)
    assert check.check_id == "reachability_ok:router_a:ping.172.30.0.2.all_success:onset"


def test_bgp_established() -> None:
    check = checks.bgp_established("router_a", "172.30.0.2", "precondition")
    assert check.metric == "bgp.peer.172.30.0.2.state"
    assert check.predicate is Predicate.EQUALS
    assert check.expected == ("Established",)
    assert check.check_id == "bgp_established:router_a:bgp.peer.172.30.0.2.state:precondition"


def test_bgp_not_established() -> None:
    check = checks.bgp_not_established("router_a", "172.30.0.2", "onset")
    assert check.metric == "bgp.peer.172.30.0.2.state"
    assert check.predicate is Predicate.IN_SET
    assert check.expected == ("Idle", "Active", "Connect")
    assert check.check_id == "bgp_not_established:router_a:bgp.peer.172.30.0.2.state:onset"


def test_remote_as_equals() -> None:
    check = checks.remote_as_equals("router_a", "172.30.0.2", 65999, "onset")
    assert check.metric == "bgp.peer.172.30.0.2.remote_as"
    assert check.predicate is Predicate.EQUALS
    assert check.expected == ("65999",)
    assert check.check_id == "remote_as_equals:router_a:bgp.peer.172.30.0.2.remote_as:onset"


def test_remote_as_differs() -> None:
    check = checks.remote_as_differs("router_a", "172.30.0.2", 65002, "onset")
    assert check.metric == "bgp.peer.172.30.0.2.remote_as"
    assert check.predicate is Predicate.NOT_EQUALS
    assert check.expected == ("65002",)
    assert check.check_id == "remote_as_differs:router_a:bgp.peer.172.30.0.2.remote_as:onset"


def test_config_unchanged() -> None:
    sha = "a" * 64
    check = checks.config_unchanged("router_b", sha, "onset")
    assert check.metric == "config.sha256"
    assert check.predicate is Predicate.EQUALS
    assert check.expected == (sha,)
    assert check.check_id == "config_unchanged:router_b:config.sha256:onset"


def test_route_present() -> None:
    check = checks.route_present("router_a", "10.255.0.2/32", "recovery")
    assert check.metric == "route.10.255.0.2/32.present"
    assert check.predicate is Predicate.EQUALS
    assert check.expected == ("true",)
    assert check.check_id == "route_present:router_a:route.10.255.0.2/32.present:recovery"


def test_route_absent() -> None:
    check = checks.route_absent("router_a", "10.255.0.2/32", "onset")
    assert check.metric == "route.10.255.0.2/32.present"
    assert check.predicate is Predicate.EQUALS
    assert check.expected == ("false",)
    assert check.check_id == "route_absent:router_a:route.10.255.0.2/32.present:onset"


def test_all_factories_default_to_trusted_evidence() -> None:
    factory_checks = [
        checks.iface_operational("n", "eth1", "baseline"),
        checks.reachability_ok("n", "1.2.3.4", "baseline"),
        checks.bgp_established("n", "1.2.3.4", "baseline"),
        checks.bgp_not_established("n", "1.2.3.4", "baseline"),
        checks.remote_as_equals("n", "1.2.3.4", 65001, "baseline"),
        checks.remote_as_differs("n", "1.2.3.4", 65001, "baseline"),
        checks.config_unchanged("n", "x" * 64, "baseline"),
        checks.route_present("n", "10.0.0.0/24", "baseline"),
        checks.route_absent("n", "10.0.0.0/24", "baseline"),
    ]
    assert all(check.require_trusted for check in factory_checks)
    assert len({check.check_id for check in factory_checks}) == 9  # templates keep ids unique
