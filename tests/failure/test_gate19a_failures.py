"""Gate 19A failure tests: the selector and corpus builder fail closed on a
missing/short family, an invalid quota, a duplicate/non-train source, a missing
accepted label, an unsupported family, and a prepared-digest mismatch."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.datasets.features import AbstentionLabels
from verifiednet.evaluation import diagnosis_task
from verifiednet.training import diagnosis_target_template
from verifiednet.training.corpus import TrainingCorpusError
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    evidence_observation_input_template,
    evidence_observation_training_policy,
)
from verifiednet.training.selection import (
    SelectionError,
    family_balanced_selection_policy,
    select_family_balanced,
)

pytestmark = pytest.mark.failure

_TASK = diagnosis_task()
_FULL = {"bgp_neighbor_removal": 40, "bgp_prefix_withdrawal": 40,
         "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 44}


def test_policy_rejects_quota_sum_mismatch() -> None:
    with pytest.raises(ValueError, match="sum to target_total"):
        family_balanced_selection_policy(target_total=64, allocation=(
            ("bgp_neighbor_removal", 20), ("bgp_prefix_withdrawal", 20),
            ("bgp_remote_as_mismatch", 4), ("iface_admin_shutdown", 19)))


def test_policy_rejects_unsupported_family() -> None:
    with pytest.raises(ValueError, match="unsupported fault family"):
        family_balanced_selection_policy(target_total=4, allocation=(
            ("not_a_real_family", 4),))


def test_missing_family_fails_closed(balanced_prepared) -> None:
    avail = dict(_FULL)
    del avail["bgp_remote_as_mismatch"]
    with pytest.raises(SelectionError, match=r"absent|insufficient"):
        select_family_balanced(balanced_prepared(avail),
                               policy=family_balanced_selection_policy())


def test_insufficient_family_fails_closed(balanced_prepared) -> None:
    avail = dict(_FULL)
    avail["bgp_remote_as_mismatch"] = 3  # need 4
    with pytest.raises(SelectionError, match=r"insufficient|no redistribution"):
        select_family_balanced(balanced_prepared(avail),
                               policy=family_balanced_selection_policy())


def test_duplicate_source_identity_fails_closed(balanced_prepared) -> None:
    from dataclasses import replace
    build = balanced_prepared
    # the SAME example_id appears under two different families, both fully
    # selected under a 1-per-family allocation -> the dedup guard must fire.
    shared_a = build.example("ex-shared", "grp-a", "bgp_neighbor_removal")
    shared_b = build.example("ex-shared", "grp-b", "bgp_prefix_withdrawal")
    prepared = build({}, extra=[shared_a, shared_b])
    prepared = replace(prepared, examples=(shared_a, shared_b))
    policy = family_balanced_selection_policy(
        target_total=2, allocation=(
            ("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1)))
    with pytest.raises(SelectionError, match="duplicate"):
        select_family_balanced(prepared, policy=policy)


def test_missing_accepted_label_fails_closed(balanced_prepared) -> None:
    from dataclasses import replace
    build = balanced_prepared
    prepared = build(_FULL)
    bad = build.example("ex-9999", "grp-9999", "iface_admin_shutdown")
    # force an accepted-kind trace carrying abstention labels
    abst = AbstentionLabels(label_policy_id="label-x", rejection_code="rc",
                            failed_phase="precondition")
    bad = bad.model_construct(features=bad.features, labels=abst, trace=bad.trace)
    prepared2 = replace(prepared, examples=(*prepared.examples, bad))
    with pytest.raises(SelectionError, match="lacks accepted labels"):
        select_family_balanced(prepared2, policy=family_balanced_selection_policy())


def test_unsupported_family_in_train_fails_closed(balanced_prepared) -> None:
    from dataclasses import replace
    build = balanced_prepared
    prepared = build(_FULL)
    rogue = build.example("ex-8888", "grp-8888", "some_unknown_family")
    prepared2 = replace(prepared, examples=(*prepared.examples, rogue))
    with pytest.raises(SelectionError, match="unsupported fault family"):
        select_family_balanced(prepared2, policy=family_balanced_selection_policy())


def test_builder_rejects_prepared_digest_mismatch(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b"),
                                            ("pf-ref", "run-c")], rejected=[])
    policy = FeaturePolicyV2()
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    sel = select_family_balanced(
        ctx.loaded, policy=family_balanced_selection_policy(
            target_total=3, allocation=(
                ("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
                ("bgp_remote_as_mismatch", 1))))
    tampered = sel.model_copy(update={"source_prepared_digest": "prep-wrong"})
    with pytest.raises(TrainingCorpusError, match="different prepared corpus"):
        build_evidence_observation_corpus(
            ctx.loaded, run_root=ctx.run_root, feature_policy_v2=policy,
            training_data_policy=data_policy, input_template=v3, target_template=tgt,
            selection=tampered)


def test_builder_rejects_non_train_selected_source(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b"),
                                            ("pf-ref", "run-c")], rejected=[])
    policy = FeaturePolicyV2()
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    sel = select_family_balanced(
        ctx.loaded, policy=family_balanced_selection_policy(
            target_total=3, allocation=(
                ("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
                ("bgp_remote_as_mismatch", 1))))
    # replace one selected id with an id absent from the train-accepted set
    forged = sel.model_copy(update={
        "ordered_source_example_ids": (
            "ex-not-a-train-source", *sel.ordered_source_example_ids[1:])})
    with pytest.raises(TrainingCorpusError, match="not an accepted train example"):
        build_evidence_observation_corpus(
            ctx.loaded, run_root=ctx.run_root, feature_policy_v2=policy,
            training_data_policy=data_policy, input_template=v3, target_template=tgt,
            selection=forged)
