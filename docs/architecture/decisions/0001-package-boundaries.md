# 0001 — Eight packages with a schemas/interfaces split, AST-enforced

**Status:** Accepted (Gate 3; validated in Gate 2.5 §6–7)
**Date:** 2026-07-11

## Context

VerifiedNet must keep a hard, verifiable boundary between components that only produce
data (schemas, verifiers) and components that touch the outside world (the command
runner, the lab, the fault mutator). The project's non-negotiable principles require
that ground truth never depend on side-effecting or model code, and that collectors can
never mutate a device. A boundary that lives only in documentation is not enforceable.

## Decision

Split `src/verifiednet` into eight packages — `schemas`, `common`, `runtime`, `labs`,
`collectors`, `verifiers`, `faults`, `incidents` — with a fixed dependency direction
(`schemas` and `common` are sibling roots; `incidents` is a data-only consumer at the
top). Behavioral interfaces live in their **owning** package, not in `schemas`
(`LabBackend`→labs, `FaultScenario`→faults, `EvidenceCollector`→collectors,
`Verifier`→verifiers); `schemas` holds pure data contracts only. A single consolidated
AST guard (`tests/security/test_import_boundaries.py`) enforces the import rules in CI:
`collectors` may not import `runtime.mutation` or `faults`; `verifiers` may not import
`runtime`/`labs`/`collectors`; `incidents` may not import any of them; `subprocess` is
importable only in `runtime/process.py`; no `shell=True`, no `os.system` anywhere.

## Consequences

- The safety properties are checked mechanically on every commit, not by review.
- `schemas` stays DB-free and import-light (pydantic + stdlib only), so contracts can be
  reused anywhere without pulling in runtime.
- The split is more packages than a vertical slice strictly needs, but the AST security
  boundary *requires* it — merging `runtime` into `labs` would put the mutation surface
  inside a package that `collectors` import. This was the reason a "premature split"
  criticism was rejected in Gate 2.5.

## References

- `../gate3/package_boundaries.md`, `../gate3/runtime_security.md`
- `../gate2_5/architecture_validation.md` §6–7 (W2, dependency-DAG correction)
