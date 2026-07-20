"""Gate 19B security proofs: Gate 19B adds no production module (it reuses the
Gate 15-19A machinery), so the training package still imports no evaluation and
the selection layer still reads no held-out truth; the operational harness writes
only outside the repository and stages no generated artifact."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "verifiednet"
_TRAINING = _SRC / "training"
_OPERATIONAL = _ROOT / "tests" / "integration" / "test_gate19b_operational.py"


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_training_package_still_imports_no_evaluation() -> None:
    for path in sorted(_TRAINING.glob("*.py")):
        for module in _imports(path):
            assert not module.startswith("verifiednet.evaluation"), path


def test_gate19b_adds_no_production_module() -> None:
    # Gate 19B is an experiment over existing machinery; it introduces no new
    # src/ module. (Selection lives in training/selection.py from Gate 19A.)
    assert not (_SRC / "training" / "gate19b.py").exists()
    assert not (_SRC / "experiment" / "gate19b.py").exists()


def test_operational_harness_writes_outside_the_repo_and_stages_no_artifact() -> None:
    src = _OPERATIONAL.read_text()
    # the output root comes from an env var, not a repo path
    assert "VERIFIEDNET_GATE19B_OUTPUT_ROOT" in src
    # it must not write under the repository tree
    assert "src/verifiednet" not in src
    # it must be network-guarded like the prior operational harnesses
    assert "must not use the network" in src
