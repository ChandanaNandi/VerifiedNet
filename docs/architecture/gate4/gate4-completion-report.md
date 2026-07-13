# Gate 4 — Completion Report

**Status:** Implementation complete; offline gate green on the development host;
live closure run recorded on the canonical host (section below). Gate 4 delivers
the first *live, verified* networking incidents: a two-router FRR eBGP lab, one
accepted remote-AS-mismatch incident, one deliberately-rejected precondition
incident, real deterministic evidence, restoration and cleanup, canonical
manifests and per-run artifacts, a run index, and a thin composition root that
executes both paths end to end.

The non-negotiable held throughout: **no AI component determines ground truth.**
No model, SLM, RAG, GraphRAG, memory, agent, or persistent workflow participated
in any step. Ground truth is assembled only from injected-fault metadata and
deterministic verifier verdicts (ADR-0009, ADR-0010).

## Scope delivered, by step

| Step | Deliverable | Document |
|---|---|---|
| 1 | Live-execution requirements + backend contract (SYS_ADMIN, API config, pinned interfaces) | ADR-0015 |
| 2 | Healthy live two-router FRR lab; convergence; read-only evidence; live fixtures | `healthy-live-lab.md` |
| 3 | One accepted live BGP remote-AS-mismatch incident (inject → onset → restore → recovery → GroundTruth) | `accepted-live-incident.md` |
| 4 | One deliberately-rejected live incident on a healthy lab (precondition FAIL, zero mutation) | `rejected-live-incident.md` |
| 5 | Canonical per-run artifact directory: durability, integrity verification, replay | `canonical-run-artifacts.md`, ADR-0016 |
| 6 | Run index + thin composition root (assemble, index, load-through-index; two live entry points) | `run-index-and-composition-root.md`, ADR-0017 |

## Acceptance matrix

Each Gate 4 requirement, and where it is proven. "Offline" = deterministic tests
on any host (no Docker); "Live" = the canonical host with the pinned image.

| # | Requirement | Evidence | Mode |
|---|---|---|---|
| 1 | Two-router eBGP lab boots from the pinned immutable image and converges | `test_frr_configured_lab`, `test_frr_healthy_evidence`; convergence helper | Live |
| 2 | Read-only evidence is collected without mutation; live fixtures recorded | `test_frr_live_fixtures`; collectors | Live |
| 3 | Accepted incident: wrong-AS injected on `router_a` only; `router_b` never mutated | `test_accepted_live_remote_as_incident`; runtime `TargetPolicy` | Live |
| 4 | Onset verified deterministically (session down, peer route withdrawn) | onset verdicts committable; accepted test | Live |
| 5 | Restoration returns the lab to a baseline-equivalent healthy state | restoration completed + forced reset; recovery verdicts | Live |
| 6 | Ground truth assembled ONLY from fault metadata + verifier verdicts | `build_ground_truth`; ADR-0009/0010; `oracle_version` pinned | Offline + Live |
| 7 | Rejected incident: rejection during precondition, BEFORE any mutation | `test_precondition_rejected_incident`; ledger stays `PENDING` | Live |
| 8 | Rejected path performs ZERO mutation and leaves the lab healthy | zero mutation transcript; post-run health re-checked | Offline + Live |
| 9 | FAIL (impossible route absent) is distinct from INSUFFICIENT (missing evidence) | rejected verdict `observed == ("false",)`; verifier | Offline + Live |
| 10 | Every run persists to a self-contained, integrity-verified directory | `write_run_artifacts` + `verify_run_dir`; Step 5 tests | Offline + Live |
| 11 | Tampering any truth-bearing file makes verification FAIL | Step 5 tamper suite; index tamper tests | Offline |
| 12 | A run is replayable offline with no Docker/network/exec | `load_run` with `subprocess` sabotaged | Offline |
| 13 | Completed runs are catalogued in a deterministic, verifiable index | `test_artifacts_index.*`; `verify_run_index` | Offline |
| 14 | A hidden/unindexed run directory is detected, not silently ignored | `test_unindexed_run_directory_is_reported` | Offline |
| 15 | A run loads back THROUGH the index with digest re-checked | `load_verified_run_from_index`; wiring + live tests | Offline + Live |
| 16 | Both live paths run through ONE shared composition root | `run_accepted_incident` / `run_precondition_rejected_incident` | Offline + Live |
| 17 | Accepted + rejected runs share one integrity-verifiable index | `test_frr_shared_run_index`; offline wiring | Offline + Live |
| 18 | Teardown leaves zero containers and zero networks | independent host-side `project_containers`/`project_networks` == [] | Live |
| 19 | The composition root is the top: nothing below imports it | `test_no_lower_package_imports_orchestrator`; AST guard | Offline |
| 20 | No model output enters the truth chain anywhere | AST boundaries; ground-truth assembly; ADR-0009/0010 | Offline |

## Offline gate (development host)

`ruff` clean; `mypy` clean (69 source files); **440 offline tests passed, 22 live
tests skipped** (no Docker present). The offline composition-wiring test drives
the real live entry points through a self-contained FRR simulator, so the
composition logic itself is exercised without Docker; the live tier re-proves the
same code against real FRR.

## Live closure run (canonical host)

The closure run executed the full live integration tier on the canonical host —
macOS/arm64, Docker 29.1.3, Compose 2.40.3-desktop.1, pinned
`frrouting/frr:v8.4.1@sha256:0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e`
(FRR 8.4.1_git) — against the Step 6 tree on baseline `820a069`:

```
22 integration tests passed, 440 deselected, in 59.29s.
```

The three composition-root live tests all passed against real FRR:
`test_accepted_live_remote_as_incident` (accepted → indexed → reload-verified),
`test_precondition_rejected_incident` (rejected, zero mutation, `PENDING` ledger),
and `test_frr_shared_run_index` (one accepted + one rejected in ONE index; each
reload-verified through the index; distinct run digests; index refuses to verify
after a single persisted run is tampered). Independent host-side checks after the
run confirmed zero `vnet-*` containers and zero `vnet-*` networks remained.

## What Gate 4 deliberately did NOT add

No general orchestrator or agent harness, no natural-language planning, no dynamic
fault selection, no CLI, no dashboard, no database, no scheduler, no DAG or
workflow engine, no event bus, no parallel runs, no autonomous retries, no
remediation approval, no additional fault families or topologies, and no Gate 5
dataset generation. No model, SLM, RAG, GraphRAG, memory, or persistent workflow.
These belong to later gates (see `../../roadmap/future-gates.md`); planned ≠ done.

## References

- `healthy-live-lab.md`, `accepted-live-incident.md`, `rejected-live-incident.md`,
  `canonical-run-artifacts.md`, `run-index-and-composition-root.md`
- ADR-0009, ADR-0010 (ground truth is model-free), ADR-0013 (orchestrator
  boundary), ADR-0015 (live execution), ADR-0016 (artifact directory), ADR-0017
  (composition root + run index)
