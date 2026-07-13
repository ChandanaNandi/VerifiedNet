# VerifiedNet

An open platform for building, verifying, benchmarking, and evaluating AI systems for
computer networks.

**Status: Gate 5 complete — a verified fault-family library.** A two-router FRR eBGP lab is
executed live across four accepted fault families (BGP remote-AS mismatch, neighbor
removal, interface administrative shutdown, BGP prefix-advertisement withdrawal) plus a
deterministic precondition-rejected incident. A small, explicit scenario catalog adds a
bounded parameter matrix with reverse-orientation (router_b) proof; runs are isolated,
repeatable, and catalogued in an integrity-verified run index. Every run has real
deterministic evidence, byte-identical restoration, and zero-residue cleanup. No AI
capability exists yet, and no model participates in producing ground truth. Live claims are
recorded only from reproducible runs on the canonical host.

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
`docs/roadmap/future-gates.md` for direction, and `docs/architecture/decisions/` for the
load-bearing choices and their rationale.

License: Apache-2.0 (see LICENSE, NOTICE). Provenance for adapted symbols:
`docs/provenance/wave_a_provenance.md`.
