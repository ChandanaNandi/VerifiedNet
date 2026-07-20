"""Gate 20C security proofs: the group-aware selection layer loads no model,
imports no evaluation/live/ML/network/subprocess code, and reads only frozen TRAIN
labels (never validation/test labels, predictions, or evaluation artifacts)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_SRC = Path(__file__).resolve().parents[2] / "src" / "verifiednet"
_MODULE = _SRC / "training" / "selection.py"


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_selection_imports_no_ml_eval_network_or_subprocess() -> None:
    banned = {"torch", "transformers", "subprocess", "socket", "requests",
              "urllib.request", "os"}
    for module in _imports(_MODULE):
        assert module not in banned, module
        assert not module.startswith("verifiednet.evaluation"), module
        assert not module.startswith("verifiednet.orchestrator"), module
        assert not module.startswith("verifiednet.labs"), module
        assert not module.startswith("verifiednet.runtime"), module


def _code_identifiers(path: Path) -> set[str]:
    """All Name/attribute identifiers actually referenced in code (docstrings and
    comments excluded), so a security check never matches mere prose."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_group_selection_reads_only_train_partition() -> None:
    tree = ast.parse(_MODULE.read_text())
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    # the group-aware selector gates on TRAIN + ACCEPTED_FAULT
    assert "TRAIN" in attrs
    assert "ACCEPTED_FAULT" in attrs
    # and never calls into evaluation/checkpoint/logit machinery (code, not prose)
    banned = {"evaluate", "evaluate_prepared_corpus", "logits", "predict",
              "load_verified_checkpoint_bundle", "score"}
    assert not (banned & _code_identifiers(_MODULE))
