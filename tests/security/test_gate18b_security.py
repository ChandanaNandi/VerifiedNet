"""Gate 18B security proofs: the v2 training-corpus builder and the evidence
resolver never import the evaluation package, load a model, or use network/
subprocess; the resolver reads only observable evidence bundles."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_SRC = Path(__file__).resolve().parents[2] / "src" / "verifiednet"
_TRAINING = _SRC / "training"
_RESOLVER = _SRC / "datasets" / "evidence_resolution.py"


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


def test_resolver_imports_no_ml_network_subprocess_or_evaluation() -> None:
    banned = {"torch", "transformers", "subprocess", "socket", "requests",
              "urllib.request"}
    for module in _imports(_RESOLVER):
        assert module not in banned, module
        assert not module.startswith("verifiednet.evaluation"), module
        assert not module.startswith("verifiednet.training"), module


def test_resolver_reads_only_evidence_not_labels() -> None:
    # the resolver may reference the model-visible baseline/onset evidence refs,
    # but never the label/ground-truth/recovery references.
    src = _RESOLVER.read_text()
    assert "baseline_evidence" in src and "onset_evidence" in src
    for banned in ("ground_truth_reference", "recovery_reference",
                   "incident_reference", ".labels", "fault_family"):
        assert banned not in src, banned
