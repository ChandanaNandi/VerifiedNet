# 0011 — SLM role and the verification boundary

**Status:** Accepted — long-term architecture (implementation at Gates 8–9)
**Date:** 2026-07-12

## Context

The networking SLM (Layer 4) is the platform's headline capability, which makes it the
most likely place for the trust boundary to erode. Its permitted role and its hard limits
must be fixed before any training or inference code exists.

## Decision

The SLM runs behind a `ModelAdapter` and is confined to advisory roles. It **may**:
classify incidents, propose hypotheses, choose which evidence to request, suggest the next
investigation step, generate a structured diagnosis proposal, and explain already-verified
findings. It **may not**: create ground truth, silently alter evidence, bypass
deterministic verification, execute mutations directly, or approve its own remediation.
Every SLM claim passes through the verification layer and is resolved to `accepted`,
`rejected`, `insufficient`, or `abstained`.

Training data discipline keeps five things strictly distinct: (1) **training labels**,
derived only from fault metadata and deterministic verification; (2) **evidence** supplied
to the model; (3) **model predictions**; (4) **verifier outcomes**; (5) **feedback
eligible for later training**. Only deterministically-verified facts (1) enter later
training data — a model prediction (3) is never promoted to a label. This forecloses
self-training loops where unverified output becomes ground truth.

## Consequences

- The SLM improves the *investigation*, not the *truth*; a wrong hypothesis is caught by
  verification and recorded as rejected/insufficient, not as a fact.
- Abstention is a first-class outcome: the model may decline, and that is recorded.
- The training pipeline (see `../final-platform-vision.md`) is auditable end-to-end: every
  label traces to a deterministic verdict.

## References

- ADR-0010 (models are not ground truth)
- `../final-platform-vision.md` (SLM creation pipeline; Layer 4)
- Gate 8–9 (base benchmark, fine-tuning)
