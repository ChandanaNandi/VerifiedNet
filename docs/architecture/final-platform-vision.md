# VerifiedNet — Final Platform Vision

**Status:** Long-term architecture (coordination checkpoint). Layers 2–8 are **planned,
not implemented.** Only Layer 1 exists today (Gates 0–3 offline; Gate 4 will make it live).

This document adds a **destination** to the roadmap. It does not rebuild what Gates 0–3
already built, and it does not change any Gate 3 contract or Gate 4 scope. Every future
layer is subordinate to the deterministic trust core below.

## The immutable trust core

The following pipeline is the source of all truth in VerifiedNet and never changes:

```
Labs
→ Fault lifecycle
→ Evidence collection
→ Deterministic verification
→ Ground-truth oracle
→ Incident records
→ Recovery verification
→ Reproducibility artifacts
```

**Invariant.** Models, agents, RAG, memory, and orchestrators may *propose, retrieve,
rank, and explain* — they may **never** create ground truth, alter evidence, bypass
deterministic verification, execute mutations directly, or approve their own remediation.
Every model claim is `accepted`, `rejected`, `insufficient`, or `abstained` by the
verification layer (see ADR-0009, ADR-0010, ADR-0011).

## The layered architecture

Each layer wraps the one below without loosening its guarantees.

### Layer 1 — Verified systems foundation *(Gates 0–5)*
Live lab backends; secure argv-only execution with read-only/mutation separation;
reversible faults; evidence collectors; deterministic verifiers; accepted/rejected
incident records; recovery proof; manifests, transcripts, and cleanup reports. Built
offline in Gates 0–3; first live incident in Gate 4; more fault families and backends in
Gate 5. **This is the trust core.**

### Layer 2 — Verified data engine *(Gate 6)*
Repeated scenario generation across multiple fault families and parameterized topology
variations; incident-corpus generation; data validation; leakage-safe splitting;
provenance and dataset manifests. Turns verified incidents into a dataset — every row
grounded in Layer 1 verdicts.

### Layer 3 — Evaluation framework *(Gates 7 and 12)*
Deterministic rule baselines and base-model evaluation infrastructure (Gate 7);
diagnosis accuracy, evidence grounding, hallucination measurement, abstention, confidence
calibration, robustness, and latency/resource measurement (Gate 12). Metrics come only
from reproducible runs; no number is invented.

### Layer 4 — Specialized networking SLM *(Gates 8–9)*
Base-model benchmark (Gate 8) then a fine-tuned networking SLM (Gate 9). The SLM **may**
classify incidents, propose hypotheses, choose which evidence to request, suggest the next
investigation step, generate a structured diagnosis proposal, and explain verified
findings. The SLM **may not** create ground truth, silently alter evidence, bypass
deterministic verification, execute mutations directly, or approve its own remediation.
It runs behind a `ModelAdapter`; every claim it makes passes through the verification
layer (ADR-0011).

### Layer 5 — Operational knowledge and memory *(Gates 10–11; memory in Gate 15)*
Distinct stores, never conflated: topology knowledge, healthy baselines, prior verified
incidents, runbooks, RFC/vendor documentation, configuration history, and successful/
unsuccessful remediations. Vector RAG for semantic retrieval (Gate 10); GraphRAG for
relationships among topology, dependencies, incidents, evidence, and remediations
(Gate 11). Every retrieved fact carries provenance and is **re-verified against current
live evidence** before it can influence an outcome. GraphRAG is future work, not a present
capability (ADR-0012).

### Layer 6 — Intelligent orchestrator and agent harness *(Gate 13)*
The orchestrator selects collectors, verifiers, knowledge sources, models, specialist
agents, and workflow paths. Possible specialist roles: triage, interface, BGP, routing,
ACL, logs/counters, knowledge retrieval, diagnosis critic, safety verifier. Agents
**propose and explain**; deterministic tools remain the authority for facts, safety, and
recovery. No agent receives unrestricted mutation access (ADR-0013).

### Layer 7 — Persistent workflows *(Gate 15)*
Event-driven or scheduled workflows: continuous health checks, configuration-drift
detection, post-deployment verification, recurring incident investigation, dataset-
generation campaigns, regression monitoring, verified alerting. Every persistent workflow
uses bounded execution, explicit ownership, durable state, and deduplication (ADR-0014).

### Layer 8 — Outcome engine *(Gates 14–15)*
Explicit, machine-readable outcomes rather than chatbot responses: `accepted_diagnosis`,
`rejected_insufficient_evidence`, `rejected_contradictory_evidence`, `abstained`,
`escalation_required`, `restoration_completed`, `recovery_verified`, `restoration_failed`,
`remediation_blocked`, `rollback_completed`. Safe remediation, approval binding, and
rollback land in Gate 14; the full outcome engine and persistent-workflow surface in
Gate 15. The platform optimizes for **completed, verified operational outcomes**.

## How the SLM is created

```
Verified live incidents
→ canonical IncidentRecords
→ dataset validation
→ leakage-safe splits
→ rule and base-model baselines
→ SLM fine-tuning
→ evaluation
→ deployment behind ModelAdapter
→ hypothesis / tool-selection role
→ deterministic verification
→ accepted / rejected / abstained outcome
→ new verified feedback
```

Five things are kept strictly distinct so that unverified model output can never become
ground truth:

1. **Training labels** — derived from fault-injection metadata and deterministic
   verification only.
2. **Evidence supplied to the model** — the read-only inputs it reasons over.
3. **Model predictions** — hypotheses, classifications, proposed next steps.
4. **Verifier outcomes** — the deterministic accepted/rejected/insufficient/abstained
   verdict on each prediction.
5. **Feedback eligible for later training** — *only* predictions whose associated facts
   were independently verified by Layer 1.

**No self-training loop.** A model prediction is never promoted to a training label. Only
deterministically-verified facts (labels 1) enter later training data (ADR-0011).

## Layer → gate mapping

| Layer | Capability | Gate(s) | Status |
|---|---|---|---|
| 1 | Verified systems foundation | 0–3 (offline), 4 (live), 5 (more families/backends) | 0–3 done; 4 next |
| 2 | Verified data engine | 6 | planned |
| 3 | Evaluation framework | 7 (baselines/infra), 12 (model-quality metrics) | planned |
| 4 | Specialized networking SLM | 8 (base benchmark), 9 (fine-tuning) | planned |
| 5 | Operational knowledge & memory | 10 (vector RAG), 11 (GraphRAG), 15 (memory) | planned |
| 6 | Orchestrator & agent harness | 13 | planned |
| 7 | Persistent workflows | 15 | planned |
| 8 | Outcome engine | 14 (remediation/rollback), 15 (outcome engine) | planned |

See `../roadmap/future-gates.md` for the full gate list and dependency order.

## Trust-boundary invariants (must hold in every future gate)

1. No layer bypasses the ground-truth oracle.
2. No agent or model receives unrestricted mutation access.
3. No model output becomes a training label without deterministic verification.
4. GraphRAG (and RAG, SLM, agents, memory) are described as future work until implemented
   and tested; planned ≠ done.
5. Persistent workflows have explicit ownership and durable, bounded state.
6. The package dependency graph remains acyclic; the orchestrator is the only composition
   root and no lower package imports it.
7. Retrieved knowledge is re-verified against current live evidence before it influences an
   outcome.
8. Ground truth is assembled only from fault metadata and deterministic verifier verdicts.
