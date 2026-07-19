"""Gate 18A failure tests: missing/malformed evidence, wrong phase, a direct
label copy, an oracle-output field, a nested forbidden key, a full-path value,
an out-of-allowlist field, and a model_construct bypass all fail closed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verifiednet.datasets.evidence_features import (
    DatasetFeaturesV2,
    EvidenceFeatureError,
    FeaturePolicyV2,
    audit_features_v2,
    derive_features_v2,
)
from verifiednet.schemas.evidence import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    Phase,
)

pytestmark = pytest.mark.failure

_TS = datetime(2026, 1, 1, tzinfo=UTC)
V2_FEAT_ID = "feat-228b357dd9f256fa"


def _rec(phase, collector, target, nz):
    return EvidenceRecord(
        evidence_id="ev", phase=phase,
        source=EvidenceSource(collector=collector, target=target),
        raw_sha256="0" * 64, raw_payload="", normalized=nz,
        captured_at=_TS, run_seq=1)


def _baseline() -> EvidenceBundle:
    return EvidenceBundle(bundle_id="b", phase=Phase.BASELINE, records=(
        _rec(Phase.BASELINE, "frr.interfaces", "router_a",
             {"iface.eth1.admin": "up", "iface.eth1.oper": "up"}),))


def _valid_features(**overrides) -> dict:
    base = dict(
        feature_policy_id=V2_FEAT_ID, backend="frr-compose",
        topology_hash="a" * 64, bgp_worst_peer_state="active",
        interface_any_admin_down=False, interface_any_oper_down=False,
        reachability_all_success=True, bgp_peer_removed=False,
        bgp_remote_as_changed=False, bgp_route_withdrawn=False)
    base.update(overrides)
    return base


def test_wrong_baseline_phase_fails_closed() -> None:
    onset_as_baseline = EvidenceBundle(bundle_id="b", phase=Phase.ONSET,
                                       records=())
    with pytest.raises(EvidenceFeatureError, match="baseline"):
        derive_features_v2(backend="frr-compose", topology_hash="a" * 64,
                           baseline=onset_as_baseline, onset=None,
                           policy=FeaturePolicyV2())


def test_wrong_onset_phase_fails_closed() -> None:
    with pytest.raises(EvidenceFeatureError, match="onset"):
        derive_features_v2(
            backend="frr-compose", topology_hash="a" * 64,
            baseline=_baseline(),
            onset=EvidenceBundle(bundle_id="o", phase=Phase.RECOVERY, records=()),
            policy=FeaturePolicyV2())


def test_malformed_evidence_json_fails_at_validation() -> None:
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate_json('{"bundle_id": 1}')  # malformed


def test_direct_fault_family_copy_is_caught() -> None:
    # a model_construct payload that smuggles a fault-family string as a value
    forged = DatasetFeaturesV2.model_construct(
        **_valid_features(backend="bgp_neighbor_removal"))
    assert audit_features_v2(forged).passed is False


def test_oracle_output_value_supplied_as_forbidden_is_caught() -> None:
    forged = DatasetFeaturesV2.model_construct(
        **_valid_features(topology_hash="scenario-secret-42"))
    result = audit_features_v2(forged, forbidden_values=frozenset({"scenario-secret-42"}))
    assert result.passed is False


def test_full_path_value_is_caught() -> None:
    forged = DatasetFeaturesV2.model_construct(
        **_valid_features(backend="runs/run-14b/evidence/onset.json"))
    assert audit_features_v2(forged).passed is False


def test_nested_forbidden_key_in_raw_payload_is_caught() -> None:
    # defense-in-depth: a raw payload (e.g. from a serialization bypass) with a
    # nested forbidden identity key is caught at any depth.
    from verifiednet.datasets.evidence_features import audit_features_v2_payload

    payload = {**_valid_features(), "meta": {"run_id": "run-14b-2r-v2"}}
    assert audit_features_v2_payload(payload).passed is False


def test_out_of_allowlist_field_in_raw_payload_is_caught() -> None:
    from verifiednet.datasets.evidence_features import audit_features_v2_payload

    payload = {**_valid_features(), "diagnosis": "iface_admin_shutdown"}
    assert audit_features_v2_payload(payload).passed is False


def test_out_of_enum_bgp_state_is_rejected_by_the_model() -> None:
    with pytest.raises(ValidationError):
        DatasetFeaturesV2(**_valid_features(bgp_worst_peer_state="broken"))


def test_audit_cannot_pass_with_error_findings() -> None:
    # the result model itself fails closed if told to pass with an ERROR
    from verifiednet.datasets.feature_leakage import (
        FeatureLeakageCode,
        FeatureLeakageFinding,
        FeatureLeakageResult,
    )
    from verifiednet.datasets.models import LeakageSeverity

    with pytest.raises(ValidationError):
        FeatureLeakageResult(passed=True, findings=(FeatureLeakageFinding(
            code=FeatureLeakageCode.FORBIDDEN_FEATURE_KEY,
            severity=LeakageSeverity.ERROR, json_path="x", detail=""),))
