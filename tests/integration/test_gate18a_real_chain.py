"""Optional Gate 18A real-chain proof (read-only): derive v2 observable features
on the registered v3 prepared/run chain and prove discrimination, firewall
cleanliness, deployed==training byte equality, the 384/64/448 token budget, and
source immutability. Creates NO training/evaluation/checkpoint artifacts.

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE18A=1`` plus a
v3 artifact root and (for the token budget) a local Qwen snapshot dir. Skips by
default so offline CI never reads the chain or loads a tokenizer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE18A") == "1"
_V3_ROOT = os.environ.get("VERIFIEDNET_GATE18A_V3_ROOT", "")
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_ENABLED and _V3_ROOT and Path(_V3_ROOT).is_dir()),
        reason="Gate 18A chain proof is opt-in and needs VERIFIEDNET_RUN_GATE18A=1 "
               "and a v3 artifact root"),
]


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_gate18a_discriminative_features_on_v3_chain() -> None:
    import collections

    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.evidence_features import (
        FeaturePolicyV2,
        audit_features_v2,
        derive_features_v2,
    )
    from verifiednet.datasets.models import DatasetExampleKind
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.evaluation.prompt import render_diagnosis_prompt_v2
    from verifiednet.schemas.evidence import EvidenceBundle
    from verifiednet.training import diagnosis_target_template
    from verifiednet.training.policy import render_training_input_v2

    v3 = Path(_V3_ROOT)
    runs = v3 / "chain" / "runs"
    prepared_dir = v3 / "chain" / "prepared"
    before = _fingerprint(v3)

    prepared = load_prepared(prepared_dir)
    policy = FeaturePolicyV2()
    target_template = diagnosis_target_template(task_id=diagnosis_task().task_id)

    def load_bundle(run_id: str, ref: object) -> EvidenceBundle | None:
        if ref is None:
            return None
        path = runs / run_id / ref.relative_path  # type: ignore[attr-defined]
        return EvidenceBundle.model_validate_json(path.read_bytes())

    v1_by_fam: dict[tuple, set[str]] = collections.defaultdict(set)
    v2_by_fam: dict[tuple, set[str]] = collections.defaultdict(set)
    v1_payloads: list[tuple] = []
    v2_payloads: list[tuple] = []
    coverage: collections.Counter = collections.Counter()
    audit_failures = 0
    tok = None
    if _MODEL_DIR and importlib.util.find_spec("transformers") is not None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from transformers import (  # type: ignore[import-not-found, unused-ignore]
            AutoTokenizer,
        )

        tok = AutoTokenizer.from_pretrained(_MODEL_DIR, local_files_only=True)
    max_input_tokens = 0
    max_total_tokens = 0

    for ex in prepared.examples:
        if ex.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        fam = ex.labels.fault_family
        onset_present = ex.features.onset_evidence is not None
        v1 = (ex.features.backend, ex.features.topology_hash, onset_present)
        base = load_bundle(ex.trace.run_id, ex.features.baseline_evidence)
        onset = load_bundle(ex.trace.run_id, ex.features.onset_evidence)
        assert base is not None
        features = derive_features_v2(
            backend=ex.features.backend, topology_hash=ex.features.topology_hash,
            baseline=base, onset=onset, policy=policy)
        if not audit_features_v2(features).passed:
            audit_failures += 1
        v2 = tuple(sorted(features.model_dump(
            exclude={"feature_policy_id", "schema_version"}).items()))
        v1_payloads.append(v1)
        v2_payloads.append(v2)
        v1_by_fam[v1].add(fam)
        v2_by_fam[v2].add(fam)
        flag = ("admin_down" if features.interface_any_admin_down else
                "peer_removed" if features.bgp_peer_removed else
                "remote_as_changed" if features.bgp_remote_as_changed else
                "route_withdrawn" if features.bgp_route_withdrawn else "none")
        coverage[(fam, flag)] += 1
        # deployed v2 prompt == v2 training input, byte-for-byte
        deployed = render_diagnosis_prompt_v2(features)
        assert deployed == render_training_input_v2(features)
        if tok is not None:
            target = target_template.render(fam)
            n_in = len(tok.encode(deployed, add_special_tokens=False))
            n_tgt = len(tok.encode(target, add_special_tokens=False))
            max_input_tokens = max(max_input_tokens, n_in)
            max_total_tokens = max(max_total_tokens, n_in + n_tgt + 1)

    n = len(v2_payloads)
    assert n >= 64, f"expected the accepted corpus, got {n}"
    assert audit_failures == 0, f"{audit_failures} v2 payloads leaked"
    # v1 is fully ambiguous; v2 removes ALL cross-family collisions
    v1_ambiguous = sum(1 for p in v1_payloads if len(v1_by_fam[p]) > 1)
    v2_ambiguous = sum(1 for p in v2_payloads if len(v2_by_fam[p]) > 1)
    assert v1_ambiguous == n, "expected v1 to be fully family-ambiguous"
    assert v2_ambiguous == 0, "v2 must have no cross-family-ambiguous payloads"
    assert len(set(v2_payloads)) > len(set(v1_payloads))
    # every family is covered by its own discriminative flag (never 'none')
    families = {fam for fam, _flag in coverage}
    assert len(families) == 4
    assert not any(flag == "none" for _fam, flag in coverage)

    if tok is not None:
        assert max_input_tokens <= 384, f"input tokens {max_input_tokens} > 384"
        assert max_total_tokens <= 448, f"total tokens {max_total_tokens} > 448"

    # source chain byte-identical before and after (read-only)
    assert _fingerprint(v3) == before
    print(f"GATE18A: n={n} unique_v1={len(set(v1_payloads))} "
          f"unique_v2={len(set(v2_payloads))} v1_ambiguous={v1_ambiguous} "
          f"v2_ambiguous={v2_ambiguous} max_input_tok={max_input_tokens} "
          f"max_total_tok={max_total_tokens} audit_failures={audit_failures}")
