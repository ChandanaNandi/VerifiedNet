# Gate 16A — Contract-Aligned Training Serialization

**Status:** IMPLEMENTED (Gate 16A). The additive serialization contract that
makes the supervised training INPUT byte-identical to the frozen Gate 8
deployed prompt — and nothing else. **No experiment, preregistration, plan,
authorization, execution, checkpoint, evaluation, benchmark, or
interpretation exists in this gate; Gate 16B is deliberately unstarted.**

## 1. Gate 15's actual finding

Gate 15 ended `unchanged`: training loss fell 2.2788 → 0.1173 while both the
base model and the trained checkpoint produced 0/230 valid structured
predictions. The Gate 16 design review located the mismatch precisely — and
exonerated the target: the Gate 10A/15 supervised target
(`{"fault_family":X,"prediction_type":"diagnosis"}`) **already round-trips
through the frozen Gate 8 parser** as a valid `DiagnosisPrediction` (now
contract-tested for every family). What differed was the CONDITIONING: the
v1 training input carries a different instruction sentence and a different
output-schema sentence than the deployed prompt, so at inference the model
continues a prefix it never saw in training. Conditioning — not target JSON —
is therefore the single independent variable Gate 16B will test.

## 2. v1 versus v2 serialization

`TrainingInputTemplate.template_version` widens additively to
``Literal[1, 2]``. **v1 is byte-frozen**: its rendering, its identity
derivation, and every persisted Gate 10A/15 artifact are unchanged (pinned in
contract tests: v1 template `traintmpl-d9ace87210088ece`, v1 policy
`trainpolicy-47cd597b27119125`, target `traintgt-286e4ecdff06833e`, plus a
literal byte-pin of a full v1 rendering). **v2 renders the deployed Gate 8
prompt exactly** — same instruction sentence (including the abstention
language), same sorted candidate class space, same observation block, and
the prompt's response-schema sentence in place of v1's ``Output:`` line.
The v2 text is Literal-LOCKED by the model validator to the mirrored
contract constants: a v2 template carrying any other instructions, name, or
class space is unrepresentable — `contract_aligned_input_template()` exposes
no text parameters at all.

## 3. The mirrored-text boundary (ADR-0034)

The training package still may not import `verifiednet.evaluation`
(ADR-0022, AST-enforced): v2's text is a MIRROR of the prompt's two public
sentences, restated as constants in the training layer, never shared code.
The cross-layer byte-equality proof lives in `tests/` — where importing both
layers is legal — as a contract test asserting
``v2.render(features) == PromptTemplate.render(features)`` across feature
combinations, plus a Hypothesis property over the generated feature space.
If the frozen prompt ever changes, the mirror diverges and CI fails loudly;
nothing can drift silently in either direction.

## 4. Target and objective invariance

The target template is untouched (v1, same id, same bytes; the
contract-aligned policy REFUSES any other target version), no ``confidence``
key was added, the objective policy `objpol-e5f36da1a1292f3d` is unchanged
(separator ``"\n"``, `mask_input_and_separator`, ignore-index −100,
single-trailing-EOS), and eligibility is byte-identical Gate 10A Literals:
train-partition, accepted-fault, accepted-diagnosis only, abstention
structurally excluded. Only the input-template identity changed, so the
training-policy id changes (`contract_aligned_training_policy`) while every
downstream measurement contract stays pinned (prompt
`prompt-93808d932655a347`, scoring v1, interpretation
`interp-6a0d81d82b2b8d16`, success policy `esucc-ab21b8d6e2ab7a70` — all
contract-pinned literals now).

## 5. Same-source proof

Building the v1 corpus and the v2 corpus from the SAME prepared chain and
applying the same first-64 canonical cap selects **exactly the same ordered
`source_example_id` sequence** — compared id-by-id, not by counts — with
byte-identical targets and trace bindings; the only intended per-example
difference is the rendered input (and consequently the content-derived
example/corpus ids). Proven offline on the deterministic v3-shaped fixture
chain and re-proven on the REAL v3 prepared chain in the gated integration
test. The source prepared corpus is byte-fingerprint-unchanged by every
corpus build.

## 6. Token-length proof

Offline, a structural bound: the v2 rendering adds a CONSTANT,
feature-independent delta versus v1 (the two mirrored sentences, < 260
characters). Authoritatively, the gated integration test
(`VERIFIEDNET_RUN_GATE16A=1`) tokenizes all 64 REAL selected v2 inputs and
targets with the REAL pinned Qwen tokenizer (tokenizer only — the model is
never loaded) and asserts every example fits the UNCHANGED Gate 15 sequence
policy (384 input / 64 target / 448 total including separator and EOS),
reporting exact maxima. An overlength result fails the gate — nothing is
truncated and the sequence policy is never adjusted here.

## 7. No model execution

Gate 16A performs no training, no inference, no tokenizer loading in any
offline path, and no network access (trap-proven); the training package's
import surface is re-proven free of evaluation, subprocess, and static ML
imports. All Gate 15 artifacts, corpora v1/v2/v3, checkpoints, and prior
evaluations remain byte-identical.

## 8. The exact boundary before Gate 16B

Gate 16A ends with contracts and proofs only. Gate 16B — a NEW preregistered
one-run experiment specification binding the v2 policy, one bounded real
CPU fine-tune, matched evaluation on registered corpus v3 through the
unchanged Gate 7/9/12/13 machinery, and a frozen-policy outcome — requires
its own approval and begins only after the token-length proof has passed on
the authoritative machine.
