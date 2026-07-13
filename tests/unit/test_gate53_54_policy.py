"""Exact-shape tests for the Gate 5.3 + 5.4 mutation policies (ADR-0005)."""

from __future__ import annotations

import pytest

from verifiednet.common.errors import PolicyViolationError
from verifiednet.faults.frr_commands import (
    iface_no_shutdown_argv,
    iface_shutdown_argv,
    restore_network_argv,
    withdraw_network_argv,
)
from verifiednet.runtime.policy import (
    MutationCommandPolicy,
    bgp_prefix_withdrawal_mutation_shapes,
    iface_admin_shutdown_mutation_shapes,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def iface_policy() -> MutationCommandPolicy:
    return MutationCommandPolicy(
        allowed_binaries=frozenset({"vtysh"}),
        allowed_shapes=iface_admin_shutdown_mutation_shapes(),
    )


@pytest.fixture
def prefix_policy() -> MutationCommandPolicy:
    return MutationCommandPolicy(
        allowed_binaries=frozenset({"vtysh"}),
        allowed_shapes=bgp_prefix_withdrawal_mutation_shapes(),
    )


# --- interface shutdown -------------------------------------------------------


def test_iface_shape_names() -> None:
    assert [s.name for s in iface_admin_shutdown_mutation_shapes()] == [
        "iface_shutdown",
        "iface_no_shutdown",
        "clear_bgp",
    ]


def test_allows_iface_shutdown_and_restore(iface_policy: MutationCommandPolicy) -> None:
    iface_policy.check(iface_shutdown_argv("eth1"))
    iface_policy.check(iface_no_shutdown_argv("eth1"))


def test_denies_loopback_shutdown(iface_policy: MutationCommandPolicy) -> None:
    # Only lab link interfaces (ethN); shutting down lo must be denied.
    with pytest.raises(PolicyViolationError):
        iface_policy.check(
            ("vtysh", "-c", "configure terminal", "-c", "interface lo", "-c", "shutdown")
        )


def test_denies_freeform_interface_name(iface_policy: MutationCommandPolicy) -> None:
    with pytest.raises(PolicyViolationError):
        iface_policy.check(
            ("vtysh", "-c", "configure terminal", "-c", "interface evil0", "-c", "shutdown")
        )


def test_denies_bare_shutdown(iface_policy: MutationCommandPolicy) -> None:
    with pytest.raises(PolicyViolationError):
        iface_policy.check(("vtysh", "-c", "shutdown"))


def test_denies_ip_link_binary(iface_policy: MutationCommandPolicy) -> None:
    # ip-link-mode is explicitly out of scope: a non-vtysh binary is denied.
    with pytest.raises(PolicyViolationError):
        iface_policy.check(("ip", "link", "set", "eth1", "down"))


# --- prefix withdrawal --------------------------------------------------------


def test_prefix_shape_names() -> None:
    assert [s.name for s in bgp_prefix_withdrawal_mutation_shapes()] == [
        "withdraw_network",
        "restore_network",
    ]


def test_allows_withdraw_and_restore_network(prefix_policy: MutationCommandPolicy) -> None:
    prefix_policy.check(withdraw_network_argv(65001, "10.255.0.1/32"))
    prefix_policy.check(restore_network_argv(65001, "10.255.0.1/32"))


def test_denies_network_without_prefix_length(prefix_policy: MutationCommandPolicy) -> None:
    with pytest.raises(PolicyViolationError):
        prefix_policy.check(
            (
                "vtysh",
                "-c",
                "configure terminal",
                "-c",
                "router bgp 65001",
                "-c",
                "address-family ipv4 unicast",
                "-c",
                "no network 10.255.0.1",
            )
        )


def test_denies_no_clear_bgp_shape_in_prefix_family(
    prefix_policy: MutationCommandPolicy,
) -> None:
    # This family never resets the session, so clear bgp is NOT an allowed shape.
    with pytest.raises(PolicyViolationError):
        prefix_policy.check(("vtysh", "-c", "clear bgp 172.30.0.2"))


def test_denies_neighbor_command_from_other_family(
    prefix_policy: MutationCommandPolicy,
) -> None:
    with pytest.raises(PolicyViolationError):
        prefix_policy.check(
            ("vtysh", "-c", "configure terminal", "-c", "router bgp 65001",
             "-c", "no neighbor 172.30.0.2")
        )
