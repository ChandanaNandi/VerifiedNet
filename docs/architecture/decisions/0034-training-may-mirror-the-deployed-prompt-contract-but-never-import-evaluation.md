# 0034 — Training may mirror the deployed public prompt contract, but production training code never imports evaluation; byte equality is enforced across layers by contract tests

**Status:** Accepted (owner decision, Gate 16A)
**Date:** 2026-07-15

## Context

Gate 15's `unchanged` result isolated a conditioning mismatch: the model was
fine-tuned to continue a training-input text that differs byte-wise from the
deployed Gate 8 prompt, and the supervised target was provably already valid
under the frozen parser. Aligning the training input with the deployed
prompt creates a tension between two standing rules: the training layer must
be able to STATE the deployed contract exactly, yet ADR-0022 forbids it from
importing the evaluation layer (evaluation and benchmark artifacts are never
training sources, and a shared prompt implementation would silently couple
the two layers).

## Decision

1. **Mirroring is permitted; importing is not.** The training layer may
   restate the deployed prompt's PUBLIC text (instruction and
   response-schema sentences — the class space and format contract, never
   example-level data) as its own constants. Production training code still
   never imports `verifiednet.evaluation`; the AST boundary guard is
   unchanged.

2. **The mirror is locked, not free text.** A contract-aligned template
   version Literal-locks its text to the mirrored constants inside the model
   validator — a drifted or injected variant is unrepresentable, and the
   builder exposes no text parameters.

3. **Byte equality is proven across layers in tests.** Contract and property
   tests — living in the tests tree, where importing both layers is legal —
   assert the mirrored rendering is byte-identical to the deployed prompt
   rendering for the feature space. Any change to either side breaks CI
   loudly; silent divergence is impossible in both directions.

4. **Everything downstream stays frozen.** The target serialization,
   objective, eligibility Literals, parser, prompt, scoring, benchmark,
   comparison, and success policy are unchanged and now carry pinned
   identity literals in the contract tier.

## Consequences

- Serialization alignment becomes a legitimate, isolated experimental
  variable (Gate 16B) without weakening evaluation isolation.
- The deployed prompt remains the single source of truth; the mirror can
  only follow it, and only through a reviewed commit that keeps the
  equality tests green.
- Future prompt versions require a matching mirrored template version and a
  new preregistered experiment — never an in-place edit.

## References

- `docs/architecture/gate16/contract-aligned-serialization.md`
- ADR-0022 (evaluation is not a training source), ADR-0033 (preregistered
  one-run experiments)
