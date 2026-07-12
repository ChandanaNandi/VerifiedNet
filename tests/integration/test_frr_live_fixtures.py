"""Integration checks on the COMMITTED live fixture capture set.

Validates that the live capture set recorded from the healthy lab is intact
(manifest hashes match, provenance statements present, pinned image bound) and
that the existing collector parsers accept the REAL captured FRR output —
proving live-format compatibility without hand-edited fixtures. These checks
read committed files; a missing set is a hard failure (the set is a committed
artifact), while Docker availability is still required by the directory gate
for consistency of the integration tier.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifiednet.collectors.frr.bgp import parse_bgp_summary
from verifiednet.labs.frr.fixture_capture import verify_fixture_manifest
from verifiednet.labs.frr.topologies import (
    PINNED_FRR_IMAGE,
    PINNED_FRR_IMAGE_ARM64_DIGEST,
)

pytestmark = pytest.mark.integration

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "frr"
    / "live"
    / "frr-8.4.1-linux-arm64"
)


def test_live_fixture_set_is_intact() -> None:
    assert FIXTURE_DIR.is_dir(), f"committed live fixture set missing: {FIXTURE_DIR}"
    assert verify_fixture_manifest(FIXTURE_DIR) == []


def test_manifest_binds_the_approved_image_and_platform() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["image_reference"] == PINNED_FRR_IMAGE
    assert manifest["manifest_list_digest"] == PINNED_FRR_IMAGE.split("@", 1)[1]
    assert manifest["platform_digest"] == PINNED_FRR_IMAGE_ARM64_DIGEST
    assert manifest["frr_version"] == "8.4.1_git"
    assert manifest["statements"]["no_mutation_command_executed"] is True
    assert manifest["statements"]["produced_from_live_two_router_healthy_lab"] is True
    assert manifest["source_commit"]
    assert manifest["topology_sha256"]
    env = manifest["environment"]
    for key in ("host_arch", "host_os", "container_runtime_version", "compose_version"):
        assert key in env, f"environment missing {key}"
    for entry in manifest["files"].values():
        assert entry["logical_argv"][0] in {"vtysh"}
        assert entry["transport_argv"][:2] == ["docker", "compose"]


def test_captured_bgp_summaries_parse_with_the_existing_parser() -> None:
    for node, peer, local_as, remote_as in (
        ("router_a", "172.30.0.2", "65001", "65002"),
        ("router_b", "172.30.0.1", "65002", "65001"),
    ):
        raw = (FIXTURE_DIR / f"{node}_bgp_summary_established.json").read_text(
            encoding="utf-8"
        )
        normalized = parse_bgp_summary(raw)
        assert normalized["bgp.local_as"] == local_as
        assert normalized[f"bgp.peer.{peer}.state"] == "Established"
        assert normalized[f"bgp.peer.{peer}.remote_as"] == remote_as


def test_captured_interfaces_show_eth1_up() -> None:
    for node in ("router_a", "router_b"):
        data = json.loads(
            (FIXTURE_DIR / f"{node}_interfaces.json").read_text(encoding="utf-8")
        )
        eth1 = data["eth1"]
        admin = eth1.get("administrativeStatus", eth1.get("adminStatus"))
        oper = eth1.get("operationalStatus", eth1.get("operStatus"))
        assert str(admin).lower() == "up"
        assert str(oper).lower() == "up"


def test_captured_routes_contain_both_loopbacks() -> None:
    for node, learned in (("router_a", "10.255.0.2/32"), ("router_b", "10.255.0.1/32")):
        data = json.loads(
            (FIXTURE_DIR / f"{node}_routes.json").read_text(encoding="utf-8")
        )
        assert learned in data, f"{node} missing learned loopback {learned}"
        protocols = {entry["protocol"] for entry in data[learned]}
        assert "bgp" in protocols


def test_captured_running_configs_are_real(tmp_path: Path) -> None:
    for node, asn in (("router_a", "65001"), ("router_b", "65002")):
        text = (FIXTURE_DIR / f"{node}_running_config.txt").read_text(encoding="utf-8")
        assert f"router bgp {asn}" in text
        assert f"hostname {node}" in text
