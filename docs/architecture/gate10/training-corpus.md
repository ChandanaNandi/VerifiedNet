# Gate 10A — Training Readiness and the Supervised Training Corpus

**Status:** IMPLEMENTED (Gate 10A). This document describes `verifiednet.training`
— the deterministic, immutable supervised training-corpus layer derived from the
Gate 6 prepared corpus. It implements ADR-0022. **No model training occurs in
Gate 10A**: no torch/transformers/PEFT, no optimizers, no checkpoints, no GPU or
model execution — the AST boundary guard enforces this. The output is training
data infrastructure, not a trained model.

## 1. Why the boundary exists

The flow now forks after the prepared corpus:

```
Prepared Corpus
  ├── Evaluation and Benchmarking   (Gates 7–9)
  └── Training-Corpus Projection    (this gate)
          ↓
     Future Training Runner         (Gate 10B+)
```

Before any trainer exists, there must be an authoritative contract for which
examples may legally become training data and exactly how they become
input/target pairs. Without it, a pipeline can silently train on test data or
leak evaluator-only truth into model input — corrupting every future
measurement.

## 2. Eligibility: train-only, accepted-only

`TrainingDataPolicy` is frozen, versioned, and content-addressed
(`trainpolicy-…`). Its eligibility fields are **Literal-locked**: source
partition `train`, example kind `accepted_fault`, label kind `accepted_fault`,
abstention excluded. A policy admitting validation, test, or abstention examples
cannot be constructed in this gate. `TrainingTraceMetadata.partition` is likewise
`Literal["train"]`, so a training example bound to any other partition is
unrepresentable. The builder additionally FILTERS to train-partition accepted
examples and fails closed if a train example lacks accepted labels.

## 3. The three layers of a training example

**Model input** (`SupervisedTrainingInput`) — rendered by the explicit,
content-addressed `TrainingInputTemplate` (`traintmpl-…`) from model-visible
`DatasetFeatures` ONLY: backend, topology hash, baseline/onset evidence
presence, plus the public candidate-family class list and the required output
schema. It never contains identity (`example_id`/`group_id`/run id), digests,
partition/split, policy ids, rejection facts, evaluator outcomes, or reserved
`dataset_*` fields — allowlist construction, never dump-and-delete. The training
template is deliberately INDEPENDENT of the Gate 8 inference prompt: training
may not import `verifiednet.evaluation`, so the two templates have distinct
explicit identities rather than a silently shared implementation.

**Supervised target** (`SupervisedTrainingTarget`) — canonical JSON
(`{"fault_family": …, "prediction_type": "diagnosis"}`, sorted keys, no
whitespace) rendered by the content-addressed `TrainingTargetTemplate`
(`traintgt-…`) directly from the authoritative `AcceptedLabels.fault_family`.
Equivalent labels serialize byte-identically. No correctness, confidence,
reasoning, outcome category, ranking, recovery data, identity, or digests.

**Audit metadata** (`TrainingTraceMetadata`) — source example/group ids, the
governing task/policy/template/feature/label-policy ids, partition confirmation,
and schema versions. Never enters input or target text. Corpus-level provenance
digests live in the manifest, not per example (see §7).

## 4. Identities

Every identity is a validated content hash:

- `training_data_policy_id = "trainpolicy-" + sha256_canonical({schema/policy
  versions, allowed partition/kinds, task_id, template ids, include_abstention})[:16]`
- `input_template_id = "traintmpl-" + sha256_canonical({versions, name,
  instructions, sorted candidates, task_id, feature_policy_id})[:16]`
- `target_template_id = "traintgt-" + sha256_canonical({versions, task_id,
  output_schema})[:16]`
- `training_example_id = "trainex-" + sha256_canonical({source_example_id,
  task_id, policy id, template ids, rendered_input, rendered_target})[:24]` —
  the source example id participates in the hash (trace binding) but never
  appears in input/target text. The example model re-derives this id, so a
  tampered input, target, or binding fails at parse time.
