"""Unit tests for the pure vtysh argv builders."""

from __future__ import annotations

import pytest

from verifiednet.faults.frr_commands import clear_bgp_argv, set_remote_as_argv

pytestmark = pytest.mark.unit


def test_set_remote_as_argv_exact() -> None:
    assert set_remote_as_argv(65001, "172.30.0.2", 65999) == (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        "router bgp 65001",
        "-c",
        "neighbor 172.30.0.2 remote-as 65999",
    )


def test_set_remote_as_argv_revert_exact() -> None:
    assert set_remote_as_argv(65001, "172.30.0.2", 65002) == (
        "vtysh",
        "-c",
        "configure terminal",
        "-c",
        "router bgp 65001",
        "-c",
        "neighbor 172.30.0.2 remote-as 65002",
    )


def test_clear_bgp_argv_exact() -> None:
    assert clear_bgp_argv("172.30.0.2") == ("vtysh", "-c", "clear bgp 172.30.0.2")


def test_builders_return_tuples() -> None:
    assert isinstance(set_remote_as_argv(1, "10.0.0.1", 2), tuple)
    assert isinstance(clear_bgp_argv("10.0.0.1"), tuple)
