"""Optional Gate 19A real-chain proof (read-only): apply the family-balanced
selection policy to the registered v3 prepared chain, prove the exact 20/20/20/4
budget-preserving composition, deterministic round-robin order, a byte-identical
v2 corpus build for shared sources vs the Gate 18B natural-order corpus, the
unchanged 384/64/448 token budget, and source/prior-artifact immutability.

Creates NO execution, checkpoint, evaluation, benchmark, or experiment artifact
and runs NO fine-tune. DOUBLE-GATED: the ``integration`` marker AND
``VERIFIEDNET_RUN_GATE19A=1`` plus a v3 artifact root; the token budget also needs
a local Qwen snapshot dir. Skips by default.
"""

from __future__ import annotations

import collections
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE19A") == "1"
_V3_ROOT = os.environ.get("VERIFIEDNET_GATE19A_V3_ROOT", "")
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")
_PRIOR_DIRS = os.environ.get("VERIFIEDNET_GATE19A_PRIOR_ARTIFACT_DIRS", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_ENABLED and _V3_ROOT and Path(_V3_ROOT).is_dir()),
        reason="Gate 19A chain proof is opt-in and needs VERIFIEDNET_RUN_GATE19A=1 "
               "and a v3 artifact root"),
]

GATE19A_EXPECTED = {"bgp_neighbor_removal": 20, "bgp_prefix_withdrawal": 20,
                    "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 20}


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_gate19a_family_balanced_selection_on_v3_chain() -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.evidence_features import FeaturePolicyV2
    from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.evaluation.prompt import render_diagnosis_prompt_v2
    from verifiednet.experiment import cap_training_corpus
    from verifiednet.training import diagnosis_target_template
    from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
    from verifiednet.training.policy import (
        evidence_observation_input_template,
        evidence_observation_training_policy,
        render_training_input_v2,
    )
    from verifiednet.training.selection import (
        compare_training_corpora,
        family_balanced_selection_policy,
        select_family_balanced,
    )

    v3 = Path(_V3_ROOT)
    run_root = v3 / "chain" / "runs"
    prepared_dir = v3 / "chain" / "prepared"
    before = _fingerprint(v3)
    prior_roots = [Path(p) for p in _PRIOR_DIRS.split(os.pathsep) if p]
    prior_before = {str(p): _fingerprint(p) for p in prior_roots if p.is_dir()}

    prepared = load_prepared(prepared_dir)
    task = diagnosis_task()
    feature_policy = FeaturePolicyV2()
    v3_input = evidence_observation_input_template(
        task_id=task.task_id, feature_policy_v2_id=feature_policy.policy_id)
    tgt = diagnosis_target_template(task_id=task.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=task.task_id, input_template=v3_input, target_template=tgt)
    kw = dict(run_root=run_root, feature_policy_v2=feature_policy,
              training_data_policy=data_policy, input_template=v3_input,
              target_template=tgt)

    # 1-2. train-partition family availability
    avail: collections.Counter = collections.Counter()
    for ex in prepared.examples:
        if (ex.trace.partition is DatasetPartition.TRAIN
                and ex.trace.example_kind is DatasetExampleKind.ACCEPTED_FAULT):
            avail[ex.labels.fault_family] += 1

    # 3-6. select exactly 64, prove 20/20/20/4 and deterministic round-robin
    policy = family_balanced_selection_policy()
    selection = select_family_balanced(prepared, policy=policy)
    again = select_family_balanced(prepared, policy=policy)
    assert selection == again, "selection must be deterministic"
    assert selection.total_count == 64
    counts = {q.fault_family: q.count for q in selection.per_family_counts}
    assert counts == GATE19A_EXPECTED, counts
    assert len(set(selection.ordered_source_example_ids)) == 64
    first4 = [s.fault_family for s in selection.selected[:4]]
    assert first4 == list(policy.family_order), "round-robin must lead one-per-family"

    # 7-9. build the balanced v2 corpus; audit is internal; deployed==training bytes
    balanced = build_evidence_observation_corpus(prepared, selection=selection, **kw)
    assert len(balanced.examples) == 64
    v2_by_source = {}
    for ex in prepared.examples:
        if ex.trace.example_id in set(selection.ordered_source_example_ids):
            v2_by_source[ex.trace.example_id] = ex
    from verifiednet.datasets.evidence_resolution import resolve_features_v2
    max_input_tokens = 0
    max_total_tokens = 0
    tok = None
    if _MODEL_DIR and importlib.util.find_spec("transformers") is not None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from transformers import (  # type: ignore[import-not-found, unused-ignore]
            AutoTokenizer,
        )
        tok = AutoTokenizer.from_pretrained(_MODEL_DIR, local_files_only=True)
    for source in v2_by_source.values():
        feats = resolve_features_v2(source, run_root=run_root, policy=feature_policy)
        deployed = render_diagnosis_prompt_v2(feats)
        assert deployed == render_training_input_v2(feats)
        if tok is not None:
            n_in = len(tok.encode(deployed, add_special_tokens=False))
            n_tgt = len(tok.encode(tgt.render(source.labels.fault_family),
                                   add_special_tokens=False))
            max_input_tokens = max(max_input_tokens, n_in)
            max_total_tokens = max(max_total_tokens, n_in + n_tgt + 1)

    # 10-12. Gate 18B natural first-64 corpus and the deterministic comparison
    gate18b = cap_training_corpus(
        build_evidence_observation_corpus(prepared, **kw), max_example_count=64)
    comparison = compare_training_corpora(gate18b, balanced)
    assert comparison.baseline_count == comparison.candidate_count == 64
    assert comparison.baseline_unique and comparison.candidate_unique
    assert comparison.shared_inputs_equal and comparison.shared_targets_equal
    assert comparison.feature_policy_equal and comparison.input_template_equal
    assert comparison.target_template_equal
    assert not comparison.ordering_identical or comparison.intersection_count == 64
    overlap = comparison.intersection_count
    added = len(comparison.added_source_ids)
    removed = len(comparison.removed_source_ids)

    if tok is not None:
        assert max_input_tokens <= 384, f"input tokens {max_input_tokens} > 384"
        assert max_total_tokens <= 448, f"total tokens {max_total_tokens} > 448"

    # 13. source + prior artifacts byte-identical (read-only)
    assert _fingerprint(v3) == before
    for p in prior_roots:
        if p.is_dir():
            assert _fingerprint(p) == prior_before[str(p)], f"mutated prior: {p}"

    print(f"GATE19A: available={dict(avail)} selected={counts} total=64 "
          f"gate18b_overlap={overlap} added={added} removed={removed} "
          f"shared_inputs_equal={comparison.shared_inputs_equal} "
          f"max_input_tok={max_input_tokens} max_total_tok={max_total_tokens} "
          f"balanced_corpus_id={balanced.training_corpus_id}")
