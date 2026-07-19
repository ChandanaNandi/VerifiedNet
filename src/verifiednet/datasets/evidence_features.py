"""Gate 18A — discriminative observable-evidence features (feature policy v2).

Gate 17B showed the boundary-aligned model emits valid JSON for every example
(230/230) yet scores 0/36 accepted-test accuracy: the v1 model-visible features
(``backend``, ``topology_hash``, evidence-*presence* flags) are label-ambiguous —
identical model-visible inputs map to different fault families, so I(features;
family) ≈ 0 and any learner collapses to the majority class.

This module adds an ADDITIVE, content-addressed **feature policy v2** that exposes
a small, bounded, deterministic set of OBSERVABLE network-state facts derived from
the authoritative baseline/onset evidence bundles (``verifiednet.schemas.evidence``).
Every field is either raw observable state or a deterministic baseline→onset delta —
NEVER an oracle conclusion, a fault-family label, an identity, a split, or a path.
The v1 policy, models, allowlist, and every prior artifact are untouched.

Firewall posture (the load-bearing rule): the derivation reads only the collector
``normalized`` observations (the SAME inputs the Gate 5 oracle consumes), and never
the oracle's OUTPUT (the fault family / ground-truth reference). A dedicated audit
proves each exposed value is observable state or a delta, is inside the locked v2
allowlist, and is not a fault-family string, a full artifact path, or any label /
identity / split scalar.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.feature_leakage import (
    FeatureLeakageCode,
    FeatureLeakageFinding,
    FeatureLeakageResult,
    audit_feature_payload,
)
from verifiednet.datasets.models import LeakageSeverity
from verifiednet.schemas.base import StrictModel
from verifiednet.schemas.evidence import EvidenceBundle, Phase

FEATURE_POLICY_V2_VERSION = 2

#: The canonical fault-family class space (Gate 5). Used ONLY as a forbidden-value
#: guard in the v2 firewall — never rendered or exposed as a feature.
_FAULT_FAMILY_STRINGS: frozenset[str] = frozenset({
    "bgp_neighbor_removal",
    "bgp_prefix_withdrawal",
    "bgp_remote_as_mismatch",
    "iface_admin_shutdown",
})

#: The v2 model-visible allowlist (the audit surface): exactly the observable
#: fields ``DatasetFeaturesV2`` exposes. Sorted; a reviewer audits this constant.
FEATURE_ALLOWLIST_V2: tuple[str, ...] = (
    "backend",
    "bgp_peer_removed",
    "bgp_remote_as_changed",
    "bgp_route_withdrawn",
    "bgp_worst_peer_state",
    "interface_any_admin_down",
    "interface_any_oper_down",
    "reachability_all_success",
    "topology_hash",
)


class EvidenceFeatureCategory(StrEnum):
    """The firewall's three-way classification of each exposed field."""

    CONTEXT = "context"  # permitted inference-time context (backend, topology_hash)
    RAW_STATE = "raw_state"  # a raw observable collector readout
    STATE_DELTA = "state_delta"  # a deterministic baseline→onset observable delta


#: Every v2 field's firewall category. No field may be a diagnostic conclusion.
V2_FIELD_CATEGORY: dict[str, EvidenceFeatureCategory] = {
    "backend": EvidenceFeatureCategory.CONTEXT,
    "topology_hash": EvidenceFeatureCategory.CONTEXT,
    "bgp_worst_peer_state": EvidenceFeatureCategory.RAW_STATE,
    "interface_any_admin_down": EvidenceFeatureCategory.RAW_STATE,
    "interface_any_oper_down": EvidenceFeatureCategory.RAW_STATE,
    "reachability_all_success": EvidenceFeatureCategory.RAW_STATE,
    "bgp_peer_removed": EvidenceFeatureCategory.STATE_DELTA,
    "bgp_remote_as_changed": EvidenceFeatureCategory.STATE_DELTA,
    "bgp_route_withdrawn": EvidenceFeatureCategory.STATE_DELTA,
}

BgpPeerState = Literal[
    "established", "openconfirm", "opensent", "active", "connect", "idle",
    "other", "no_peer",
]

