"""Gate 18A security proofs: the evidence-features derivation is import-pure —
no model, torch/transformers, evaluation/benchmark/trainer, network, subprocess,
or filesystem — and deriving + auditing loads no ML runtime."""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_MODULE = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
           / "datasets" / "evidence_features.py")

_BANNED_IMPORTS = {
    "torch", "transformers", "subprocess", "socket", "requests",
    "urllib.request", "os", "pathlib",
}
_BANNED_IMPORT_PREFIXES = ("verifiednet.evaluation", "verifiednet.training",
                           "verifiednet.experiment")


def test_module_imports_no_ml_network_fs_or_downstream_layer() -> None:
    tree = ast.parse(_MODULE.read_text())
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            assert module not in _BANNED_IMPORTS, module
            for prefix in _BANNED_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module


def test_module_defines_no_file_or_process_calls() -> None:
    # AST-level: no call to open/Path/os.system/subprocess/socket/urlopen etc.
    # (substring checks would false-positive on the module docstring prose).
    tree = ast.parse(_MODULE.read_text())
    banned_calls = {"open", "system", "popen", "urlopen", "run", "Popen",
                    "create_connection"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            assert name not in banned_calls, name


def test_derivation_and_audit_load_no_ml_runtime() -> None:
    from verifiednet.datasets.evidence_features import (
        FeaturePolicyV2,
        audit_features_v2,
        derive_features_v2,
    )
    from verifiednet.schemas.evidence import (
        EvidenceBundle,
        EvidenceRecord,
        EvidenceSource,
        Phase,
    )

    ts = datetime(2026, 1, 1, tzinfo=UTC)
    base = EvidenceBundle(bundle_id="b", phase=Phase.BASELINE, records=(
        EvidenceRecord(evidence_id="ev", phase=Phase.BASELINE,
                       source=EvidenceSource(collector="frr.interfaces",
                                             target="router_a"),
                       raw_sha256="0" * 64, raw_payload="",
                       normalized={"iface.eth1.admin": "up"},
                       captured_at=ts, run_seq=1),))
    f = derive_features_v2(backend="frr-compose", topology_hash="a" * 64,
                           baseline=base, onset=None, policy=FeaturePolicyV2())
    assert audit_features_v2(f).passed is True
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules
