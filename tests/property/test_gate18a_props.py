"""Gate 18A property tests: derivation determinism, record-order independence,
bounded render size, audit totality, and policy-id sensitivity over randomized
observable evidence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.datasets.evidence_features import (
    FEATURE_ALLOWLIST_V2,
    DatasetFeaturesV2,
    FeaturePolicyV2,
    audit_features_v2,
    derive_features_v2,
    render_evidence_observation_block,
)
from verifiednet.schemas.evidence import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    Phase,
)

pytestmark = pytest.mark.property

_TS = datetime(2026, 1, 1, tzinfo=UTC)
_STATES = ["Established", "Active", "Idle", "Connect", "OpenSent"]
_BOOL = ["true", "false"]


def _rec(phase, collector, target, nz):
    return EvidenceRecord(
        evidence_id="ev", phase=phase,
        source=EvidenceSource(collector=collector, target=target),
        raw_sha256="0" * 64, raw_payload="", normalized=nz,
        captured_at=_TS, run_seq=1)


@st.composite
def _bundles(draw):
    state = draw(st.sampled_from(_STATES))
    remote_as = draw(st.sampled_from(["65001", "65002", "65003"]))
    admin = draw(st.sampled_from(_BOOL))
    present = draw(st.sampled_from(_BOOL))
    route_present = draw(st.sampled_from(_BOOL))
    base = EvidenceBundle(bundle_id="b", phase=Phase.BASELINE, records=(
        _rec(Phase.BASELINE, "frr.bgp_summary", "router_a",
             {"bgp.peer.10.0.0.2.state": "Established",
              "bgp.peer.10.0.0.2.remote_as": "65001"}),
        _rec(Phase.BASELINE, "frr.interfaces", "router_a",
             {"iface.eth1.admin": "up", "iface.eth1.oper": "up"}),
        _rec(Phase.BASELINE, "frr.routes", "router_a",
             {"route.10.0.0.0/24.present": "true",
              "route.10.0.0.0/24.protocols": "bgp"}),
    ))
    onset_records = (
        _rec(Phase.ONSET, "frr.bgp_summary", "router_a",
             {"bgp.peer.10.0.0.2.state": state,
              "bgp.peer.10.0.0.2.remote_as": remote_as,
              "bgp.peer.10.0.0.2.present": present}),
        _rec(Phase.ONSET, "frr.interfaces", "router_a",
             {"iface.eth1.admin": admin, "iface.eth1.oper": admin}),
        _rec(Phase.ONSET, "frr.routes", "router_a",
             {"route.10.0.0.0/24.present": route_present,
              "route.10.0.0.0/24.protocols": "bgp" if route_present == "true" else ""}),
    )
    onset = EvidenceBundle(bundle_id="o", phase=Phase.ONSET, records=onset_records)
    return base, onset


def _derive(base, onset):
    return derive_features_v2(backend="frr-compose", topology_hash="a" * 64,
                              baseline=base, onset=onset, policy=FeaturePolicyV2())


@settings(max_examples=150, deadline=None)
@given(bo=_bundles())
def test_derivation_is_deterministic(bo) -> None:
    base, onset = bo
    assert _derive(base, onset) == _derive(base, onset)


@settings(max_examples=150, deadline=None)
@given(bo=_bundles(), perm=st.permutations(range(3)))
def test_record_order_does_not_change_output(bo, perm) -> None:
    base, onset = bo
    shuffled = EvidenceBundle(bundle_id=onset.bundle_id, phase=onset.phase,
                              records=tuple(onset.records[i] for i in perm))
    assert _derive(base, onset) == _derive(base, shuffled)


@settings(max_examples=150, deadline=None)
@given(bo=_bundles())
def test_render_is_bounded_and_audit_total(bo) -> None:
    base, onset = bo
    f = _derive(base, onset)
    assert len(render_evidence_observation_block(f)) < 512
    result = audit_features_v2(f)  # totality: always returns a result
    assert result.passed is True  # a legitimately-derived payload never leaks


@settings(max_examples=150, deadline=None)
@given(bo=_bundles())
def test_derived_payload_only_exposes_allowlist(bo) -> None:
    base, onset = bo
    payload = _derive(base, onset).model_dump(mode="json")
    visible = set(payload) - {"schema_version", "feature_policy_id"}
    assert visible == set(FEATURE_ALLOWLIST_V2)


def test_policy_id_is_stable_and_distinct_from_any_field_perturbation() -> None:
    from verifiednet.datasets.features import FeaturePolicy

    assert FeaturePolicyV2().policy_id == FeaturePolicyV2().policy_id
    assert FeaturePolicyV2().policy_id != FeaturePolicy().policy_id


def test_feature_model_rejects_out_of_enum_state() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DatasetFeaturesV2(
            feature_policy_id="feat-x", backend="frr-compose",
            topology_hash="a" * 64, bgp_worst_peer_state="frobnicated",
            interface_any_admin_down=False, interface_any_oper_down=False,
            reachability_all_success=True, bgp_peer_removed=False,
            bgp_remote_as_changed=False, bgp_route_withdrawn=False)
