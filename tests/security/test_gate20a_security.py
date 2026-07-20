"""Gate 20A security proofs: the remote-AS expansion layer loads no model, imports
no live composition root / lab / ML / network / subprocess code, and (being
contract-only) generates no run/dataset/corpus/model artifact."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_SRC = Path(__file__).resolve().parents[2] / "src" / "verifiednet"
_MODULE = _SRC / "experiment" / "remoteas_expansion.py"


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_expansion_layer_imports_no_ml_network_subprocess_or_live_execution() -> None:
    banned = {"torch", "transformers", "subprocess", "socket", "requests",
              "urllib.request", "os"}
    for module in _imports(_MODULE):
        assert module not in banned, module
        assert not module.startswith("verifiednet.orchestrator"), module
        assert not module.startswith("verifiednet.labs"), module
        assert not module.startswith("verifiednet.runtime"), module


def test_expansion_layer_only_predicts_no_run_writes() -> None:
    src = _MODULE.read_text()
    assert "group_id_for_identity" in src
    assert "assign_group_split" in src
    for banned in ("write_training_corpus", "docker", "compose_up",
                   "run_scenario", "generate_run"):
        assert banned not in src, banned


def test_expansion_layer_predicts_train_via_production_splitter() -> None:
    names = {n.id for n in ast.walk(ast.parse(_MODULE.read_text()))
             if isinstance(n, ast.Name)}
    assert "assign_group_split" in names
    assert "group_id_for_identity" in names
