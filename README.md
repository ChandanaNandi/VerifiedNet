# VerifiedNet

An open platform for building, verifying, benchmarking, and evaluating AI systems for
computer networks.

**Status: research program complete (Final Research Closure Gate).** VerifiedNet built a
reproducible, model-free verified-incident and evaluation substrate (a two-router FRR eBGP
lab live across four accepted fault families plus a precondition-rejected incident, an
integrity-verified run index, a read-only dataset engine, a deterministic evaluation and
benchmark framework, and a reproducible training stack) and then ran a sequence of
preregistered, one-variable controlled experiments (Gates 15–20C) to locate and remove the
bottlenecks between a pinned 0.5B SLM and correct held-out fault diagnosis from observable
evidence. Boundary alignment solved the structured-output failure (Gate 17); observable-
evidence representation proved necessary (Gate 18); family imbalance explained most of the
collapse (Gate 19); and insufficient remote-AS coverage was **falsified** as the remaining
explanation (Gate 20). The remaining limitation is consistent with field-to-label binding
and/or model capacity under the fixed constraints. Every experiment, corpus, checkpoint,
and benchmark is content-addressed and reproducible from the repository and stored
artifacts. The full scientific record is in
[`docs/research/final-research-summary.md`](docs/research/final-research-summary.md).

Core thesis: verified, reproducible networking incidents and standardized evaluation for
networking AI. Ground truth comes exclusively from injected-fault metadata and
deterministic verifiers — never from a model.

## Layout

- `src/verifiednet/schemas` — versioned, DB-free data contracts (Pydantic v2, strict)
- `src/verifiednet/common` — canonical JSON, hashing, ids/RunContext, logging, errors
- `src/verifiednet/runtime` — bounded argv-only execution, policies, transcripts
- `src/verifiednet/labs` — LabBackend interface + live two-router FRR Compose backend
- `src/verifiednet/collectors` — read-only FRR evidence collectors (fake-runner tested)
- `src/verifiednet/verifiers` — pure claim verification and deterministic checks
- `src/verifiednet/faults` — fault lifecycle, phase-guarded ledger, BGP ASN-mismatch spec
- `src/verifiednet/incidents` — ground-truth oracle, IncidentRecord builder, manifests
- `src/verifiednet/artifacts` — canonical per-run artifact directory, integrity verifier, run index
- `src/verifiednet/orchestrator` — thin composition root: assemble, index, four fault-family bindings, bounded scenario catalog
- `src/verifiednet/datasets` — read-only, model-free projection of verified runs into a leakage-safe, split, prepared corpus (Gate 6)
- `src/verifiednet/evaluation` — deterministic evaluation-task contract, rule/base/checkpoint predictors on one feature-only boundary, benchmark, paired comparison, registered corpora (Gates 7–9, 11–14)
- `src/verifiednet/training` — reproducible, content-addressed training corpus / spec / plan / execution / checkpoint stack + bounded real fine-tune, and the deterministic source-selection policies (Gate 10, 16A/17A/18A/19A)
- `src/verifiednet/experiment` — preregistered one-run controlled-experiment layer (spec, firewall, success policy, result) and the append-only remote-AS coverage campaign (Gates 15–20C)

## Development

```
uv sync
uv run ruff check src tests
uv run mypy
uv run pytest
```

The offline suite runs anywhere (fake runners, fixtures, no Docker/FRR/services); the live
integration tier auto-skips without a Docker daemon and runs against the pinned FRR image
on the canonical host (`uv run pytest -m integration`).

## Documentation

The full engineering record — architecture, decisions, research, provenance, and
roadmap — lives in [`docs/`](docs/README.md). Start with `docs/README.md` for the map,
[`docs/research/final-research-summary.md`](docs/research/final-research-summary.md) for
the terminal scientific record (problem, hypotheses, experiments, results, conclusions,
reproducibility), and `docs/architecture/decisions/` for the load-bearing choices and
their rationale. The gate-by-gate history is in `docs/architecture/gate*/`.

License: Apache-2.0 (see LICENSE, NOTICE). Provenance for adapted symbols:
`docs/provenance/wave_a_provenance.md`.