- `training_corpus_id = "traincorpus-" + sha256_canonical({task_id, policy id,
  template ids, ordered training_example_ids})[:16]` — deliberately EXCLUDES the
  prepared digest so the corpus identity is invariant under evaluation-side
  changes (§7).
- `training_corpus_digest = "traindig-" + sha256_canonical({versions, corpus id,
  config ids, provenance pins, count, ordered example ids, generator,
  path-sorted file hashes})[:24]` — non-recursive, self-validated by the
  manifest.

## 5. Pure builder and training leakage audit

`build_training_corpus(prepared, *, training_data_policy, input_template,
target_template)` is pure: no filesystem, network, subprocess, model execution,
randomness, or timestamps. It verifies policy/template/task/feature-policy
coherence, selects eligible examples, renders explicitly, audits every rendered
payload, sorts by source example id (input-order independent), and fails closed
on duplicates or any mismatch.

`audit_training_example` inspects the ACTUAL serialized payloads: forbidden
key-like tokens anywhere in the input text; the example's own evaluator-only
values (source ids, policy/template ids) copied verbatim into the input; and a
target that must be a JSON object with exactly the authorized keys
(`prediction_type`, `fault_family`) and a diagnosis type. Fail-closed on ERROR.
**Limitation (stated):** structural and exact-value checks do not prove absence
of arbitrary semantic leakage; that is bounded by the feature allowlist and the
templates. The candidate class list in the input is public class space, not the
answer for a specific example.

## 6. Immutable layout, writer, verifier, loaders

```
training-corpora/<training_corpus_id>/
    manifest.json     inputs.jsonl     targets.jsonl     metadata.jsonl
```

Line *i* of each file is the same example, preserving the layer boundary on
disk. The writer is atomic under `.INCOMPLETE`, verifies before finalizing, and
refuses to overwrite. The verifier re-derives every training-example id from the
stored layers (the content-hash binding is the independently auditable proof —
full re-rendering from features requires the source prepared corpus, which the
manifest pins by digest), re-runs the leakage audit on every example, re-checks
policy-id consistency, counts, file hashes, and the corpus digest — fail-closed.

**Trainer-facing API:** `load_training_pairs` verifies then returns ONLY
`TrainingPair(input_text, target_text)` — no identity, trace, digests, or
evaluation data (the model literally has no such fields). **Audit-facing API:**
`load_training_corpus` returns all three layers plus the manifest.

## 7. Partition isolation (the critical guarantee)

Changing ONLY validation/test/abstention examples leaves the training corpus
unchanged: same `training_corpus_id`, same training-example ids, byte-identical
`inputs.jsonl`/`targets.jsonl`/`metadata.jsonl`. The manifest's provenance pins
(prepared/dataset digests, and the corpus digest over them) necessarily track
the changed source — that is the only permitted difference. Proven by test:
two prepared corpora with identical train examples but a different abstention
example produce identical training content.

## 8. Guarantees proven by test

Train-only selection with validation/test/abstention exclusion; Literal-locked
policy and trace models; self-validating identity binding (tampered input →
parse failure); deliberate-leakage rejection (injected source id caught by the
audit even under `model_construct` bypass); build-twice reproducibility
(byte-identical directories); input-order independence; source immutability
(runs, dataset export, prepared corpus, and evaluation artifacts byte-identical
before/after the full pipeline); no execution and no network (subprocess,
process runner, and `urllib` sabotaged); trainer loader exposes only pairs; and
the training package imports no evaluation or model-training modules
(AST-enforced).

## 9. Limitations and Gate 10B entry

The v1 train partition is tiny, so this corpus proves the MACHINERY —
eligibility, separation, identity, isolation — not a useful training set.
Gate 10B (implemented — see `training-plan.md`, ADR-0023) adds the reproducible
training specification and trainer abstraction, still with no fine-tuning
execution: planning binds this corpus by id and digest through a descriptor
(count and identity only — never example text); a future execution gate will
consume the examples themselves exclusively through `load_training_pairs`.
Checkpoints and actual fine-tuning come later, behind their own gates.
