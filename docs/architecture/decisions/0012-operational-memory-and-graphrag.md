# 0012 — Operational memory and GraphRAG

**Status:** Accepted — long-term architecture (implementation at Gates 10–11; memory Gate 15)
**Date:** 2026-07-12

## Context

VerifiedNet will accumulate operational knowledge of several very different kinds. Treating
them as one undifferentiated "context blob" would make retrieval untraceable and would risk
stale or unverified knowledge influencing an outcome.

## Decision

Keep the knowledge stores **separate and typed**: topology knowledge, healthy baselines,
prior verified incidents, runbooks, RFC/vendor documentation, configuration history, and
successful/unsuccessful remediations. Retrieval is layered:

- **Vector RAG** (Gate 10) for semantic retrieval over documents and prior incidents.
- **GraphRAG** (Gate 11) for relationships among topology, dependencies, incidents,
  evidence types, symptoms, root causes, and remediation actions.

Two hard rules: (1) every retrieved fact carries **provenance** (its source, kind, and
whether it is authoritative knowledge, an extracted claim, an inferred relationship, an
incident-specific observation, or model-generated text); (2) retrieved knowledge is
**re-verified against current live evidence** before it can influence an outcome — history
informs the investigation but does not substitute for present-state verification.

## Consequences

- GraphRAG is **future work, not a present capability**; nothing in Gates 0–4 implements a
  knowledge graph, and it must not be described as if it does.
- LLM-extracted graph edges are never auto-trusted; they enter as provenance-tagged claims
  subject to verification.
- Because facts are re-verified live, a stale runbook or an outdated baseline cannot silently
  drive a wrong conclusion.

## References

- ADR-0010 (models are not ground truth)
- `../final-platform-vision.md` (Layer 5)
- Gate 10 (vector RAG), Gate 11 (GraphRAG)
