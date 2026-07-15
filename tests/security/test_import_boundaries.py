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

#: Gate 10F/11: the sanctioned lazy-ML-import sites. Only these files may
#: reference torch/transformers — one training executor (Gate 10F) and one
#: checkpoint-inference backend (Gate 11) — and the dedicated laziness tests
#: separately prove every such import is function-level (lazy), so both
#: modules import cleanly without the training-hf extras installed.
ML_LAZY_ALLOWED = {
    SRC / "training" / "hfexecutor.py",
    SRC / "evaluation" / "hfinference.py",
}
ML_LAZY_MODULES = ("torch", "transformers", "peft", "bitsandbytes",
                   "accelerate", "safetensors")

#: Gate 11/12: the sanctioned consumers of the training package. The
#: checkpoint-backed predictor (Gate 11) and the verified base-model bundle
#: (Gate 12) consume VERIFIED artifacts through exactly one module of the
#: training layer — the verified-checkpoint store (structural safetensors
#: parsing + real-checkpoint verification). Everything else in evaluation
#: stays training free, and training still never imports evaluation
#: (ADR-0022 unchanged).
TRAINING_CONSUMER_ALLOWED = {
    SRC / "evaluation" / "checkpointpred.py",
    SRC / "evaluation" / "basemodel.py",
}
TRAINING_CONSUMER_MODULES = ("verifiednet.training.realckptstore",)

#: The composition root. Every other package is "below" it and must not import
#: it; only the orchestrator package itself (and test/tooling code outside src)
#: may reference ``verifiednet.orchestrator``.
ORCHESTRATOR_ROOT = "orchestrator"

#: The read-only dataset engine (Gate 6). A top-level CONSUMER of verified run
#: artifacts (ADR-0018): no other src package may import it EXCEPT the evaluation
#: engine, and it may not import the live composition root or any live-execution
#: package.
DATASETS_ROOT = "datasets"

#: The deterministic evaluation engine (Gate 7). It consumes the read-only
#: dataset engine (the prepared corpus) but must not import the live composition
#: root or any live-execution package, and no other src package may import it.
EVALUATION_ROOT = "evaluation"

