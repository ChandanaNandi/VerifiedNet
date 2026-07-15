# Gate 16B — Preregistered Contract-Aligned Conditioning Experiment

**Status:** IMPLEMENTED (Gate 16B). The second preregistered, one-run, matched
base-versus-trained experiment on registered evaluation corpus v3 — Gate 15's
experiment with **exactly one variable changed**: the training input is
rendered with the Gate 16A contract-aligned v2 template
(`traintmpl-c0513ab53036ae9b`), byte-identical to the deployed Gate 8 prompt,
instead of Gate 15's v1 template. It reuses the ADR-0033 experiment machinery
and the ADR-0034 serialization contract verbatim; **no new durable rule and
no new experiment format** are introduced. **No prompt, parser, scoring,
ranking, comparison, target, objective, or success-policy change; no warm
start, resume, second run, second checkpoint, larger budget, LoRA, RAG,
agents, deployment, or publication.**

## 1. The isolated variable

Gate 15 ended `unchanged` (base 0/230 valid structured outputs, trained
0/230), and the Gate 16 design review proved the supervised target was
already valid under the frozen parser — the mismatch was the training-input
CONDITIONING. Gate 16B tests that single variable:

```
Gate 15:  TrainingInputTemplate v1   (trainpolicy-47cd597b27119125)
Gate 16B: TrainingInputTemplate v2   (trainpolicy-336332a846b0f791)
```

The v2 corpus is content-addressed differently (its input bytes differ), so
the training-corpus id, spec, plan, slice, execution, checkpoint, and the
experiment id all ripple — but the ripple is driven ONLY by the input
serialization. Every other identity is held: target `traintgt-286e4ecdff06833e`,
objective `objpol-e5f36da1a1292f3d`, prompt `prompt-93808d932655a347`, the
pinned Qwen2.5-0.5B snapshot @ `7ae5576…`, the exact Gate 15 envelope (64
examples / 64 steps / 2 epochs / batch 1 × accum 2 / lr 2e-5 / seeds 15 /
384-64-448), the frozen success policy `esucc-ab21b8d6e2ab7a70`, and the
whole Gate 7/9/12/13 measurement stack.

## 2. Same-64-source proof

The v2 corpus derives from the SAME v3 prepared chain through the unchanged
Gate 10A builder and the SAME first-64 canonical cap. The experiment proves —
offline on the fixture chain and operationally on the real v3 chain — that the
capped v1 and v2 corpora select **exactly the same ordered
`source_example_id` sequence**, with byte-identical targets and trace
bindings; only the rendered input (and the content-derived ids it produces)
differs. Held-out validation/test/abstention examples never enter training.

## 3. No production code change

Gate 16B required NO production change. The v2 binding is fully expressible
through the existing Gate 15 `ControlledTrainingExperimentSpec`: the spec
records `training_corpus_policy_id` (= the v2 policy) and the v2 corpus
id/digest, and the input-template identity is bound transitively through the
corpus manifest. A v2-bound spec therefore gets a distinct `experiment_id`
from an otherwise-identical v1-bound spec while every frozen control stays
byte-equal — proven by property tests over every control field. The entire
gate is offline tests + one gated operational test + documentation.

## 4. Freshness, one run, one checkpoint

The treatment checkpoint is trained FRESH from the pinned pretrained base
snapshot — never a warm start from the Gate 15 checkpoint (the executor has
no checkpoint/parent/resume parameter, and a real checkpoint lineage
Literal-forbids a parent). Exactly one execution and one checkpoint are
produced (Literal ceilings; retry/resume unsupported); a failed run
preserves its verified failed execution and finalizes as `experiment_failed`.
The Gate 10F.1 and Gate 15 checkpoints and the base-model artifact are
byte-fingerprinted before and after and asserted unchanged.

## 5. Measurement and the frozen success policy

Base and treatment predictors share one inference stack and are evaluated
alongside the fixed-prior and evidence-rule baselines on registered corpus
v3 through the unchanged Gate 7 engine and Gate 9 benchmark (descriptive
ranking). Gate 13 reliability is measured for both model predictors —
the report explicitly records the treatment's valid-structured-output count
against the descriptive references (base 0/230 and Gate 15 treatment 0/230),
but that cross-experiment comparison never enters the frozen success policy.
The outcome is derived by the unchanged `esucc-ab21b8d6e2ab7a70` policy:
`improved` still requires strictly higher accepted test accuracy AND net
paired wins AND no invalid increase AND no abstention regression. A validity
gain WITHOUT an accuracy gain is `mixed`, never `improved` — task improvement
is never claimed from validity alone.

## 6. Operational result

The gated operational test (`VERIFIEDNET_RUN_GATE16B=1`) runs the full
15-step flow on the real artifacts and finalizes the persisted
controlled-experiment result. This document records the authoritative outcome
after the operational run; the report additionally answers the standing
question *"did valid structured output increase above 0/230?"* directly from
the persisted reliability artifact, and labels any Gate 15-versus-Gate 16B
comparison as descriptive only.

## 7. Proof obligations discharged by tests

Spec construction binds the v2 policy and is one-run/one-checkpoint locked;
experiment-id stability and sensitivity to every frozen control; the
independent variable is the only intended id driver; same-64-source ordering
(fixture + real chain) with identical targets and diverging inputs; no
held-out partition enters the v2 corpus; the executor has no warm-start
channel and a lineage forbids a parent checkpoint; a second execution/
checkpoint is unrepresentable; wrong budget / prompt / normalization yield a
different experiment; a v1 substitution is visible not silent; the frozen
result validator refuses a dishonest `improved`; the v2 corpus is
firewall-clean, model-free, network-free, and leaves the source prepared
corpus byte-identical; and the gated operational experiment with prior-
artifact immutability fingerprints.
