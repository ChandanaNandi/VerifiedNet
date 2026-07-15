"""Gate 16A security proofs: template work is model-free, network-free,
subprocess-free, evaluation-artifact-free, and source-preserving."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security


def test_v2_serialization_is_model_free_and_network_free(
    tmp_path: Path, eval_pipeline, monkeypatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 16A must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    from verifiednet.evaluation import diagnosis_task
    from verifiednet.training import (
        build_training_corpus,
        contract_aligned_input_template,
        contract_aligned_training_policy,
        diagnosis_target_template,
        write_training_corpus,
    )

    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b")],
                        rejected=["run-rej"])
    task_id = diagnosis_task().task_id
    v2 = contract_aligned_input_template(
        task_id=task_id,
        feature_policy_id=ctx.loaded.manifest.feature_policy_id)
    target = diagnosis_target_template(task_id=task_id)
    corpus = build_training_corpus(
        ctx.loaded,
        training_data_policy=contract_aligned_training_policy(
            task_id=task_id, input_template=v2, target_template=target),
        input_template=v2, target_template=target)
    write_training_corpus(corpus, tmp_path / "training-corpora")
    # no model execution, no tokenizer loading, no ML runtime anywhere
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_rendered_inputs_carry_no_labels_or_trace_metadata(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.training import (
        build_training_corpus,
        contract_aligned_input_template,
        contract_aligned_training_policy,
        diagnosis_target_template,
    )

    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    task_id = diagnosis_task().task_id
    v2 = contract_aligned_input_template(
        task_id=task_id,
        feature_policy_id=ctx.loaded.manifest.feature_policy_id)
    target = diagnosis_target_template(task_id=task_id)
    corpus = build_training_corpus(
        ctx.loaded,
        training_data_policy=contract_aligned_training_policy(
            task_id=task_id, input_template=v2, target_template=target),
        input_template=v2, target_template=target)
    by_example = {e.trace.example_id: e for e in ctx.loaded.examples}
    for example in corpus.examples:
        source = by_example[example.trace.source_example_id]
        text = example.input.text
        # no identity, split, or authoritative label beyond the target
        assert example.trace.source_example_id not in text
        assert example.trace.source_group_id not in text
        assert source.trace.run_id not in text
        from verifiednet.datasets.features import AcceptedLabels

        assert isinstance(source.labels, AcceptedLabels)
        assert source.labels.scenario_id not in text
        # the class SPACE is public; the specific answer must only be in the
        # target, never asserted in the input beyond that list membership
        assert example.target.text.count(source.labels.fault_family) == 1


def test_no_subprocess_and_no_evaluation_artifact_access() -> None:
    import ast

    package = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
               / "training")
    banned_prefixes = ("verifiednet.evaluation", "subprocess", "torch",
                       "transformers", "peft", "accelerate", "bitsandbytes")
    lazy_allowed = {"hfexecutor.py"}  # the sanctioned lazy-ML site (Gate 10F)
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                for banned in banned_prefixes:
                    if module == banned or module.startswith(banned + "."):
                        assert path.name in lazy_allowed and banned in (
                            "torch", "transformers"), (path.name, module)


def test_source_prepared_corpus_is_untouched_by_v2_corpus_builds(
    tmp_path: Path, eval_pipeline,
) -> None:
    import hashlib

    from verifiednet.evaluation import diagnosis_task
    from verifiednet.training import (
        build_training_corpus,
        contract_aligned_input_template,
        contract_aligned_training_policy,
        diagnosis_target_template,
        write_training_corpus,
    )

    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    prepared_dir = Path(str(ctx.prepared_dir))
    before = {str(p.relative_to(prepared_dir)):
              hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(prepared_dir.rglob("*")) if p.is_file()}
    task_id = diagnosis_task().task_id
    v2 = contract_aligned_input_template(
        task_id=task_id,
        feature_policy_id=ctx.loaded.manifest.feature_policy_id)
    target = diagnosis_target_template(task_id=task_id)
    corpus = build_training_corpus(
        ctx.loaded,
        training_data_policy=contract_aligned_training_policy(
            task_id=task_id, input_template=v2, target_template=target),
        input_template=v2, target_template=target)
    write_training_corpus(corpus, tmp_path / "training-corpora")
    after = {str(p.relative_to(prepared_dir)):
             hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(prepared_dir.rglob("*")) if p.is_file()}
    assert after == before
