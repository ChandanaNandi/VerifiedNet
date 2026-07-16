# 0035 — Training objectives align the generation boundary to deployed inference

**Status:** Accepted

## Context

Gate 15 and Gate 16B were preregistered one-run experiments (ADR-0033). Both
decreased training loss yet produced 0/230 valid structured predictions and
classified `unchanged`. Gate 16A (ADR-0034) had already made the training input
*bytes* identical to the deployed Gate 8 prompt, so the residual failure could
not be input-content. Gate 16B's treatment failure mode was `empty_output ×230`.

A read-only diagnostic on the existing Gate 16B checkpoint
(`realckpt-e9d2664ebf4177727ce20966`) isolated the cause. The training objective
serialized supervised sequences as `input + "\n" + target + EOS` and masked the
input *and the separator* from the loss, so the model was trained to emit the
target only after a trailing newline token (`198`). Deployment feeds the raw
prompt with no trailing newline. On that raw prefix the trained model placed
≈0.93 probability on EOS (`<|im_end|>`, `151645`) and terminated immediately
(decoded `""`, reproducing the persisted `eval-c5a63abb095e270f` exactly);
appending the single `"\n"` moved ≈0.9999 probability onto the opening `{"`
token and produced the correct JSON. All other factors were aligned: the
training and inference tokenizers encoded the prompt to identical ids, no BOS
was added, and the training and inference EOS were both `151645`.

The general lesson is boundary alignment: a supervised objective that inserts a
token between the input and the target — even one masked from the loss —
conditions the first generated token on a context the deployed system never
provides, creating a train/inference generation-boundary mismatch that can fully
suppress output while training loss still falls.

## Decision

Training objectives that supervise a response for a model whose deployment feeds
a raw prompt MUST condition the first supervised target token on the **exact
byte/token prefix that deployment provides**. A separator (or any token)
inserted between the input and the target — masked or not — is prohibited unless
that same token is part of the deployed inference prefix, because a masked
separator still changes the generation boundary the model learns.

Concretely, the boundary-aligned causal-LM objective
(`objpol-7e6428964eae2db8`) serializes `input + target + EOS` and masks the
input span only. It is additive: the separator-bearing objective
(`objpol-e5f36da1a1292f3d`) remains valid and byte-compatible for every prior
artifact. The two are the only representable configurations of the frozen
`TrainingObjectivePolicy` — a cross-field invariant forbids a separator with
input-only masking and forbids input-and-separator masking without the `"\n"`
separator — and the public builder injects no separator, masking mode, or
objective text. Because input and target are tokenized independently, removing
the separator retokenizes neither side; the pre-target training prefix equals
the raw inference prompt ids exactly.

## Consequences

- Objective changes that alter the generation boundary are identity-bearing and
  must be justified against the deployed inference prefix, not chosen for
  training convenience.
- A gated real-tokenizer proof verifies prefix equality against the pinned
  tokenizer; offline tests prove the sequence/label arithmetic without a
  tokenizer.
- This ADR governs the objective contract only. It authorizes no training run,
  no experiment, and no deployment change; binding the boundary-aligned
  objective in a preregistered experiment (Gate 17B) remains subject to
  ADR-0033.
- Appending the separator to the deployed prompt instead is explicitly rejected:
  it would mutate the Gate 8/11 contract and break base-eval byte-identity.

## References

- `architecture/gate17/boundary-aligned-objective.md` (Gate 17A design and the
  diagnostic evidence).
- `architecture/gate16/contract-aligned-conditioning-experiment.md` (Gate 16B
  null result and `empty_output` follow-up).
- ADR-0033 (preregistered one-run experiments), ADR-0034 (contract-aligned
  serialization).
