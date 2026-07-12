"""Unit tests for runtime command/target policies."""

from __future__ import annotations

import pytest

from verifiednet.common.errors import PolicyViolationError
from verifiednet.runtime import (
    CommandPolicy,
    MutationCommandPolicy,
    TargetPolicy,
    bgp_remote_as_mutation_shapes,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def read_policy() -> CommandPolicy:
    return CommandPolicy(allowed_binaries=frozenset({"vtysh"}), vtysh_show_only=True)


@pytest.fixture
def mutation_policy() -> MutationCommandPolicy:
    return MutationCommandPolicy(
        allowed_binaries=frozenset({"vtysh"}),
        allowed_shapes=bgp_remote_as_mutation_shapes(),
    )


class TestReadCommandPolicy:
    def test_allows_show_command(self, read_policy: CommandPolicy) -> None:
        read_policy.check(["vtysh", "-c", "show ip bgp summary json"])

    def test_allows_multiple_show_commands(self, read_policy: CommandPolicy) -> None:
        read_policy.check(
            ["vtysh", "-c", "show ip bgp summary json", "-c", "show bgp neighbors json"]
        )

    def test_denies_empty_argv(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError, match="empty argv"):
            read_policy.check([])

    def test_denies_configure(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError):
            read_policy.check(["vtysh", "-c", "configure terminal"])

    def test_denies_clear(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError):
            read_policy.check(["vtysh", "-c", "clear bgp 172.30.0.2"])

    def test_denies_forbidden_token_inside_show(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError, match="forbidden token"):
            read_policy.check(["vtysh", "-c", "show delete everything"])

    @pytest.mark.parametrize(
        "command",
        [
            "show ip bgp | include Established",
            "show ip bgp; reboot",
            "show ip bgp > /tmp/out",
            "show ip bgp `id`",
            "show ip bgp $HOME",
            "show ip bgp & sleep 1",
        ],
    )
    def test_denies_shell_metacharacters(self, read_policy: CommandPolicy, command: str) -> None:
        with pytest.raises(PolicyViolationError, match="metacharacter"):
            read_policy.check(["vtysh", "-c", command])

    def test_denies_unknown_binary(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError, match="binary not allowed"):
            read_policy.check(["rm", "-rf", "somefile"])

    def test_denies_vtysh_without_dash_c(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError):
            read_policy.check(["vtysh"])

    def test_denies_dangling_dash_c(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError, match="dangling"):
            read_policy.check(["vtysh", "-c"])

    def test_case_insensitive_forbidden_tokens(self, read_policy: CommandPolicy) -> None:
        with pytest.raises(PolicyViolationError):
            read_policy.check(["vtysh", "-c", "SHOW ip bgp CLEAR"])


class TestMutationCommandPolicy:
    def test_allows_remote_as_sequence(self, mutation_policy: MutationCommandPolicy) -> None:
        mutation_policy.check(
            [
                "vtysh",
                "-c",
                "configure terminal",
                "-c",
                "router bgp 65001",
                "-c",
                "neighbor 172.30.0.2 remote-as 65999",
            ]
        )

    def test_allows_clear_bgp(self, mutation_policy: MutationCommandPolicy) -> None:
        mutation_policy.check(["vtysh", "-c", "clear bgp 172.30.0.2"])

    def test_denies_show_not_in_shapes(self, mutation_policy: MutationCommandPolicy) -> None:
        with pytest.raises(PolicyViolationError, match="shape"):
            mutation_policy.check(["vtysh", "-c", "show ip bgp summary json"])

    def test_denies_partial_prefix_configure_only(
        self, mutation_policy: MutationCommandPolicy
    ) -> None:
        # A lone "configure terminal" must NOT match the 3-command set_remote_as shape.
        with pytest.raises(PolicyViolationError, match="shape"):
            mutation_policy.check(["vtysh", "-c", "configure terminal"])

    def test_denies_missing_leading_command(
        self, mutation_policy: MutationCommandPolicy
    ) -> None:
        # Correct tail but missing "configure terminal": wrong count/order.
        with pytest.raises(PolicyViolationError, match="shape"):
            mutation_policy.check(
                [
                    "vtysh",
                    "-c",
                    "router bgp 65001",
                    "-c",
                    "neighbor 172.30.0.2 remote-as 65999",
                ]
            )

    def test_denies_router_bgp_without_asn(
        self, mutation_policy: MutationCommandPolicy
    ) -> None:
        # Parameter position must be filled: "router bgp" without an ASN is denied.
        with pytest.raises(PolicyViolationError, match="shape"):
            mutation_policy.check(
                [
                    "vtysh",
                    "-c",
                    "configure terminal",
                    "-c",
                    "router bgp",
                    "-c",
                    "neighbor 172.30.0.2 remote-as 65999",
                ]
            )

    def test_denies_reordered_sequence(self, mutation_policy: MutationCommandPolicy) -> None:
        with pytest.raises(PolicyViolationError, match="shape"):
            mutation_policy.check(
                [
                    "vtysh",
                    "-c",
                    "router bgp 65001",
                    "-c",
                    "configure terminal",
                    "-c",
                    "neighbor 172.30.0.2 remote-as 65999",
                ]
            )

    @pytest.mark.parametrize("argv", [["rm", "-rf", "/"], ["reboot"]])
    def test_denies_unlisted_binaries(
        self, mutation_policy: MutationCommandPolicy, argv: list[str]
    ) -> None:
        with pytest.raises(PolicyViolationError, match="binary not allowed"):
            mutation_policy.check(argv)

    def test_denies_shell_metacharacters(self, mutation_policy: MutationCommandPolicy) -> None:
        with pytest.raises(PolicyViolationError, match="metacharacter"):
            mutation_policy.check(["vtysh", "-c", "clear bgp 172.30.0.2; rm -rf /"])

    def test_denies_sequence_longer_than_shape(
        self, mutation_policy: MutationCommandPolicy
    ) -> None:
        with pytest.raises(PolicyViolationError, match="shape"):
            mutation_policy.check(
                [
                    "vtysh",
                    "-c",
                    "configure terminal",
                    "-c",
                    "router bgp 65001",
                    "-c",
                    "neighbor 172.30.0.2 remote-as 65999",
                    "-c",
                    "neighbor 172.30.0.2 shutdown",
                ]
            )

    def test_denies_vtysh_without_dash_c(self, mutation_policy: MutationCommandPolicy) -> None:
        with pytest.raises(PolicyViolationError):
            mutation_policy.check(["vtysh"])


class TestTargetPolicy:
    def test_allows_listed_target(self) -> None:
        TargetPolicy(allowed_targets=frozenset({"router_a", "router_b"})).check("router_a")

    def test_denies_unlisted_target(self) -> None:
        policy = TargetPolicy(allowed_targets=frozenset({"router_a", "router_b"}))
        with pytest.raises(PolicyViolationError, match="target not allowed"):
            policy.check("router_c")