#: The supervised training-corpus layer (Gate 10A). It consumes the prepared
#: corpus but must NEVER import the evaluation package (evaluation and benchmark
#: artifacts are not training sources, ADR-0022), any live-execution package, or
#: any model-training library. No other src package may import it.
TRAINING_ROOT = "training"

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
        "verifiednet.faults.bgp_neighbor_removal",
        "verifiednet.faults.iface_admin_shutdown",
        "verifiednet.faults.bgp_prefix_withdrawal",
        "verifiednet.faults.scenario",
        "verifiednet.faults.frr_commands",
    ),
    # datasets is a read-only consumer of verified run artifacts (ADR-0018): it
    # reads schemas + common + the artifacts package only. It must NOT import the
    # live composition root, the live lab, mutation/execution runtime, collectors,
    # verifiers, or scenario implementations — it never runs or re-derives a run.
    "datasets": (
        "verifiednet.orchestrator",
        "verifiednet.labs",
        "verifiednet.collectors",
        "verifiednet.verifiers",
        "verifiednet.faults",
        "verifiednet.runtime.mutation",
        "verifiednet.runtime.readonly",
        "verifiednet.runtime.process",
    ),
    # evaluation consumes the read-only dataset engine (Gate 7) but is itself a
    # deterministic, offline CONSUMER: it must NOT import the live composition
    # root, the live lab, mutation/execution runtime, collectors, verifiers, or
    # scenario implementations — it never runs, re-derives, or trains anything.
    # Gate 11: ML libraries are banned exactly as in training; the ONE
    # sanctioned lazy site is evaluation/hfinference.py (ML_LAZY_ALLOWED).
    "evaluation": (
        "verifiednet.orchestrator",
        "verifiednet.labs",
        "verifiednet.collectors",
        "verifiednet.verifiers",
        "verifiednet.faults",
        "verifiednet.runtime.mutation",
        "verifiednet.runtime.readonly",
        "verifiednet.runtime.process",
        "torch",
        "transformers",
        "peft",
        "bitsandbytes",
        "accelerate",
    ),
    # training is the Gate 10A supervised-corpus layer: it consumes the prepared
    # corpus (datasets) ONLY. It must not import the evaluation package (its
    # artifacts are never training sources — ADR-0022), any live-execution
    # package, or any model-training library (no training happens in Gate 10A).
    "training": (
        "verifiednet.orchestrator",
        "verifiednet.labs",
        "verifiednet.collectors",
        "verifiednet.verifiers",
        "verifiednet.faults",
        "verifiednet.runtime.mutation",
        "verifiednet.runtime.readonly",
        "verifiednet.runtime.process",
        "verifiednet.evaluation",
        "torch",
        "transformers",
        "peft",
        "bitsandbytes",
        "accelerate",
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
                    if (path in ML_LAZY_ALLOWED
                            and banned.split(".")[0] in ML_LAZY_MODULES):
                        continue  # the sanctioned lazy-ML site (see above)
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
        # The read-only dataset engine may not be imported by anything below it
        # EXCEPT its legitimate downstream consumers: the evaluation engine and
        # the training-corpus layer.
        if package not in (DATASETS_ROOT, EVALUATION_ROOT, TRAINING_ROOT) and (
            module == "verifiednet.datasets"
            or module.startswith("verifiednet.datasets.")
        ):
            violations.append(
                Violation(str(path), lineno, "imports-datasets", module)
            )
        # The training layer may not be imported by anything else, EXCEPT the
        # one sanctioned Gate 11 consumer: the checkpoint-backed predictor may
        # import ONLY the verified-checkpoint store (never planning, execution,
        # or the training executor).
        if package != TRAINING_ROOT and (
            module == "verifiednet.training"
            or module.startswith("verifiednet.training.")
        ) and not (
            path in TRAINING_CONSUMER_ALLOWED
            and module in TRAINING_CONSUMER_MODULES
        ):
            violations.append(
                Violation(str(path), lineno, "imports-training", module)
            )
        # The evaluation engine may not be imported by anything else (one-way flow).
        if package != EVALUATION_ROOT and (
            module == "verifiednet.evaluation"
            or module.startswith("verifiednet.evaluation.")
        ):
            violations.append(
                Violation(str(path), lineno, "imports-evaluation", module)
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
def test_guard_detects_datasets_importing_orchestrator_fixture() -> None:
    # The read-only dataset engine importing the live composition root inverts
    # the one-way truth flow (ADR-0018) and must be flagged.
    path = VIOLATION_FIXTURES / "datasets_imports_orchestrator.py"
    violations = scan_file(path, "datasets")
    assert any(v.rule == "datasets-forbidden-import" for v in violations)


@pytest.mark.security
def test_real_datasets_package_stays_read_only() -> None:
    pkg = SRC / "datasets"
    offenders = [
        v
        for path in sorted(pkg.rglob("*.py"))
        for v in scan_file(path, "datasets")
        if "forbidden-import" in v.rule
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_no_lower_package_imports_datasets() -> None:
    # No src package outside the dataset engine may import it (one-way flow),
    # except its legitimate downstream consumers (evaluation + training).
    offenders = [
        v
        for path in sorted(SRC.rglob("*.py"))
        if _package_of(path) not in (DATASETS_ROOT, EVALUATION_ROOT, TRAINING_ROOT)
        for v in scan_file(path, _package_of(path))
        if v.rule == "imports-datasets"
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_real_training_package_stays_isolated() -> None:
    # training may not import evaluation, live execution, or model-training libs.
    pkg = SRC / "training"
    if not pkg.is_dir():
        pytest.skip("training package not present")
    offenders = [
        v
        for path in sorted(pkg.rglob("*.py"))
        for v in scan_file(path, "training")
        if "forbidden-import" in v.rule
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_no_lower_package_imports_training() -> None:
    # No src package outside the training layer may import it (one-way flow),
    # except the Gate 11 checkpoint predictor's narrow verified-checkpoint
    # consumption (TRAINING_CONSUMER_ALLOWED / TRAINING_CONSUMER_MODULES).
    offenders = [
        v
        for path in sorted(SRC.rglob("*.py"))
        if _package_of(path) != TRAINING_ROOT
        for v in scan_file(path, _package_of(path))
        if v.rule == "imports-training"
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_real_evaluation_package_stays_read_only() -> None:
    pkg = SRC / "evaluation"
    if not pkg.is_dir():
        pytest.skip("evaluation package not present")
    offenders = [
        v
        for path in sorted(pkg.rglob("*.py"))
        for v in scan_file(path, "evaluation")
        if "forbidden-import" in v.rule
    ]
    assert offenders == [], "\n".join(f"{v.path}:{v.lineno} {v.detail}" for v in offenders)


@pytest.mark.security
def test_no_lower_package_imports_evaluation() -> None:
    # No src package outside the evaluation engine may import it (one-way flow).
    offenders = [
        v
        for path in sorted(SRC.rglob("*.py"))
        if _package_of(path) != EVALUATION_ROOT
        for v in scan_file(path, _package_of(path))
        if v.rule == "imports-evaluation"
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


def _assert_ml_imports_are_lazy(path: Path) -> None:
    """A sanctioned lazy-ML site may import torch/transformers/safetensors
    ONLY inside function bodies — never at module level, so importing the
    module (and the package) succeeds without the training-hf extras."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_level: list[str] = []
    for node in tree.body:  # module level ONLY
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level.append(node.module)
    for name in module_level:
        assert name.split(".")[0] not in ML_LAZY_MODULES, name
    # and the lazy imports genuinely exist somewhere in the file (the
    # exemption is used, not dormant)
    all_imports = {m.split(".")[0] for m, _ in _module_imports(tree)}
    assert "torch" in all_imports and "transformers" in all_imports


def test_hfexecutor_ml_imports_are_lazy() -> None:
    _assert_ml_imports_are_lazy(SRC / "training" / "hfexecutor.py")


def test_hfinference_ml_imports_are_lazy() -> None:
    _assert_ml_imports_are_lazy(SRC / "evaluation" / "hfinference.py")


@pytest.mark.security
def test_guard_detects_evaluation_importing_training_fixture() -> None:
    # An evaluation module other than the sanctioned checkpoint predictor
    # importing the training layer must be flagged (fixture path is outside
    # TRAINING_CONSUMER_ALLOWED).
    path = VIOLATION_FIXTURES / "evaluation_imports_training.py"
    violations = scan_file(path, "evaluation")
    assert any(v.rule == "imports-training" for v in violations)


@pytest.mark.security
def test_checkpoint_predictor_training_consumption_is_narrow() -> None:
    # The sanctioned consumer file may import ONLY the verified-checkpoint
    # store module — the real source file must carry no other training import,
    # and no other evaluation file may import training at all.
    for path in sorted((SRC / "evaluation").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        training_imports = [
            m for m, _ in _module_imports(tree)
            if m == "verifiednet.training"
            or m.startswith("verifiednet.training.")
        ]
        if path in TRAINING_CONSUMER_ALLOWED:
            assert training_imports, "the sanctioned consumer must be used"
            assert all(m in TRAINING_CONSUMER_MODULES
                       for m in training_imports), training_imports
        else:
            assert training_imports == [], (str(path), training_imports)
