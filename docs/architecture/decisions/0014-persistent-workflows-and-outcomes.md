# 0014 — Persistent workflows and the outcome engine

**Status:** Accepted — long-term architecture (implementation at Gates 14–15)
**Date:** 2026-07-12

## Context

The platform's end state runs unattended: continuous health checks, drift detection,
post-deployment verification, recurring investigations, dataset-generation campaigns,
regression monitoring, and verified alerting. Unattended automation is where bounded
execution, ownership, and durable state stop being nice-to-haves and become safety
requirements. Separately, the platform must produce **operational outcomes**, not chat.

## Decision

**Persistent workflows (Layer 7).** Every event-driven or scheduled workflow must have:
bounded execution (explicit deadlines and resource limits — no unbounded loops or
subprocesses), explicit ownership (a named owner and a single writer for its state),
durable state (survives restarts; recorded, not in-memory-only), and deduplication (the
same trigger does not launch overlapping or duplicate runs). These reuse Layer 1's
guarantees — argv-only bounded execution, run manifests, cleanup reports.

**Outcome engine (Layer 8).** The platform emits explicit, machine-readable outcomes rather
than free-form responses: `accepted_diagnosis`, `rejected_insufficient_evidence`,
`rejected_contradictory_evidence`, `abstained`, `escalation_required`,
`restoration_completed`, `recovery_verified`, `restoration_failed`, `remediation_blocked`,
`rollback_completed`. Safe remediation, approval binding, and rollback (Gate 14) produce
the remediation outcomes; the full outcome engine and workflow surface land in Gate 15. The
platform optimizes for **completed, verified operational outcomes**.

## Consequences

- A scheduled campaign that hangs or crashes is bounded and cleaned up, and its partial
  state is durable and owned — not orphaned.
- Every run terminates in one enumerated outcome, so success/failure is queryable and
  auditable, never a matter of interpreting prose.
- Remediation remains human-approval-bound and rollback-capable; the outcome engine records
  which outcome occurred, including the rejected and blocked paths.

## References

- ADR-0002 (bounded execution), ADR-0009/0010 (deterministic truth), ADR-0013 (orchestrator)
- `../final-platform-vision.md` (Layers 7–8)
- Gate 14 (remediation/rollback), Gate 15 (persistent workflows, memory, outcome engine)
