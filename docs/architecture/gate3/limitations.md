# Gate 3 Limitations — read before trusting anything

Stated plainly, per the honesty-first project rules:

1. **No live network was executed.** No Docker container, FRR process, or any
   external service was started. Every test ran against fake runners, fake
   clocks, and fixtures. The FRR compose/config renderers produce text that has
   never booted a router.
2. **Parser fixtures are source-derived.** The FRR JSON fixtures under
   `tests/fixtures/frr/` encode the output shapes the NeuroNOC parsers expected
   (FRR-in-lab, v8.4.x). They are PROVISIONAL until Gate 4 re-records them from
   the live plain-FRR lab. FRR field drift is a known accepted risk.
3. **No performance or correctness claim about live FRR is established.** Polling
   timeouts, convergence behavior, and the `clear bgp` timing note are carried
   from source-repo measurements, not from this codebase.
4. **No AI capability exists yet.** There is no model, no RAG, no GraphRAG, no
   agent, no fine-tuning anywhere in this repository. Anything suggesting
   otherwise is wrong.
5. **The Gate 3 result is architecture and offline behavior only**: contracts,
   policies, state machines, parsers, verifiers, ledger, oracle, builders, and
   their tests (234 passing, all offline).
6. The consolidated AST guard cannot see dynamic imports (`importlib`, string
   `__import__`). Runtime policies are the mitigating second layer.
7. The two-router lab exists as validated configuration data and rendered text
   only; `LabBackend` has no live implementation yet (Gate 4).
8. EnvironmentManifest fields are populated with deterministic fixtures in tests;
   real OS/kernel/digest capture is Gate 4 work.
9. ClosCall's public-license action remains open; closcall-derived behavior was
   reimplemented from specification and is documented in the provenance register.
   Public redistribution of anything claiming closcall *expression* stays blocked
   until that repo publishes a license.
10. The full incident loop (lab → fault → oracle → record on a live system) has
    never run end-to-end; Gate 3 proves the offline wiring only (the lifecycle
    test drives all five FaultScenario methods against scripted fakes).
