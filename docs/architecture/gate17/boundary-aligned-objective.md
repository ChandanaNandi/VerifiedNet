# Gate 17A — Contract-Aligned Boundary Objective

Gate 17A adds one additive training objective and nothing else. It removes the
masked newline separator from the causal-LM objective so that a supervised
sequence is `input + target + EOS` instead of `input + "\n" + target + EOS`,
with loss masking the input span only. No training runs, no experiment is
preregistered, no plan/authorization/execution/checkpoint/evaluation/benchmark/
comparison/result is created. Inference, the deployed prompt, the parser,
scoring, ranking, comparison, reliability classification, the templates, the
model/tokenizer, the corpus, decoding, and the success policy are all frozen.

## Why: the two null results and the diagnostic

Gate 15 (v1 serialization) and Gate 16B (v2 serialization, byte-identical to
the deployed Gate 8 prompt) were both preregistered one-run experiments. Both
decreased training loss (Gate 16B: `3.193601 → 0.082657`), both produced
**0/230** valid structured predictions, and both classified `unchanged`. The
one datum that changed between them is the treatment failure mode: Gate 15's
trained model produced malformed output; Gate 16B's trained model produced
`empty_output ×230`. Changing the input *content* did not help — the failure
lives at the generation boundary, not in the input.

The Gate 17 read-only diagnostic (existing checkpoint
`realckpt-e9d2664ebf4177727ce20966`, digest `realdig-21e44e5343c71cf34498f5c2`)
established the mechanism directly and reproduced the persisted result exactly:

- On the **raw deployed prompt** (which ends `…"abstention".`, no trailing
  newline), the trained model's first generated token is EOS (`<|im_end|>`,
  id `151645`) with probability ≈ **0.93**; greedy decoding stops immediately;
  the decoded completion is `""`. This reproduces the persisted treatment
  evaluation `eval-c5a63abb095e270f` (`raw_excerpt == ""` ×230) byte-for-byte.
- On the **prompt + "\n"** (the exact masked training separator, a single
  token, id `198`), the first token becomes `{"` with probability ≈ **0.9999**
  and the model emits valid target-like JSON.
- Everything else is aligned: the training tokenizer
  (`AutoTokenizer.from_pretrained`, `add_special_tokens=False`) and the
  inference tokenizer (`PreTrainedTokenizerFast(tokenizer_file=…)`,
  `add_special_tokens=True`) encode the prompt to the **same 244 ids**; no BOS
  is added (`bos_token=None`, `add_bos_token=False`); the training EOS
  (`tokenizer.eos_token_id`) and the inference EOS (`config.json
  eos_token_id`) are **both 151645**; the checkpoint stayed byte-identical
  before and after the probe.

So the masked `"\n"` separator is not merely correlated with the failure — its
absence at inference causally drives immediate EOS, and its presence restores
the target.

## Piecewise tokenization: why target retokenization is not involved

The training objective never tokenizes a joined string. The executor encodes
the input, the separator, and the target **independently**
(`tokenizer.encode(text, add_special_tokens=False)`) and concatenates the three
integer sequences plus a trailing EOS. Consequently:

- Removing the separator removes exactly the one newline token (`198`) that sat
  between the input tokens and the first target token.
- The input token tuple is unchanged; the target token tuple is unchanged.
  There is no string-boundary merge, so the first target token is not
  retokenized (it remains the target's own first token — `4913 = {"` for the
  representative pinned target, recorded by the gated proof rather than
  hardcoded as a universal fact).
- The boundary-aligned pre-target prefix therefore equals the **raw deployed
  inference prompt token ids** exactly, while the legacy prefix equals those
  ids followed by `[198]`.

## Old vs new objective

The Gate 10F objective (`objpol-e5f36da1a1292f3d`) is unchanged and remains
valid and byte-compatible for every prior artifact:

- sequence `input + "\n" + target + EOS`; labels mask input **and** separator;
  supervise target and the single trailing EOS.

The Gate 17A objective (`objpol-7e6428964eae2db8`) is additive and distinct:

- sequence `input + target + EOS`; labels mask the **input only**; supervise
  target and the single trailing EOS; `separator = ""`; `chat_template = none`;
  right padding with masked pad labels; mean-over-unmasked loss.

Both are the same frozen, content-addressed `TrainingObjectivePolicy`; only the
`separator` and `label_masking` fields differ, which is sufficient to derive a
distinct id. A cross-field validator makes the two configurations the *only*
representable ones: a hidden separator may not coexist with input-only masking,
and input-and-separator masking may not exist without the `"\n"` separator. The
public builder `boundary_aligned_objective_policy()` takes no arguments, so no
arbitrary separator, masking mode, or objective text can be injected. A derived
`sequence_construction` view (`input_target_eos` vs `input_separator_target_eos`)
drives explicit executor dispatch and is never serialized, so it cannot perturb
the id.

## Frozen inference contract

Gate 17A changes **no** inference behavior. The deployed prompt still ends
without a trailing newline and is still fed raw to the checkpoint backend. The
alignment is achieved entirely on the training side: the objective now
supervises the first target token under the exact prefix that deployment
provides, rather than under a prefix that carries an extra newline the deployed
system never emits. The alternative remedy — appending `"\n"` to the deployed
prompt — would fix the *existing* checkpoint but mutate the Gate 8/11 contract
and break base-eval byte-identity, so it is deliberately not taken.

## No training in Gate 17A; the exact boundary before Gate 17B

Gate 17A ships the objective contract, its pure example builder, explicit
executor dispatch (so a future plan can bind it), documentation, and the full
test tiers. It binds the objective to no experiment and runs no training. Gate
17B — the preregistered one-run experiment that binds `objpol-7e6428964eae2db8`
under the otherwise-frozen Gate 16B controls, with a matched base-vs-trained
evaluation and a base eval that must reproduce `eval-18433773bc0d69d6` — remains
unstarted. The scientific prediction carried into 17B: the boundary-aligned
checkpoint, evaluated on the frozen raw prompt, will emit valid structured
output rather than immediate EOS, moving validity off zero; whether that
becomes `improved` or `mixed` is decided only by the frozen success policy on
accuracy.

## Scope

No prompt, parser, scoring, ranking, comparison, reliability-classification,
target, template, model, tokenizer, corpus, decoding, or success-policy change;
no warm start, second run, larger budget, LoRA, RAG, agents, deployment, or
publication. See the Gate 17 pre-implementation review and diagnostic for the
evidence this design rests on.
