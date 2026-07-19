"""Gate 18A contract tests: the v2 models are frozen and extra-forbidding, the
v2 allowlist is locked and label-free, v1 is byte-unchanged, the Gate 8
instructions/schema are frozen, and the deployed v2 prompt equals the v2
training input byte-for-byte."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verifiednet.datasets.evidence_features import (
    FEATURE_ALLOWLIST_V2,
    DatasetFeaturesV2,
    FeaturePolicyV2,
)
from verifiednet.datasets.feature_leakage import FORBIDDEN_FEATURE_KEYS
from verifiednet.datasets.features import FEATURE_ALLOWLIST_V1, FeaturePolicy
from verifiednet.evaluation.prompt import (
    _INSTRUCTIONS,
    _RESPONSE_SCHEMA,
    render_diagnosis_prompt_v2,
)
from verifiednet.schemas.evidence import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    Phase,
)
from verifiednet.training.policy import (
    _CONTRACT_INSTRUCTIONS,
    _CONTRACT_RESPONSE_SCHEMA,
    render_training_input_v2,
)

pytestmark = pytest.mark.contract

V1_FEAT_ID = "feat-4f792db1ef08ee5f"
V2_FEAT_ID = "feat-228b357dd9f256fa"


def _features() -> DatasetFeaturesV2:
    return DatasetFeaturesV2(
        feature_policy_id=V2_FEAT_ID, backend="frr-compose",
        topology_hash="a" * 64, bgp_worst_peer_state="active",
        interface_any_admin_down=True, interface_any_oper_down=True,
        reachability_all_success=False, bgp_peer_removed=False,
        bgp_remote_as_changed=False, bgp_route_withdrawn=False)


def test_models_are_frozen_and_extra_forbid() -> None:
    f = _features()
    with pytest.raises(ValidationError):
        f.backend = "other"
    with pytest.raises(ValidationError):
        DatasetFeaturesV2(
            feature_policy_id=V2_FEAT_ID, backend="frr-compose",
            topology_hash="a" * 64, bgp_worst_peer_state="active",
            interface_any_admin_down=False, interface_any_oper_down=False,
            reachability_all_success=True, bgp_peer_removed=False,
            bgp_remote_as_changed=False, bgp_route_withdrawn=False,
            fault_family="x")  # extra=forbid
    with pytest.raises(ValidationError):
        FeaturePolicyV2(policy_version=1)  # locked to 2


def test_v2_policy_allowlist_is_locked() -> None:
    assert FeaturePolicyV2().allowed_fields == FEATURE_ALLOWLIST_V2
    with pytest.raises(ValidationError):
        FeaturePolicyV2(allowed_fields=("backend",))


def test_v2_allowlist_contains_no_label_or_identity_fields() -> None:
    assert not (set(FEATURE_ALLOWLIST_V2) & FORBIDDEN_FEATURE_KEYS)
    for banned in ("fault_family", "scenario_id", "template_id", "run_id",
                   "ground_truth_reference", "expected_outcome", "partition",
                   "split_policy_id", "rejection_code"):
        assert banned not in FEATURE_ALLOWLIST_V2


def test_v1_is_byte_unchanged() -> None:
    assert FeaturePolicy().policy_id == V1_FEAT_ID
    assert FEATURE_ALLOWLIST_V1 == (
        "backend", "baseline_evidence", "onset_evidence", "topology_hash")


def test_gate8_instructions_and_schema_are_frozen_and_mirrored() -> None:
    # v2 changes only the observation block; instructions/schema are the frozen
    # Gate 8 text, mirrored byte-for-byte on the training side (Gate 16A).
    assert _CONTRACT_INSTRUCTIONS == _INSTRUCTIONS
    assert _CONTRACT_RESPONSE_SCHEMA == _RESPONSE_SCHEMA
    prompt = render_diagnosis_prompt_v2(_features())
    assert prompt.startswith(_INSTRUCTIONS)
    assert prompt.endswith(_RESPONSE_SCHEMA)


def test_deployed_v2_prompt_equals_training_input_bytes() -> None:
    f = _features()
    assert render_diagnosis_prompt_v2(f) == render_training_input_v2(f)


def test_v2_prompt_contains_no_label_and_only_v2_fields() -> None:
    f = _features()
    prompt = render_diagnosis_prompt_v2(f)
    assert "fault_family" not in prompt.split("Respond with")[0]  # not in obs block
    for family in ("bgp_neighbor_removal", "iface_admin_shutdown"):
        # families appear only in the frozen candidate list, never as a value
        assert prompt.count(family) == 1


def test_derive_requires_baseline_phase() -> None:
    from verifiednet.datasets.evidence_features import (
        EvidenceFeatureError,
        derive_features_v2,
    )

    ts = datetime(2026, 1, 1, tzinfo=UTC)
    not_baseline = EvidenceBundle(bundle_id="b", phase=Phase.ONSET, records=(
        EvidenceRecord(evidence_id="ev", phase=Phase.ONSET,
                       source=EvidenceSource(collector="frr.interfaces",
                                             target="router_a"),
                       raw_sha256="0" * 64, raw_payload="", normalized={},
                       captured_at=ts, run_seq=1),))
    with pytest.raises(EvidenceFeatureError, match="baseline"):
        derive_features_v2(backend="frr-compose", topology_hash="a" * 64,
                           baseline=not_baseline, onset=None,
                           policy=FeaturePolicyV2())
