# Gate 17B — Boundary-Aligned Objective Experiment

Gate 17B is the preregistered, one-run controlled experiment that binds the
Gate 17A boundary-aligned objective (`objpol-7e6428964eae2db8`). It is Gate 16B
with **exactly one variable changed** — the training objective — and it
confirms the Gate 17 diagnostic at the experiment level.

## Design (one independent variable)

Everything is held byte-for-byte identical to Gate 16B except the objective:
the same registered evaluation corpus v3 (`evalcorpus-8c932345efc3e6e6`,
`ecdig-e72927cc7d4b6fd0fa141462`), the same ordered 64 training sources, the
same v2 contract-aligned input (`traintmpl-c0513ab53036ae9b`), the same target
(`traintgt-286e4ecdff06833e`), the same pinned model
(`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae5576…`), tokenizer, budget
(64 examples / 2 epochs / 64 optimizer steps / 448 seq / effective batch 2),
prompt (`prompt-93808d932655a347`), parser, scoring, benchmark, and the frozen
success policy `esucc-ab21b8d6e2ab7a70`. The **only** changed bound field is
`objective_policy_id`: the Gate 10F separator-bearing objective
(`objpol-e5f36da1a1292f3d`) is replaced by the boundary-aligned objective
(`objpol-7e6428964eae2db8`), which assembles `input + target + EOS` and masks
the input span only. Switching it yields a distinct `experiment_id` while every
other control stays byte-equal — proven offline in the unit/property/failure
tiers. One run, one checkpoint, fresh from the pinned base (no warm start;
lineage forbids a parent).

## Operational result (exp-2d7024f609a37a2c)

Fresh boundary-aligned fine-tune, one run / one checkpoint
(`realckpt-f1f86f9d70fc1db7172e93a2`, digest `realdig-a399f4fecc610209c1eeba8f`,
parent `None`), loss `3.532090 → 0.070484` over 64 steps / 2 epochs. Outcome
**`mixed`**.

- **Structured-output validity: `0/230 → 230/230`.** The base model is
  `0/230` valid (all `prose_wrapped_json` ×230, byte-consistent with Gate 15
  and Gate 16B). The boundary-aligned treatment is **`230/230` valid with zero
  invalid predictions** — a complete reversal of Gate 16B's `empty_output ×230`.
  Removing the masked separator so the first target token is supervised under
  the exact raw deployed prompt eliminated the immediate-EOS collapse, exactly
  as the Gate 17 diagnostic predicted (`P(EOS)≈0.93 → P("{")≈0.9999`).
- **Accepted-diagnosis accuracy did NOT improve.** On the 36 eligible test
  examples the treatment produced valid JSON for all 36 (`trained_invalid=0`)
  but `0` correct (`both_incorrect=36`, `base_incorrect_trained_correct=0`);
  the base was invalid on all 36. Valid structured output does not imply the
  correct fault family — the model reliably emits parseable JSON but not the
  right class.
- **Therefore the frozen policy classifies the outcome `mixed`**, never
  `improved`: a validity gain without an accepted-diagnosis accuracy gain. The
  success policy requires higher accepted-test accuracy for `improved`; that did
  not occur, and the result is reported honestly.

The Gate 15 / Gate 16B / Gate 10F.1 checkpoints and the base model are
fingerprinted byte-identical before and after; the test-set firewall passed
before any held-out truth was consulted; the run is strictly offline.

## Interpretation

Gate 17B closes the structured-output-reliability question that Gates 15 and 16B
left open: the binding constraint on validity was the train/inference
generation-boundary mismatch, not input content, corpus, budget, or capacity.
The boundary-aligned objective makes the deployed model emit well-formed,
parser-valid structured output every time. What remains is a *content/accuracy*
problem — the model does not yet choose the correct fault family — which is a
distinct axis (decoding, objective shaping beyond the boundary, data, or
capacity) for a future gate. No accuracy claim is made here.

## Scope

No prompt, parser, scoring, ranking, comparison, reliability-classification,
target, template, model, tokenizer, corpus, decoding, or success-policy change;
the objective is the sole independent variable. No warm start, second run,
larger budget, LoRA, RAG, agents, deployment, or publication. See
`architecture/gate17/boundary-aligned-objective.md` (Gate 17A objective and the
diagnostic) and ADR-0035.

**Follow-up (Gate 18).** The 0/36 accuracy was diagnosed as a representation
ceiling: the v1 model-visible features are label-ambiguous (6 payloads, all
family-ambiguous across 206 accepted examples). Gate 18A adds an additive
discriminative feature policy v2 that exposes observable evidence with zero
cross-family collisions on the real chain. Gate 18B then bound that
representation in a preregistered one-run experiment (changing only v1 → v2 from
this gate) and moved the outcome from `mixed` to `improved` on a `3/36` held-out
accepted-test gain — the first in the series — though the model still collapses
toward a dominant family. This gate's `mixed` outcome is unchanged. See
`architecture/gate18/discriminative-evidence-features.md`,
`architecture/gate18/discriminative-evidence-experiment.md`, and ADR-0036.
