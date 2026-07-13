"""Consolidated AST security boundary guard.

Provenance: consolidates the triplicated AST scan pattern from
neuronoc-network-ops-assistant (MIT) backend/tests/{test_remediation,test_validation,
test_telemetry}.py at commit 5f24447, parameterized into a single policy-driven guard
(Gate 2.5 HIGH correction; harvest verb: copy with modifications).

Policy enforced (Gate 3):
- ``schemas`` imports no VerifiedNet implementation package.
- ``collectors`` may not import ``verifiednet.runtime.mutation`` (mutation-capable
  executor types) nor ``verifiednet.faults``.
- ``verifiers`` may not import ``runtime``, ``labs`` or ``collectors``.
- ``incidents`` may not import ``runtime``, ``labs``, ``collectors`` or ``faults``.
- ``orchestrator`` is the top composition root: NO other package may import it
  (the dependency arrow only points down into it, never out of it).
- ``subprocess`` may only be imported by ``verifiednet/runtime/process.py``.
- No ``shell=True`` anywhere under ``src/``.
- No ``os.system`` calls anywhere under ``src/``.

The guard also validates itself against deliberately violating fixture modules in
``tests/fixtures/security_violations/``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "verifiednet"
VIOLATION_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "security_violations"

SUBPROCESS_ALLOWED = {SRC / "runtime" / "process.py"}

#: The composition root. Every other package is "below" it and must not import
#: it; only the orchestrator package itself (and test/tooling code outside src)
#: may reference ``verifiednet.orchestrator``.
ORCHESTRATOR_ROOT = "orchestrator"

# package -> forbidden import prefixes
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "schemas": (
        "verifiednet.common",
        "verifiednet.runtime",
        "verifiednet.labs",
        "verifiednet.collectors",
        "verifiednet.verifiers",
        "verifiednet.faults",
        "verifiednet.incidents",
    ),
    "collectors": ("verifiednet.runtime.mutation", "verifiednet.faults"),
    "verifiers": ("verifiednet.runtime", "verifiednet.labs", "verifiednet.collectors"),
    "incidents": (
        "verifiednet.runtime",
        "verifiednet.labs",
        "verifiednet.collectors",
        "verifiednet.faults",
    ),
    # artifacts is low-level persistence: schemas + common + the PURE data
    # models for transcript/ledger only. It must not import live execution
    # behavior (runtime executors/process), labs, collectors, verifiers,
    # incident builders/oracle, or scenario implementations.
    "artifacts": (
        "verifiednet.runtime.mutation",
        "verifiednet.runtime.readonly",
        "verifiednet.runtime.process",
        "verifiednet.labs",
        "verifiednet.collectors",
        "verifiednet.verifiers",
        "verifiednet.incidents",
        "verifiednet.faults.bgp_remote_as_mismatch",
        "verifiednet.faults.scenario",
        "verifiednet.faults.frr_commands",
    ),
}


@dataclass(frozen=True)
class Violation:
    path: str
    lineno: int
    rule: str
    detail: str


def _module_imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return found


def scan_file(path: Path, package: str | None) -> list[Violation]:
    """Scan one python file against the full policy. Pure; no imports executed."""
    violations: list[Violation] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = _module_imports(tree)

    for module, lineno in imports:
        if (module == "subprocess" or module.startswith("subprocess.")) and (
            path not in SUBPROCESS_ALLOWED
        ):
            violations.append(
                Violation(str(path), lineno, "subprocess-outside-runtime", module)
            )
        if package and package in FORBIDDEN_IMPORTS:
            for banned in FORBIDDEN_IMPORTS[package]:
                if module == banned or module.startswith(banned + "."):
                    violations.append(
                        Violation(str(path), lineno, f"{package}-forbidden-import", module)
                    )
        # The composition root may not be imported by anything below it.
        if package != ORCHESTRATOR_ROOT and (
            module == "verifiednet.orchestrator"
            or module.startswith("verifiednet.orchestrator.")
        ):
            violations.append(
                Violation(str(path), lineno, "imports-orchestrator", module)
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    violations.append(
                        Violation(str(path), node.lineno, "shell-true", ast.dump(node.func))
                    )
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "system"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                violations.append(Violation(str(path), node.lineno, "os-system", "os.system"))
    return violations


def _package_of(path: Path) -> str | None:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else None


def scan_tree() -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(SRC.rglob("*.py")):
        violations.extend(scan_file(path, _package_of(path)))
    return violations


@pytest.mark.security
def test_source_tree_has_no_boundary_violations() -> None:
    violations = scan_tree()
    assert violations == [], "\n".join(
        f"{v.path}:{v.lineno} [{v.rule}] {v.detail}" for v in violations
    )


@pytest.mark.security
def test_schemas_package_imports_no_implementation_packages() -> None:
    pkg = SRC / "schemas"
    for path in sorted(pkg.rglob("*.py")):
        assert not [v for v in scan_file(path, "schemas") if "forbidden-import" in v.rule]


# ---------------------------------------------------------------------------
# Guard self-validation against deliberately violating fixtures
# ---------------------------------------------------------------------------

@pytest.mark.security
def test_guard_detects_subprocess_violation_fixture() -> None:
    path = VIOLATION_FIXTURES / "uses_subprocess.py"
    violations = scan_file(path, "collectors")
    assert any(v.rule == "subprocess-outside-runtime" for v in violations)


@pytest.mark.security
def test_guard_detects_shell_true_fixture() -> None:
    path = VIOLATION_FIXTURES / "uses_shell_true.py"
    violations = scan_file(path, None)
    assert any(v.rule == "shell-true" for v in violations)


@pytest.mark.security
def test_guard_detects_os_system_fixture() -> None:
    path = VIOLATION_FIXTURES / "uses_os_system.py"
    violations = scan_file(path, None)
    assert any(v.rule == "os-system" for v in violations)


@pytest.mark.security
def test_guard_detects_lab_subprocess_bypass_fixture() -> None:
    # A lab module must drive processes through verifiednet.runtime.process, the
    # single subprocess boundary. A lab importing subprocess directly is a
    # bypass and must be flagged regardless of the package it is scanned under.
    path = VIOLATION_FIXTURES / "lab_imports_subprocess.py"
    violations = scan_file(path, "labs")
    assert any(v.rule == "subprocess-outside-runtime" for v in violations)


@pytest.mark.security
def test_real_labs_package_never_imports_subprocess() -> None:
    # Guard the live Gate 4 labs backend/adapters: no module under labs/ may
    # import subprocess; all process execution flows through the runtime.
    pkg = SRC / "labs"
    offenders = [
        v
        for path in sorted(pkg.rglob("*.py"))
        for v in scan_file(path, "labs")
        if v.rule == "subprocess-outside-runtime"
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_guard_detects_collector_mutation_import_fixture() -> None:
    path = VIOLATION_FIXTURES / "collector_imports_mutation.py"
    violations = scan_file(path, "collectors")
    assert any(v.rule == "collectors-forbidden-import" for v in violations)


@pytest.mark.security
def test_guard_detects_artifacts_labs_import_fixture() -> None:
    path = VIOLATION_FIXTURES / "artifacts_imports_labs.py"
    violations = scan_file(path, "artifacts")
    assert any(v.rule == "artifacts-forbidden-import" for v in violations)


@pytest.mark.security
def test_real_artifacts_package_stays_low_level() -> None:
    pkg = SRC / "artifacts"
    offenders = [
        v
        for path in sorted(pkg.rglob("*.py"))
        for v in scan_file(path, "artifacts")
        if "forbidden-import" in v.rule
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_guard_detects_lower_package_importing_orchestrator_fixture() -> None:
    # A lower package (here scanned as ``labs``) importing the composition root
    # inverts the dependency arrow and must be flagged.
    path = VIOLATION_FIXTURES / "labs_imports_orchestrator.py"
    violations = scan_file(path, "labs")
    assert any(v.rule == "imports-orchestrator" for v in violations)


@pytest.mark.security
def test_orchestrator_may_import_itself() -> None:
    # The composition root wires the layers together; internal imports within
    # the orchestrator package must NOT be flagged as boundary violations.
    pkg = SRC / "orchestrator"
    offenders = [
        v
        for path in sorted(pkg.rglob("*.py"))
        for v in scan_file(path, "orchestrator")
        if v.rule == "imports-orchestrator"
    ]
    assert offenders == []


@pytest.mark.security
def test_no_lower_package_imports_orchestrator() -> None:
    # Guard the real source tree: no package below the composition root may
    # import ``verifiednet.orchestrator``.
    offenders = [
        v
        for path in sorted(SRC.rglob("*.py"))
        if _package_of(path) != ORCHESTRATOR_ROOT
        for v in scan_file(path, _package_of(path))
        if v.rule == "imports-orchestrator"
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_guard_detects_incidents_runtime_import_fixture() -> None:
    path = VIOLATION_FIXTURES / "incidents_imports_runtime.py"
    violations = scan_file(path, "incidents")
    assert any(v.rule == "incidents-forbidden-import" for v in violations)


@pytest.mark.security
def test_guard_accepts_clean_fixture() -> None:
    path = VIOLATION_FIXTURES / "clean_module.py"
    assert scan_file(path, "collectors") == []
