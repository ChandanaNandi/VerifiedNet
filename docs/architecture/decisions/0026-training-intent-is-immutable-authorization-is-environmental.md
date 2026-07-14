# 0026 — Training intent is immutable; execution authorization is environmental evidence

**Status:** Accepted (owner decision, Gate 10E)
**Date:** 2026-07-14

## Context

ADR-0023 made every weight-affecting input explicit and content-addressed;
ADR-0024/0025 proved execution and checkpoint lifecycles in simulation. The
first real ML backend now enters the architecture — and with it a category of
facts the platform has never had to hold: what one specific MACHINE can do
today. Package versions drift, devices differ, deterministic modes come and
go. If those facts leak into content-addressed training identities, plans stop
being portable and reproducibility disputes become environment archaeology.
If they are ignored, training starts on machines that cannot honestly execute
the plan. Both failure modes are prevented by one rule.

## Decision

1. **Immutable intent and runtime evidence are different artifact classes,
   and evidence never mutates intent.** The Gate 10B plan (corpus binding,
   model/tokenizer specs, hyperparameters, precision, seeds, budget, trainer
   implementation identity) stays byte-identical through preflight. What a
   machine can do lives in a separate `TrainingEnvironmentSnapshot` and a
   separate `TrainingExecutionAuthorization`. The same plan on two machines
   legitimately yields two authorization ids; a runtime observation can never
   retroactively change a Gate 10B identity.

2. **Execution requires authorization, and authorization requires staged,
   structured preflight.** Twelve ordered stages (plan verification → corpus
   verification → backend contract → packages → device → model resolution →
   tokenizer resolution → precision → memory estimate → determinism →
   checkpoint compatibility → authorization), each producing immutable
   findings; skipped stages are reported as errors, never hidden; and
   `authorized=True` with any ERROR finding is unrepresentable (parse-time)
   and re-checked by the store verifier (never trusting the stored boolean).
   The backend protocol has no `train` method — a later gate consumes a
   VERIFIED authorization, so execution without preflight is structurally
   unreachable.

3. **Every mutable external input must resolve to an immutable identity
   before authorization.** Model and tokenizer artifacts resolve separately
   (tokenizer identity is never assumed from model identity) to pinned
   revisions plus content hashes binding local bytes; a name alone is not
   proof; mutable aliases are rejected at every layer; unresolved artifacts
   refuse authorization. Offline preflight performs no network access —
   remote download is a separate, explicitly authorized operation that does
   not exist yet.

4. **Capability discovery is honest and narrowly scoped.** Package versions
   are compared with a standards-compliant PEP 440 parser (never
   lexicographically) via `importlib.metadata` (never importing the package);
   the v1 system probe is deliberately CPU-only because honest CUDA probing
   requires torch itself; determinism is assessed into explicit categories,
   best-effort requires the caller's explicit acknowledgement, and nothing
   downgrades silently. Memory estimation is a conservative deterministic
   approximation over an EXPLICIT parameter count — never inferred from a
   model name, never compared against unstable free-memory readings.

5. **Environment evidence is secret-free by construction.** Snapshots and
   authorizations are `extra="forbid"` models whose schemas contain no
   username, hostname, home directory, environment variables, process id, or
   wall-clock time — such fields are unrepresentable, not merely omitted; the
   proof suite injects fake credentials into the process environment and
   scans every persisted byte.

6. **The initial real-backend scope is exactly one modeled mode.**
   HF Transformers + PyTorch, single process, single device, FULL
   fine-tuning. LoRA/QLoRA are not claimed because the Gate 10B spec models
   no adapter hyperparameters — an adapter mode would be an unmodeled
   promise. Heavy ML libraries are not dependencies of the core package in
   this gate; their absence is a structured preflight finding, never an
   import-time crash, and the AST import boundary stays fully intact.

## Consequences

- Gate 10F (first bounded real training execution) starts from a verified
  authorization: the machine is proven capable, every input is immutable, and
  the determinism claim is honest — the run adds only execution.
- Plans remain portable forever; authorizations are cheap, per-machine, and
  disposable.
- The v1 probe's honesty has a cost: on a real machine it reports CPU-only
  and undeclared total memory, so real authorization requires the
  torch-backed probe that arrives with Gate 10F. Refusing until then is the
  correct behavior, not a limitation to paper over.

## References

- `../gate10/execution-preflight.md`
- ADR-0023 (runs are planned before executed), ADR-0024 (execution is
  event-sourced and simulated first), ADR-0025 (checkpoints are verified
  artifacts with bound lineage)
