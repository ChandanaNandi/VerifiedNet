# VerifiedNet — Roadmap (Future Gates)

The platform is built in strict gates. Gates 0–3 are complete (offline architecture and
contracts, verified with 238 tests). What follows is planned and **not yet implemented** —
listed here so the trajectory is explicit. No capability below should be assumed to exist
until its gate ships and is tested.

## Immediate next

**Gate 4 — First end-to-end vertical slice (live).** Stand up the minimal two-router FRR
eBGP lab and run one real incident: healthy → verify interfaces/peer/BGP Established →
inject remote-AS mismatch on `router_a` → verify onset (wrong ASN AND not-Established) →
collect evidence → restore → verify recovery (Established AND routes both ways) → write one
accepted `IncidentRecord` and one deliberately rejected record, plus run/environment
manifests. Prerequisites: pin `frrouting/frr:v8.4.1` by multi-arch digest; implement the
live `LabBackend` (compose up/health/exec/down, per-run project naming, no
`container_name`); a docker-exec `ReadOnlyExecutor` adapter; re-record the FRR JSON
fixtures against the live lab; an orchestrator wiring collectors to `evidence_provider`.

## Then, in order

- **Gate 5** — More parameter variations and additional fault families (SONiC DB
  collectors, ASIC/ACL validation, Batfish verification enter here).
- **Gate 6** — Dataset grouping and leakage-safe splits (canonical incidents → dataset).
- **Gate 7** — Rule-based baseline benchmark.
- **Gate 8** — Base SLM benchmark (first model track; adapters land here).
- **Gate 9** — Vector RAG.
- **Gate 10** — GraphRAG (provenance-tagged knowledge graph; no auto-trusted LLM edges).
- **Gate 11** — Fine-tuned networking SLM (LoRA/QLoRA / continued pretraining).
- **Gate 12** — Fine-tuned SLM + RAG + GraphRAG combinations.
- **Gate 13** — Blackboard multi-agent comparison.
- **Gate 14** — Hallucination, grounding, safety, robustness, calibration, and temporal-
  reasoning evaluation.
- **Gate 15** — Dashboard, public benchmark reports, reproducibility release.

## Standing rules across all future gates

- No capability (RAG, GraphRAG, SLM training, agents) is claimed until implemented and
  tested. Planned ≠ done.
- No performance number is invented; every metric comes from a reproducible run.
- The SLM is **one model track inside the platform**, compared against deterministic rules,
  base model, vector RAG, GraphRAG, fine-tuning, and their combinations — never presented
  as the whole platform.
- Ground truth stays model-free (see `architecture/decisions/0009`).

## Two SLM tracks (future)

- **Track A** — a small decoder-only Transformer built from scratch, for education and
  architectural understanding; not presented as the operational model unless evaluation
  proves it.
- **Track B** — a practical open-weight SLM adapted via continued pretraining / SFT /
  LoRA-QLoRA, benchmarked against every baseline above.
