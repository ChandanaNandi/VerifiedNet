"""Gate 19A security proofs: the family-balanced selection layer imports no
evaluation package, loads no model, uses no network/subprocess, and depends on
no filesystem-enumeration order; the training package still imports no
evaluation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_SRC = Path(__file__).resolve().parents[2] / "src" / "verifiednet"
_TRAINING = _SRC / "training"
_SELECTION = _TRAINING / "selection.py"


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_selection_imports_no_ml_network_subprocess_or_evaluation() -> None:
    banned = {"torch", "transformers", "subprocess", "socket", "requests",
              "urllib.request", "os", "glob"}
    for module in _imports(_SELECTION):
        assert module not in banned, module
        assert not module.startswith("verifiednet.evaluation"), module


def test_training_package_still_imports_no_evaluation() -> None:
    for path in sorted(_TRAINING.glob("*.py")):
        for module in _imports(path):
            assert not module.startswith("verifiednet.evaluation"), path


def test_selection_reads_only_train_accepted_labels() -> None:
    # AST-level: the selector references the TRAIN partition and accepted labels,
    # and NEVER the validation/test partitions or any evaluation-record attribute.
    tree = ast.parse(_SELECTION.read_text())
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "TRAIN" in attrs
    assert "AcceptedLabels" in names
    for banned in ("VALIDATION", "TEST", "prediction", "benchmark", "confusion"):
        assert banned not in attrs, banned
