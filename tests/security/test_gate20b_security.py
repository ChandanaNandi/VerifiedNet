"""Gate 20B security proofs: the offline campaign-result / append-only-diff /
readiness layer imports no ML, live composition root, lab, runtime, network, or
subprocess code, and writes no run/dataset/corpus/model artifact. Live execution,
projection, and v4 registration happen only in the gated harness (outside src)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_SRC = Path(__file__).resolve().parents[2] / "src" / "verifiednet"
_MODULE = _SRC / "experiment" / "remoteas_campaign.py"


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_campaign_layer_imports_no_ml_network_subprocess_or_live_execution() -> None:
    banned = {"torch", "transformers", "subprocess", "socket", "requests",
              "urllib.request", "os"}
    for module in _imports(_MODULE):
        assert module not in banned, module
        assert not module.startswith("verifiednet.orchestrator"), module
        assert not module.startswith("verifiednet.labs"), module
        assert not module.startswith("verifiednet.runtime"), module


def test_campaign_layer_writes_no_run_dataset_or_model() -> None:
    src = _MODULE.read_text()
    for banned in ("write_training_corpus", "write_dataset", "docker",
                   "compose_up", "run_scenario", "run_accepted_incident",
                   "register_evaluation_corpus", "torch.save"):
        assert banned not in src, banned


def test_campaign_layer_only_records_and_verifies() -> None:
    src = _MODULE.read_text()
    # it consumes the prepared corpus + Gate 20A contracts and reuses canonical
    # hashing for content-addressing; it never fabricates truth.
    assert "sha256_canonical" in src
    assert "LoadedPrepared" in src