#: Least-converged-first ranking; the "worst" observed state is the minimum rank.
_STATE_RANK: dict[str, int] = {
    "established": 6, "openconfirm": 5, "opensent": 4, "active": 3,
    "connect": 2, "idle": 1, "other": 0,
}


class EvidenceFeatureError(VerifiedNetError):
    """Required evidence was missing or malformed; derivation fails closed."""


class FeaturePolicyV2(StrictModel):
    """A frozen, versioned, content-addressed v2 feature policy.

    Distinct from the v1 ``FeaturePolicy``: its allowlist is the observable v2
    field set. The v1 policy and its id are untouched.
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[2] = 2
    allowed_fields: tuple[str, ...] = FEATURE_ALLOWLIST_V2

    @model_validator(mode="after")
    def _locked_allowlist(self) -> FeaturePolicyV2:
        if tuple(sorted(self.allowed_fields)) != tuple(self.allowed_fields):
            raise ValueError("allowed_fields must be sorted")
        if self.allowed_fields != FEATURE_ALLOWLIST_V2:
            raise ValueError("allowed_fields must equal the canonical v2 allowlist")
        return self

    @property
    def policy_id(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "allowed_fields": list(self.allowed_fields),
        }
        return "feat-" + sha256_canonical(payload)[:16]


class DatasetFeaturesV2(StrictModel):
    """The v2 model-visible allowlist: bounded, observable network-state facts.

    Carries NO fault-family label, ground truth, diagnosis, oracle result,
    recovery, rejection code/phase, identity, split, path, or digest. Every
    non-context field is a raw observable collector readout or a deterministic
    baseline→onset delta (see ``V2_FIELD_CATEGORY``).
    """

    schema_version: Literal[1] = 1
    feature_policy_id: str = Field(min_length=1)
    backend: str
    topology_hash: str
    bgp_worst_peer_state: BgpPeerState
    interface_any_admin_down: bool
    interface_any_oper_down: bool
    reachability_all_success: bool
    bgp_peer_removed: bool
    bgp_remote_as_changed: bool
    bgp_route_withdrawn: bool


# ---------------------------------------------------------------------------
# Pure evidence indexing + derivation (no filesystem/network/model)
# ---------------------------------------------------------------------------

_BGP_PEER = re.compile(r"^bgp\.peer\.(.+)\.(state|remote_as|present)$")
_IFACE = re.compile(r"^iface\.(.+)\.(admin|oper)$")
_ROUTE = re.compile(r"^route\.(.+)\.(present|protocols)$")
_PING = re.compile(r"^ping\.(.+)\.(all_success|probe_count|success_count)$")


class _Indexed(StrictModel):
    peers: dict[str, dict[str, str]] = Field(default_factory=dict)
    ifaces: dict[str, dict[str, str]] = Field(default_factory=dict)
    routes: dict[str, dict[str, str]] = Field(default_factory=dict)
    pings: dict[str, dict[str, str]] = Field(default_factory=dict)


def _index_bundle(bundle: EvidenceBundle) -> _Indexed:
    """Canonically index the observable collector readouts in one bundle."""
    peers: dict[str, dict[str, str]] = {}
    ifaces: dict[str, dict[str, str]] = {}
    routes: dict[str, dict[str, str]] = {}
    pings: dict[str, dict[str, str]] = {}
    for record in bundle.records:
        target = record.source.target
        for key, value in record.normalized.items():
            val = str(value)
            if (m := _BGP_PEER.match(key)) is not None:
                peers.setdefault(f"{target}|{m.group(1)}", {})[m.group(2)] = val
            elif (m := _IFACE.match(key)) is not None:
                ifaces.setdefault(f"{target}|{m.group(1)}", {})[m.group(2)] = val
            elif (m := _ROUTE.match(key)) is not None:
                routes.setdefault(f"{target}|{m.group(1)}", {})[m.group(2)] = val
            elif (m := _PING.match(key)) is not None:
                pings.setdefault(f"{target}|{m.group(1)}", {})[m.group(2)] = val
    return _Indexed(peers=peers, ifaces=ifaces, routes=routes, pings=pings)


def _canonical_state(raw: str) -> str:
    s = raw.strip().lower()
    return s if s in _STATE_RANK else "other"


def _worst_peer_state(idx: _Indexed) -> BgpPeerState:
    present_states = [
        _canonical_state(p["state"])
        for p in idx.peers.values()
        if "state" in p and p.get("present", "true") != "false"
    ]
    if not present_states:
        return "no_peer"
    worst = min(present_states, key=lambda s: _STATE_RANK[s])
    return worst  # type: ignore[return-value]


def _route_is_bgp_present(entry: dict[str, str]) -> bool:
    return (entry.get("present", "false") == "true"
            and "bgp" in entry.get("protocols", ""))


def derive_features_v2(
    *,
    backend: str,
    topology_hash: str,
    baseline: EvidenceBundle,
    onset: EvidenceBundle | None,
    policy: FeaturePolicyV2,
) -> DatasetFeaturesV2:
    """Derive v2 observable features from authoritative evidence bundles.

    Pure and deterministic: no filesystem, network, subprocess, model, mutation,
    randomness, or timestamp. Reads ONLY collector observations, never the oracle
    output. Fails closed if the required baseline bundle is missing/malformed or is
    not the baseline phase. When ``onset`` is absent (an abstention/precondition
    example legitimately has no onset), raw state reflects the healthy baseline and
    every delta is ``False``.
    """
    # Accepted examples carry a BASELINE reference; rejected/abstention examples
    # carry the PRECONDITION check as their reference bundle (no onset). Both are
    # the healthy-intent reference state; ONSET/RECOVERY are never a reference.
    if baseline.phase not in (Phase.BASELINE, Phase.PRECONDITION):
        raise EvidenceFeatureError(
            f"baseline bundle has phase {baseline.phase!r}, expected "
            f"baseline or precondition")
    if onset is not None and onset.phase is not Phase.ONSET:
        raise EvidenceFeatureError(
            f"onset bundle has phase {onset.phase!r}, expected onset")

    base_idx = _index_bundle(baseline)
    obs_idx = _index_bundle(onset) if onset is not None else base_idx

    # --- raw observable state (from the observed/onset phase) ---------------
    worst = _worst_peer_state(obs_idx)
    admin_down = any(i.get("admin") == "down" for i in obs_idx.ifaces.values())
    oper_down = any(i.get("oper") == "down" for i in obs_idx.ifaces.values())
    reach_ok = all(
        p.get("all_success", "true") == "true" for p in obs_idx.pings.values()
    )

    # --- deterministic baseline→onset deltas -------------------------------
    if onset is None:
        peer_removed = remote_as_changed = route_withdrawn = False
    else:
        peer_removed = any(
            p.get("present") == "false" for p in obs_idx.peers.values()
        )
        remote_as_changed = any(
            key in base_idx.peers
            and "remote_as" in base_idx.peers[key] and "remote_as" in entry
            and base_idx.peers[key]["remote_as"] != entry["remote_as"]
            for key, entry in obs_idx.peers.items()
        )
        route_withdrawn = any(
            _route_is_bgp_present(base_entry)
            and not _route_is_bgp_present(obs_idx.routes.get(key, {}))
            for key, base_entry in base_idx.routes.items()
        )

    return DatasetFeaturesV2(
        feature_policy_id=policy.policy_id,
        backend=backend,
        topology_hash=topology_hash,
        bgp_worst_peer_state=worst,
        interface_any_admin_down=admin_down,
        interface_any_oper_down=oper_down,
        reachability_all_success=reach_ok,
        bgp_peer_removed=peer_removed,
        bgp_remote_as_changed=remote_as_changed,
        bgp_route_withdrawn=route_withdrawn,
    )


# ---------------------------------------------------------------------------
# v2 leakage firewall (extends the Part-4 audit with v2-specific proofs)
# ---------------------------------------------------------------------------

_PATH_MARKERS = ("/", ".json", "run-", "evidence")


def audit_features_v2_payload(
    payload: dict[str, object],
    *,
    forbidden_values: frozenset[str] = frozenset(),
) -> FeatureLeakageResult:
    """Audit a SERIALIZED v2 feature dict (defense-in-depth on the raw payload).

    Operates on the actual dict — not just the typed model — so a leak injected
    by a ``model_construct`` bypass, an extra key, or a nested structure is still
    caught. Proves: (1) no forbidden identity/label/split key or value at any
    depth (the Part-4 walk, extended with the fault-family strings and any
    caller-supplied evaluator-only scalars); (2) every model-visible field is
    inside the locked v2 allowlist (no extra field); (3) no value is a
    fault-family string; (4) no value resembles a full artifact path; (5) every
    field is CONTEXT / RAW_STATE / STATE_DELTA — never a diagnostic conclusion.
    """
    findings = list(audit_feature_payload(
        payload, forbidden_values=forbidden_values | _FAULT_FAMILY_STRINGS))

    visible = set(payload) - {"schema_version", "feature_policy_id"}
    extra = sorted(visible - set(FEATURE_ALLOWLIST_V2))
    for name in extra:
        findings.append(FeatureLeakageFinding(
            code=FeatureLeakageCode.FORBIDDEN_FEATURE_KEY,
            severity=LeakageSeverity.ERROR, json_path=name,
            detail="field outside the locked v2 allowlist"))
    for name in visible:
        if name not in V2_FIELD_CATEGORY:
            findings.append(FeatureLeakageFinding(
                code=FeatureLeakageCode.FORBIDDEN_FEATURE_KEY,
                severity=LeakageSeverity.ERROR, json_path=name,
                detail="uncategorized field (no observable/delta category)"))
    for name, value in payload.items():
        if isinstance(value, str) and value:
            if value in _FAULT_FAMILY_STRINGS:
                findings.append(FeatureLeakageFinding(
                    code=FeatureLeakageCode.FORBIDDEN_FEATURE_VALUE,
                    severity=LeakageSeverity.ERROR, json_path=name,
                    detail="fault-family string present in features"))
            if name not in ("feature_policy_id", "topology_hash") and any(
                    marker in value for marker in _PATH_MARKERS):
                findings.append(FeatureLeakageFinding(
                    code=FeatureLeakageCode.FORBIDDEN_FEATURE_VALUE,
                    severity=LeakageSeverity.ERROR, json_path=name,
                    detail="value resembles an artifact path"))
    has_error = any(f.severity is LeakageSeverity.ERROR for f in findings)
    return FeatureLeakageResult(passed=not has_error, findings=tuple(findings))


def audit_features_v2(
    features: DatasetFeaturesV2,
    *,
    forbidden_values: frozenset[str] = frozenset(),
) -> FeatureLeakageResult:
    """Audit a v2 feature model by serializing it and auditing the payload."""
    return audit_features_v2_payload(
        features.model_dump(mode="json"), forbidden_values=forbidden_values)


# ---------------------------------------------------------------------------
# Shared, single-source v2 observation render (byte-identical for eval+training)
# ---------------------------------------------------------------------------

def _b(value: bool) -> str:
    return "true" if value else "false"


def render_evidence_observation_block(features: DatasetFeaturesV2) -> str:
    """The canonical, deterministic v2 observation block.

    ONE source of truth so the deployed inference prompt and the training input
    render byte-identically (Gate 17A boundary preservation). Fixed field order;
    no trailing newline (the prompt wrapper adds the separator).
    """
    return (
        "Observation metadata:\n"
        f"- backend: {features.backend}\n"
        f"- topology_hash: {features.topology_hash}\n"
        f"- bgp_worst_peer_state: {features.bgp_worst_peer_state}\n"
        f"- bgp_peer_removed: {_b(features.bgp_peer_removed)}\n"
        f"- bgp_remote_as_changed: {_b(features.bgp_remote_as_changed)}\n"
        f"- bgp_route_withdrawn: {_b(features.bgp_route_withdrawn)}\n"
        f"- interface_admin_down: {_b(features.interface_any_admin_down)}\n"
        f"- interface_oper_down: {_b(features.interface_any_oper_down)}\n"
        f"- reachability_all_success: {_b(features.reachability_all_success)}"
    )
