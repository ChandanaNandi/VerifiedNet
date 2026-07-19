"""Gate 18A unit tests: the v2 feature policy, evidence derivation per fault
family, deterministic bounded output, the v2 leakage audit, the shared v2
observation render, and the v1/v2 distinction."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from verifiednet.datasets.evidence_features import (
    FEATURE_ALLOWLIST_V2,
    V2_FIELD_CATEGORY,
    DatasetFeaturesV2,
    EvidenceFeatureCategory,
    FeaturePolicyV2,
    audit_features_v2,
    derive_features_v2,
    render_evidence_observation_block,
)
from verifiednet.datasets.features import FeaturePolicy
from verifiednet.schemas.evidence import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    Phase,
)

pytestmark = pytest.mark.unit

V2_POLICY_ID = "feat-228b357dd9f256fa"
_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _rec(phase: Phase, collector: str, target: str, nz: dict[str, str]) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev-x", phase=phase,
        source=EvidenceSource(collector=collector, target=target),
        raw_sha256="0" * 64, raw_payload="", normalized=nz,
        captured_at=_TS, run_seq=1)


def _bundle(phase: Phase, records: tuple[EvidenceRecord, ...]) -> EvidenceBundle:
    return EvidenceBundle(bundle_id="b", phase=phase, records=records)


def _healthy_baseline() -> EvidenceBundle:
    return _bundle(Phase.BASELINE, (
        _rec(Phase.BASELINE, "frr.bgp_summary", "router_a",
             {"bgp.local_as": "65101", "bgp.peer.10.0.0.2.remote_as": "65102",
              "bgp.peer.10.0.0.2.state": "Established"}),
        _rec(Phase.BASELINE, "frr.interfaces", "router_a",
             {"iface.eth1.admin": "up", "iface.eth1.oper": "up"}),
        _rec(Phase.BASELINE, "frr.routes", "router_a",
             {"route.10.255.1.2/32.present": "true",
              "route.10.255.1.2/32.protocols": "bgp"}),
        _rec(Phase.BASELINE, "frr.reachability", "router_a",
             {"ping.10.0.0.2.all_success": "true"}),
    ))


def _derive(onset: EvidenceBundle | None) -> DatasetFeaturesV2:
    return derive_features_v2(
        backend="frr-compose", topology_hash="a" * 64,
        baseline=_healthy_baseline(), onset=onset, policy=FeaturePolicyV2())


def test_v2_policy_id_is_pinned_and_distinct_from_v1() -> None:
    assert FeaturePolicyV2().policy_id == V2_POLICY_ID
    assert FeaturePolicyV2().policy_id != FeaturePolicy().policy_id


def test_allowlist_is_sorted_and_categorized() -> None:
    assert tuple(sorted(FEATURE_ALLOWLIST_V2)) == FEATURE_ALLOWLIST_V2
    for field in FEATURE_ALLOWLIST_V2:
        assert field in V2_FIELD_CATEGORY
    # no field is a diagnostic conclusion: every field is context/raw/delta
    assert set(V2_FIELD_CATEGORY.values()) <= {
        EvidenceFeatureCategory.CONTEXT, EvidenceFeatureCategory.RAW_STATE,
        EvidenceFeatureCategory.STATE_DELTA}


def test_derive_iface_admin_shutdown() -> None:
    onset = _bundle(Phase.ONSET, (
        _rec(Phase.ONSET, "frr.interfaces", "router_a",
             {"iface.eth1.admin": "down", "iface.eth1.oper": "down"}),
        _rec(Phase.ONSET, "frr.reachability", "router_a",
             {"ping.10.0.0.2.all_success": "false"}),
    ))
    f = _derive(onset)
    assert f.interface_any_admin_down is True
    assert f.interface_any_oper_down is True
    assert f.reachability_all_success is False
    assert f.bgp_peer_removed is False
    assert f.bgp_remote_as_changed is False


def test_derive_neighbor_removal() -> None:
    onset = _bundle(Phase.ONSET, (
        _rec(Phase.ONSET, "frr.bgp_summary", "router_a",
             {"bgp.peer.10.0.0.2.present": "false"}),
        _rec(Phase.ONSET, "frr.routes", "router_a",
             {"route.10.255.1.2/32.present": "false",
              "route.10.255.1.2/32.protocols": ""}),
    ))
    f = _derive(onset)
    assert f.bgp_peer_removed is True
    assert f.bgp_worst_peer_state == "no_peer"  # removed peer excluded
    assert f.bgp_route_withdrawn is True
    assert f.interface_any_admin_down is False


def test_derive_remote_as_mismatch() -> None:
    onset = _bundle(Phase.ONSET, (
        _rec(Phase.ONSET, "frr.bgp_summary", "router_a",
             {"bgp.peer.10.0.0.2.remote_as": "65650",
              "bgp.peer.10.0.0.2.state": "Active"}),
    ))
    f = _derive(onset)
    assert f.bgp_remote_as_changed is True
    assert f.bgp_worst_peer_state == "active"
    assert f.bgp_peer_removed is False
    assert f.interface_any_admin_down is False


def test_derive_prefix_withdrawal_session_stays_up() -> None:
    onset = _bundle(Phase.ONSET, (
        _rec(Phase.ONSET, "frr.bgp_summary", "router_a",
             {"bgp.peer.10.0.0.2.remote_as": "65102",
              "bgp.peer.10.0.0.2.state": "Established"}),
        _rec(Phase.ONSET, "frr.routes", "router_a",
             {"route.10.255.1.2/32.present": "false",
              "route.10.255.1.2/32.protocols": ""}),
    ))
    f = _derive(onset)
    assert f.bgp_route_withdrawn is True
    assert f.bgp_worst_peer_state == "established"  # session healthy
    assert f.bgp_peer_removed is False
    assert f.bgp_remote_as_changed is False
    assert f.interface_any_admin_down is False


def test_abstention_no_onset_is_healthy_and_deltas_false() -> None:
    f = _derive(None)
    assert f.bgp_worst_peer_state == "established"
    assert f.interface_any_admin_down is False
    assert f.reachability_all_success is True
    assert f.bgp_peer_removed is False
    assert f.bgp_remote_as_changed is False
    assert f.bgp_route_withdrawn is False


def test_four_families_produce_distinct_feature_vectors() -> None:
    vectors = set()
    onsets = {
        "iface": (_rec(Phase.ONSET, "frr.interfaces", "router_a",
                       {"iface.eth1.admin": "down", "iface.eth1.oper": "down"}),),
        "nr": (_rec(Phase.ONSET, "frr.bgp_summary", "router_a",
                    {"bgp.peer.10.0.0.2.present": "false"}),),
        "ras": (_rec(Phase.ONSET, "frr.bgp_summary", "router_a",
                     {"bgp.peer.10.0.0.2.remote_as": "65650",
                      "bgp.peer.10.0.0.2.state": "Active"}),),
        "pf": (_rec(Phase.ONSET, "frr.bgp_summary", "router_a",
                    {"bgp.peer.10.0.0.2.remote_as": "65102",
                     "bgp.peer.10.0.0.2.state": "Established"}),
               _rec(Phase.ONSET, "frr.routes", "router_a",
                    {"route.10.255.1.2/32.present": "false",
                     "route.10.255.1.2/32.protocols": ""})),
    }
    for recs in onsets.values():
        f = _derive(_bundle(Phase.ONSET, recs))
        vectors.add(tuple(sorted(
            f.model_dump(exclude={"feature_policy_id", "schema_version"}).items())))
    assert len(vectors) == 4  # all four families distinguishable


def test_v2_audit_passes_for_derived_features() -> None:
    onset = _bundle(Phase.ONSET, (
        _rec(Phase.ONSET, "frr.bgp_summary", "router_a",
             {"bgp.peer.10.0.0.2.present": "false"}),))
    assert audit_features_v2(_derive(onset)).passed is True


def test_render_is_deterministic_and_bounded() -> None:
    f = _derive(_bundle(Phase.ONSET, (
        _rec(Phase.ONSET, "frr.interfaces", "router_a",
             {"iface.eth1.admin": "down"}),)))
    a = render_evidence_observation_block(f)
    b = render_evidence_observation_block(f)
    assert a == b
    assert a.startswith("Observation metadata:\n")
    assert not a.endswith("\n")  # boundary-aligned; wrapper adds the separator
    assert len(a) < 512  # bounded
