"""Gate 17A security proofs: constructing the boundary-aligned objective and
its examples is import-pure (no evaluation import, no torch/transformers, no
network, no subprocess, no checkpoint or experiment writes)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_TRAINING = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
             / "training")


def test_training_package_imports_no_evaluation() -> None:
    for path in sorted(_TRAINING.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert not module.startswith("verifiednet.evaluation"), path


def test_boundary_objective_build_is_ml_free_and_network_free(
    monkeypatch,
) -> None:
    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("Gate 17A objective build must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    from verifiednet.training import (
        boundary_aligned_objective_policy,
        build_boundary_aligned_example,
    )

    pol = boundary_aligned_objective_policy()
    tokens, labels = build_boundary_aligned_example(
        input_token_ids=(1, 2, 3), target_token_ids=(4, 5),
        eos_token_id=9, max_total_tokens=64)
    assert pol.objective_policy_id.startswith("objpol-")
    assert tokens == (1, 2, 3, 4, 5, 9)
    assert labels == (-100, -100, -100, 4, 5, 9)
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_bounds_module_declares_no_disk_or_subprocess_side_effects() -> None:
    # the objective/example construction path is pure: bounds.py imports no
    # subprocess/socket/requests and writes no checkpoint or experiment store.
    tree = ast.parse((_TRAINING / "bounds.py").read_text())
    banned = {"subprocess", "socket", "requests", "urllib.request"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name not in banned, a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in banned, node.module
