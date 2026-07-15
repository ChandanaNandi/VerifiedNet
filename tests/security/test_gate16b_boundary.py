"""Gate 16B security proofs: the v2 training corpus is firewall-clean,
model-free, network-free, and leaves the source prepared corpus untouched;
no evaluation or benchmark fact reaches the trainer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security


def test_v2_corpus_firewall_excludes_all_held_out_identifiers(
    tmp_path: Path, gate14b_corpus_pipeline, gate16_corpora,
) -> None:
    from verifiednet.experiment import audit_test_firewall
    from verifiednet.training import write_training_corpus

    ctx, _a, _r = gate14b_corpus_pipeline(tmp_path, runs_cap=1)
    _v1, v2 = gate16_corpora(ctx.loaded, max_example_count=64)
    written = write_training_corpus(v2, tmp_path / "training-corpora")
    payload = b"".join(
        p.read_bytes() for p in sorted(written.root.rglob("*"))
        if p.is_file())
    audit = audit_test_firewall(
        prepared=ctx.loaded, training_corpus=v2,
        training_side_payloads={"v2_training_corpus_store": payload})
    assert audit.passed is True, [c for c in audit.checks if not c.passed]
    assert audit.held_out_example_ids > 0  # there ARE held-out examples


def test_v2_corpus_build_is_model_free_and_network_free(
    tmp_path: Path, eval_pipeline, gate16_corpora, monkeypatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 16B corpus build must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b")],
                        rejected=["run-rej"])
    _v1, v2 = gate16_corpora(ctx.loaded, max_example_count=8)
    assert v2.examples
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_no_evaluation_or_benchmark_fact_can_reach_the_trainer() -> None:
    import ast

    training = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
                / "training")
    for path in sorted(training.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert not module.startswith("verifiednet.evaluation"), path


def test_source_prepared_corpus_is_untouched_by_the_v2_build(
    tmp_path: Path, eval_pipeline, gate16_corpora,
) -> None:
    import hashlib

    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    prepared_dir = Path(str(ctx.prepared_dir))
    before = {str(p.relative_to(prepared_dir)):
              hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(prepared_dir.rglob("*")) if p.is_file()}
    gate16_corpora(ctx.loaded, max_example_count=1)
    after = {str(p.relative_to(prepared_dir)):
             hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(prepared_dir.rglob("*")) if p.is_file()}
    assert after == before
