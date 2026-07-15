# Gate 13 — Persisted Evaluation Corpus and Structured-Output Reliability

**Status:** IMPLEMENTED (Gate 13). This gate strengthens the MEASUREMENT
FOUNDATION that Gate 12 exposed as weak: the evaluation corpus was a
transient fixture with zero eligible test examples, and neither real
predictor produced strictly parseable JSON. Gate 13 fixes the foundation
without touching a single measurement rule — Gate 7 scoring, the Gate 8
predictor interface and parser, Gate 9 ranking, and Gate 12 comparison
semantics are all byte-unchanged (contract-tested). **No training, no prompt
change, no prompt optimization, no ranking change happens here.** It
implements ADR-0030.

## 1. The persisted evaluation corpus (`evaluation-corpora/<evalcorpus-…>/`)

A Gate 6 prepared corpus (Gate 6 stays the only source of truth) is
REGISTERED as an immutable, versioned project artifact:

```
evaluation-corpora/<evalcorpus-…>/
    manifest.json    (version, provenance, generation policy, coverage,
                      quality_verified — Literal[True] — files, ecdig-… digest)
    coverage.json    (deterministic coverage report)
    quality.json     (fail-closed structural quality verdict)
```

Identity is pure content: `evalcorpus-…` derives from (corpus version,
prepared digest, generation-policy id, provenance); the digest covers the
manifest facts plus the report file hashes. `CorpusProvenance` is recorded
explicitly (`fixture_generated` vs `project_persisted`), and the frozen
`EvaluationCorpusGenerationPolicy` (`ecgen-…`) Literal-locks the source to
verified run artifacts — no other origin is representable. Registration
fail-closes on a policy/corpus mismatch and NEVER overwrites; an
`audit_evaluation_corpus` pass recomputes coverage and quality against the
actual prepared corpus, and `list_evaluation_corpus_versions` yields only
verified registrations in deterministic order.

## 2. Coverage statistics and quality verification

`compute_corpus_coverage` reports exact counts before any ratio: totals,
partition counts, ELIGIBLE TEST EXAMPLES (the number Gate 12 needed and did
not have), fault-family / scenario / rejection-code / topology distributions,
per-partition class balance, duplicate-feature-content groups, and class /
topology imbalance ratios. `verify_corpus_quality` fail-closes on structural
defects — duplicate example ids, split leakage (a group in two partitions),
malformed examples (kind/label/partition inconsistencies), missing baseline
or onset evidence, non-uniform feature/label policies — while imbalance is
REPORTED, never silently rebalanced: a manifest for an unverified corpus is
unrepresentable (`quality_verified: Literal[True]`).

## 3. Structured-output reliability (measured, never repaired)

Gate 12's two real failure shapes are now named, deterministic categories:
`classify_invalid_output` maps each invalid prediction's unchanged Gate 8
reason code + bounded raw excerpt to an `InvalidOutputCategory`
(backend_failure; empty_output / degenerate_repetition / truncated_json /
prose_wrapped_json / malformed_other for non-JSON output; non_object_json /
missing_required_field / out_of_schema_value / unsupported_prediction_type
for JSON that violates the response schema). The base model's output is
`prose_wrapped_json`; the one-step-trained checkpoint's is
`degenerate_repetition`. `validate_response_schema` is a strict
diagnostics-only schema checker; the AUTHORITATIVE parser remains the shared
Gate 8 `parse_backend_response`, untouched.

`compute_parser_statistics` derives, per evaluation run, raw counts and
self-consistent rates (validators refuse any stored rate that disagrees with
its counts): JSON validity, malformed-output rate, valid structured
prediction rate, and the PROMPT-COMPLIANCE rate — compliance is measured
against the unchanged Gate 8 contract; nothing here optimizes a prompt.

## 4. The benchmark-level structured-output report

`build_structured_output_report(benchmark_result)` produces one row per
benchmarked predictor — accuracy, abstention, invalid count, and the full
parser statistics — persisted as a SEPARATE immutable artifact
(`structured-output-reports/<sor-…>/{manifest.json, report.json}`,
`sordig-…` digest) keyed to the benchmark id. This extends Gate 9's
REPORTING without touching its stores, files, digests, or ranking: old
benchmark artifacts still verify byte-for-byte, and reliability rates are
reported, never ranked on. The paired view required by Gate 13 is the same
report filtered to the matched base and trained identifiers; the Gate 12
comparison artifact is unchanged.

## 5. The project evaluation corpus v1

The gated integration path registers the first PROJECT-PERSISTED corpus:
all nine catalog scenario cases × 2 deterministic runs (18 accepted across
the four fault families) + 4 rejected runs, built by the same verified Gate 6
chain, persisted outside Git under the project artifact root with the full
chain (runs → dataset → prepared → registration). Its coverage report proves
eligible test examples exist — the prerequisite Gate 12's interpretation
policy demands before any directional claim can ever be made.

## 6. Proof obligations discharged by tests

Coverage exactness and determinism; quality fail-closure for duplicate ids,
split leakage, malformed examples, and missing evidence (deliberately
doctored corpora); registration refusal on quality failure, policy mismatch,
and overwrite; per-byte tamper evidence across every stored file; audit
detection of a swapped prepared corpus; classifier totality/determinism and
category-partition completeness over a reason×excerpt grid; the two REAL
Gate 12 excerpts classifying correctly; statistics sum/rate invariants
enforced by validators; report-id order independence and sensitivity;
build-twice byte-identical registrations and reports; source immutability
and no-network across registration and reporting; Gate 13 artifacts rejected
as training inputs; and the unchanged-Gate-8-parser contract test.

**Update (Gate 14):** the registration store gained backward-compatible
descendant-version support — an optional `expansion` binding (parent id +
digest, policy/plan/campaign ids, Literal-locked target satisfaction) and an
optional `expansion.json` file; every v1 artifact verifies unchanged. Corpus
v2 was registered through it (ADR-0031). See
`../gate14/corpus-expansion.md`.

## 7. Explicitly out of scope

No larger-scale fine-tuning, no evaluation-engine change, no ranking change,
no prompt change or optimization, no retrieval, no agents. The corpus and
reliability layers exist so that the NEXT measurement (and only then, the
next training decision) can be grounded in adequate, verified data.

## Gate 15 note

Structured-output reliability reports remain DIAGNOSTICS in the Gate 15
controlled experiment: invalid-output counts feed the frozen success policy
as raw counts (an increase blocks `improved`), but rates are never ranked on
and the parser/prompt stay byte-unchanged (ADR-0033; see
`../gate15/controlled-experiment.md`).
