# VerifiedNet

An open platform for building, verifying, benchmarking, and evaluating AI systems for
computer networks.

**Status: Gate 3 — architecture and offline behavior only.** No live network has been
executed. Parser fixtures are source-derived until Gate 4 re-records them against a live
FRR lab. No AI capability exists yet. No performance or correctness claim about live FRR
has been established.

Core thesis: verified, reproducible networking incidents and standardized evaluation for
networking AI. Ground truth comes exclusively from injected-fault metadata and
deterministic verifiers — never from a model.

## Layout

- `src/verifiednet/schemas` — versioned, DB-free data contracts (Pydantic v2, strict)
- `src/verifiednet/common` — canonical JSON, hashing, ids/RunContext, logging, errors
- `src/verifiednet/runtime` — bounded argv-only execution, policies, transcripts
- `src/verifiednet/labs` — LabBackend interface + FRR topology rendering (not executed)
- `src/verifiednet/collectors` — read-only FRR evidence collectors (fake-runner tested)
- `src/verifiednet/verifiers` — pure claim verification and deterministic checks
- `src/verifiednet/faults` — fault lifecycle, phase-guarded ledger, BGP ASN-mismatch spec
- `src/verifiednet/incidents` — ground-truth oracle, IncidentRecord builder, manifests

## Development

```
uv sync
uv run ruff check src tests
uv run mypy
uv run pytest
```

All Gate 3 tests run offline: fake runners, recorded fixtures, no Docker/FRR/services.

License: Apache-2.0 (see LICENSE, NOTICE). Provenance for adapted symbols:
`docs/provenance/wave_a_provenance.md`.
