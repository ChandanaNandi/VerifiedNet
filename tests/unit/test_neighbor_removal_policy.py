"""Exact-shape tests for the Gate 5.2 neighbor-removal mutation policy.

ADR-0005 discipline: identical command count, identical ordering, fullmatch per
position; parameters only in named positions. Partial prefixes, truncated or
extended sequences, and cross-family commands are denied.
"""

from __future__ import annotations

import pytest

from verifiednet.common.errors import PolicyViolationError
from verifiednet.faults.frr_commands import (
    clear_bgp_argv,
    remove_neighbor_argv,
    restore_neighbor_argv,
)
from verifiednet.runtime.policy import (
    MutationCommandPolicy,
    bgp_neighbor_removal_mutation_shapes,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def policy() -> MutationCommandPolicy:
    return MutationCommandPolicy(
        allowed_binaries=frozenset({"vtysh"}),
        allowed_shapes=bgp_neighbor_removal_mutation_shapes(),
    )


def test_shape_names_are_exactly_the_three_approved() -> None:
    names = [shape.name for shape in bgp_neighbor_removal_mutation_shapes()]
    assert names == ["remove_neighbor", "restore_neighbor", "clear_bgp"]


def test_allows_remove_neighbor(policy: MutationCommandPolicy) -> None:
    policy.check(remove_neighbor_argv(65001, "172.30.0.2"))


def test_allows_restore_neighbor_full_sequence(policy: MutationCommandPolicy) -> None:
    policy.check(restore_neighbor_argv(65001, "172.30.0.2", 65002))


def test_allows_clear_bgp(policy: MutationCommandPolicy) -> None:
    policy.check(clear_bgp_argv("172.30.0.2"))


def test_denies_remote_as_set_from_other_family(policy: MutationCommandPolicy) -> None:
    # The remote-AS family's 3-command SET sequence is NOT a neighbor-removal
    # shape (its third command differs) — families do not share mutations.
    from verifiednet.faults.frr_commands import set_remote_as_argv

    with pytest.raises(PolicyViolationError):
        policy.check(set_remote_as_argv(65001, "172.30.0.2", 65999))


def test_denies_bare_no_neighbor_without_context(policy: MutationCommandPolicy) -> None:
    with pytest.raises(PolicyViolationError):
        policy.check(("vtysh", "-c", "no neighbor 172.30.0.2"))


def test_denies_restore_without_activate(policy: MutationCommandPolicy) -> None:
    # A truncated restore (missing the activate step) matches NO shape: the
    # load-bearing activation cannot be silently skipped at the policy level.
    truncated = restore_neighbor_argv(65001, "172.30.0.2", 65002)[:-4]
    with pytest.raises(PolicyViolationError):
        policy.check(truncated)


def test_denies_remove_with_extra_command(policy: MutationCommandPolicy) -> None:
    extended = (*remove_neighbor_argv(65001, "172.30.0.2"), "-c", "clear bgp 172.30.0.2")
    with pytest.raises(PolicyViolationError):
        policy.check(extended)


def test_denies_reordered_restore(policy: MutationCommandPolicy) -> None:
    good = remove_neighbor_argv(65001, "172.30.0.2")
    reordered = (good[0], good[1], good[6], good[3], good[4], good[5], good[2])
    with pytest.raises(PolicyViolationError):
        policy.check(reordered)


def test_denies_hostname_instead_of_ip(policy: MutationCommandPolicy) -> None:
    with pytest.raises(PolicyViolationError):
        policy.check(
            (
                "vtysh",
                "-c",
                "configure terminal",
                "-c",
                "router bgp 65001",
                "-c",
                "no neighbor evil-host",
            )
        )


def test_denies_unlisted_binary(policy: MutationCommandPolicy) -> None:
    with pytest.raises(PolicyViolationError):
        policy.check(("ip", "link", "set", "eth1", "down"))
