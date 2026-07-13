"""Security: the artifacts package stays low-level (schemas + common + pure models).

Complements the AST guard in ``test_import_boundaries.py`` (which self-validates
with a violating fixture). This module asserts the REAL artifacts package
imports no forbidden execution/behavior module, and never uses subprocess,
shell=True, or os.system.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Sibling module in tests/security/ (on sys.path during collection).
from test_import_boundaries import scan_file

pytestmark = pytest.mark.security

SRC = Path(__file__).resolve().parents[2] / "src" / "verifiednet"


def _scan_artifacts() -> list:
    pkg = SRC / "artifacts"
    return [v for path in sorted(pkg.rglob("*.py")) for v in scan_file(path, "artifacts")]


def test_artifacts_has_no_forbidden_imports() -> None:
    offenders = [v for v in _scan_artifacts() if "forbidden-import" in v.rule]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


def test_artifacts_never_uses_subprocess_or_shell() -> None:
    offenders = [
        v for v in _scan_artifacts()
        if v.rule in {"subprocess-outside-runtime", "shell-true", "os-system"}
    ]
    assert offenders == []
