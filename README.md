# VerifiedNet

An open platform for building, verifying, benchmarking, and evaluating AI systems for
computer networks.

**Status: Gate 4 complete — first live verified incidents.** A two-router FRR eBGP lab is
executed live: one accepted remote-AS-mismatch incident and one deliberately-rejected
precondition incident, each with real deterministic evidence, restoration and cleanup,
canonical per-run artifacts, a run index, and a thin composition root. No AI capability
exists yet, and no model participates in producing ground truth. Live claims are recorded
only from reproducible runs on the canonical host.

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
- `src/verifiednet/orchestrator` — thin Gate 4 composition root (assemble, index, run both live paths)

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
