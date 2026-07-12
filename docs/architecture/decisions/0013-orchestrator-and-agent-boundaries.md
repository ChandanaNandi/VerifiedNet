# 0013 — Orchestrator and agent boundaries

**Status:** Accepted — long-term architecture (implementation at Gate 13; boundary begins Gate 4)
**Date:** 2026-07-12

## Context

Later gates introduce an intelligent orchestrator and specialist agents. Without explicit
boundaries, an orchestrator can accrete authority and an agent can acquire mutation access,
quietly turning the platform into an unverified autonomous system. The Gate 4 plan already
makes `verifiednet.orchestrator` the composition root; this ADR generalizes that boundary
to the full agent harness.

## Decision

The orchestrator is the **composition root** and the only package permitted to import
across the stack (`schemas`, `common`, `runtime`, `labs`, `collectors`, `verifiers`,
`faults`, `incidents`, `artifacts`). No lower-level package may import the orchestrator;
`cli` may import the orchestrator; the orchestrator must not import `cli`. These rules are
enforced by the AST security guard.

The orchestrator may **select** collectors, verifiers, knowledge sources, models,
specialist agents, and workflow paths. Specialist roles may include triage, interface,
BGP, routing, ACL, logs/counters, knowledge retrieval, diagnosis critic, and safety
verifier. Agents **propose and explain only**. Deterministic tools remain the sole
authority for facts, safety, and recovery. **No agent receives unrestricted mutation
access**; mutations flow exclusively through the fault/remediation layer's guarded
executor with policy, transcript, and (for remediation) human approval binding.

## Consequences

- The dependency graph stays acyclic with a single, obvious top.
- Agent proliferation cannot expand the mutation surface; the number of "voices" grows but
  the number of hands does not.
- A "diagnosis critic" and a "safety verifier" are agents that argue; the deterministic
  verifier is the judge.

## References

- ADR-0001 (package boundaries), ADR-0010 (models are not ground truth)
- `../final-platform-vision.md` (Layer 6)
- Gate 4 plan (orchestrator composition-root boundary), Gate 13
