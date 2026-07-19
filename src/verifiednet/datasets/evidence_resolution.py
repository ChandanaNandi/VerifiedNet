"""Gate 18B — resolve v2 observable features from the verified run chain.

The Gate 18A ``derive_features_v2`` is a PURE function of two evidence bundles;
this module is the thin, read-only resolver that supplies those bundles by
reading the authoritative run artifacts a prepared example points at
(``trace.run_id`` joined with the model-visible ``FeatureEvidenceRef.relative_path``).
It is read-only (never mutates a run), reads ONLY the observable baseline/onset
evidence bundles (never labels, ground truth, recovery, or the oracle verdict),
audits every derived payload with the v2 leakage firewall, and fails closed on a
missing/malformed/wrong-phase bundle. It performs no network, subprocess, model,
or evaluation work.
"""

from __future__ import annotations

from pathlib import Path

from verifiednet.common.errors import VerifiedNetError
from verifiednet.datasets.evidence_features import (
    DatasetFeaturesV2,
    FeaturePolicyV2,
    audit_features_v2,
    derive_features_v2,
)
from verifiednet.datasets.features import FeatureEvidenceRef, SeparatedDatasetExample
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.schemas.evidence import EvidenceBundle


class EvidenceResolutionError(VerifiedNetError):
    """A verified run's evidence bundle could not be resolved for v2 features."""


def _load_bundle(
    run_root: Path, run_id: str, ref: FeatureEvidenceRef
) -> EvidenceBundle:
    path = run_root / run_id / ref.relative_path
    if not path.is_file():
        raise EvidenceResolutionError(f"evidence bundle missing: {path}")
    try:
        bundle = EvidenceBundle.model_validate_json(path.read_bytes())
    except Exception as exc:  # malformed bundle -> fail closed
        raise EvidenceResolutionError(
            f"malformed evidence bundle {path}: {exc}") from exc
    return bundle


def resolve_features_v2(
    example: SeparatedDatasetExample,
    *,
    run_root: Path | str,
    policy: FeaturePolicyV2,
) -> DatasetFeaturesV2:
    """Resolve + derive + audit the v2 features for ONE prepared example."""
    root = Path(run_root)
    run_id = example.trace.run_id
    baseline = _load_bundle(root, run_id, example.features.baseline_evidence)
    onset = (
        _load_bundle(root, run_id, example.features.onset_evidence)
        if example.features.onset_evidence is not None else None)
    features = derive_features_v2(
        backend=example.features.backend,
        topology_hash=example.features.topology_hash,
        baseline=baseline, onset=onset, policy=policy)
    audit = audit_features_v2(features)
    if not audit.passed:
        paths = ", ".join(f.json_path for f in audit.errors)
        raise EvidenceResolutionError(
            f"v2 feature leakage detected for {run_id}: {paths}")
    return features


def resolve_prepared_features_v2(
    prepared: LoadedPrepared,
    *,
    run_root: Path | str,
    policy: FeaturePolicyV2,
) -> dict[str, DatasetFeaturesV2]:
    """Resolve v2 features for every prepared example, keyed by ``example_id``.

    Deterministic and read-only; fails closed if any required bundle is missing,
    malformed, wrong-phase, or produces a leaking payload.
    """
    out: dict[str, DatasetFeaturesV2] = {}
    for example in prepared.examples:
        out[example.trace.example_id] = resolve_features_v2(
            example, run_root=run_root, policy=policy)
    return out
