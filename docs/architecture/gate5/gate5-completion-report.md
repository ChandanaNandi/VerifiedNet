# Gate 5 — Completion Report

**Status:** Implementation complete; offline gate green; live closure recorded
on the canonical host (section below). Gate 5 turned the single Gate 4 live
incident into a **small verified incident library**: four accepted fault
families, a deterministic precondition rejection, a bounded parameter matrix
with reverse-orientation proof, cross-family isolation, and a shared
integrity-verified run index — all through one thin composition root.

Gate 5 produced a small verified incident library; **it did NOT produce a large
dataset**. No model participated; no agent selected scenarios; no dynamic plugin
system exists; only FRR and the two-router topology are supported; the canonical
reference platform remains macOS/arm64. Ground truth is assembled only from
injected-fault metadata and deterministic verifier verdicts (ADR-0009/0010).

## Scope delivered, by substep

| Substep | Deliverable | Document |
|---|---|---|
| 5.0 | Evidence-based fault-family plan + proof matrix | `fault-family-plan.md` |
| 5.1 | Shared enablers: pure-data phase plans, family binding, check factories, `expected_peers` | `neighbor-removal-family.md` |
| 5.2 | BGP neighbor removal (missing-object family) | `neighbor-removal-family.md`, ADR-0017 ref |
| 5.3 | Interface administrative shutdown (probe-verified FRR-mode) | `interface-shutdown-and-prefix-withdrawal.md` |
| 5.4 | BGP prefix-advertisement withdrawal (routing-intent; no forced reset) | `interface-shutdown-and-prefix-withdrawal.md` |
| 5.5 | Bounded scenario catalog + validation + `run_accepted_case` | `scenario-parameterization.md` |
| 5.6 | Cross-family regression, isolation, repeatability, failure isolation | `cross-family-regression.md` |
| 5.7 | This report + acceptance matrix + closure |  |

## Gate 5 acceptance matrix

| Area | Required proof | Result |
|---|---|---|
| Remote-AS mismatch | accepted live reference + reverse orientation | PASS (`ras-ref` live test; `ras-rev` reverse suite) |
| Neighbor removal | accepted live reference + reverse orientation | PASS (`nr-ref`; `nr-rev`) |
| Interface shutdown | FRR-mode reference + reverse orientation | PASS (`if-ref`; `if-rev`), FRR-mode probe-verified |
| Prefix withdrawal | reference + reverse orientation | PASS (`pf-ref`; `pf-rev`) |
| Precondition rejection | deterministic zero-mutation rejected run | PASS (live rejected incident) |
| Parameter validation | invalid cases fail before mutation | PASS (validation unit tests) |
| Restoration | baseline-equivalent configuration | PASS (byte-identical `config_unchanged` per family) |
| Recovery | BGP/routes/ping restored | PASS (recovery verdicts committable) |
| Transcript | every pending mutation paired | PASS (per-run pending==completed) |
| Ledger | legal and terminal | PASS (ends `RECOVERY_VERIFIED`) |
| Artifacts | canonical and verified | PASS (`verify_run_dir` per run) |
| Run index | all approved runs discoverable | PASS (`verify_run_index`; load-through-index) |
| Isolation | no case contaminates another | PASS (fresh lab per case; zero residue between cases) |
| Repeatability | same truth-bearing verdicts | PASS (2× per family, identical truth outputs) |
| Cleanup | zero containers/networks | PASS (independent host-side checks) |
| Offline CI | green | PASS (527 tests; ruff; mypy) |
| Live integration | green | PASS (26 tests; canonical host) |
| Models | absent | PASS (AST boundaries; model-free oracle) |
| Gate 6 | not implemented | PASS (no dataset engine) |

## Test totals

Offline: `ruff` clean, `mypy` clean (74 source files), **527 tests passed, 26
deselected**. Live integration: **26 tests passed, 527 deselected, in 194.53s**
on the canonical host (macOS/arm64, Docker 29.1.3 / Compose 2.40.3-desktop.1,
pinned `frrouting/frr:v8.4.1@sha256:0f8c174d…`, FRR 8.4.1_git). Zero `vnet-*`
containers and zero `vnet-*` networks remained afterwards.

## Live closure results (canonical host)

The complete live integration tier ran on the canonical host — macOS/arm64,
Docker 29.1.3, Compose 2.40.3-desktop.1, pinned
`frrouting/frr:v8.4.1@sha256:0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e`
(FRR 8.4.1_git):

```
26 integration tests passed, 527 deselected, in 194.53s.
```

- Reverse-orientation (`router_b`) accepted incidents, one per family:
  `ras-rev`, `nr-rev`, `if-rev`, `pf-rev` — all accepted, indexed, and
  reload-verified through a single shared index; peer never mutated; each with a
  byte-identical config recovery verdict where the family requires it.
- Reference (`router_a`) accepted incidents per family, the deterministic
  precondition-rejected incident, and the shared-index test — all green
  (unchanged Gate 4/5.1–5.4 coverage).
- Every pending mutation paired; every ledger terminal at `RECOVERY_VERIFIED`;
  every run's artifacts verified and discoverable through the run index.
- Repeatability: proven offline (identical truth-bearing verdicts across two
  run_ids per family); live runs are integrity-consistent, not byte-identical
  across runs (real timestamps), by design.
- Cleanup: independent host-side checks confirmed zero `vnet-*` containers and
  zero `vnet-*` networks after the run.

## Limitations

Single reference platform (macOS/arm64). One lab backend (FRR) and one topology
(two-router eBGP). Four fault families with a bounded 9-case catalog — a
verified library, not a dataset. Config recovery is proven byte-identical for
every family (FRR canonical serialization); whole-directory digests are NOT
byte-identical across live runs (real timestamps), by design. No model, agent,
dynamic scenario selection, dataset generation, or evaluation exists.

## Gate 6 entry conditions

Gate 6 (verified dataset engine and leakage-safe splits) may begin only when:
Gate 5 is tagged and released; the scenario catalog and run index are the
authoritative source of accepted/rejected runs; and the dataset engine consumes
ONLY already-verified run artifacts (never re-deriving truth, never invoking a
model to label). Gate 6 has NOT begun.

## References

- `fault-family-plan.md`, `neighbor-removal-family.md`,
  `interface-shutdown-and-prefix-withdrawal.md`, `scenario-parameterization.md`,
  `cross-family-regression.md`
- ADR-0009/0010 (model-free ground truth), ADR-0013 (composition root),
  ADR-0016 (artifact directory), ADR-0017 (composition root + run index)
