# 0010 — Models are not ground truth (platform-wide)

**Status:** Accepted — long-term architecture invariant (extends ADR-0009 to all layers)
**Date:** 2026-07-12

## Context

ADR-0009 established that the ground-truth oracle uses only deterministic evidence for the
Gate 3/4 incident. As the platform grows to include an SLM (Layer 4), operational memory
and RAG/GraphRAG (Layer 5), an orchestrator and agents (Layer 6), and persistent workflows
(Layer 7), there is a standing temptation to let a confident model output "count" as a
fact. That would silently dissolve the project's central contribution.

## Decision

The deterministic trust core is immutable and platform-wide:

```
Labs → Fault lifecycle → Evidence collection → Deterministic verification
→ Ground-truth oracle → Incident records → Recovery verification → Reproducibility artifacts
```

Across **every** current and future layer: models, agents, RAG, memory, and orchestrators
may propose, retrieve, rank, and explain, but may never (a) create ground truth, (b)
silently alter evidence, (c) bypass deterministic verification, (d) execute mutations
directly, or (e) approve their own remediation. Every model claim is resolved by the
verification layer to exactly one of `accepted`, `rejected`, `insufficient`, or
`abstained`.

## Consequences

- Adding intelligence never widens the trust boundary; it only feeds proposals into it.
- The invariant is testable at package level (the incident/oracle layer cannot import
  model or execution code) and will be enforced by the AST security guard as those layers
  are added.
- Any design that would let unverified model output become a recorded fact is rejected at
  review, regardless of accuracy claims.

## References

- ADR-0009 (ground truth excludes model output)
- `../final-platform-vision.md` (trust core, invariants)
