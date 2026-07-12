# VerifiedNet — Gate 0: Source Repository Inventory

Status: **audit complete** (this document reflects direct inspection, not README claims)
Date: 2026-07-11
Inspected by: Gate 0 audit (local clones, shallow depth 50)

> **Audit baseline.** The Git commits pinned in §1 are the current audit baseline for all Gate 0
> findings and all downstream gate planning. Any uncommitted or unpublished local modifications in
> the owner's working copies are NOT covered by this inventory and must be separately inventoried
> before any harvesting begins.

> Path note. The user-supplied VerifiedNet path is `/Users/nandichandana/Downloads/VerifiedNet`
> (on the local Mac). Inspection was performed against session-local clones under `/tmp/repos/`
> pulled from `https://github.com/ChandanaNandi/<repo>` at the commits recorded below. Before
> any Gate 3+ code migration, the harvest must be re-verified against the user's canonical local
> checkouts if they differ from these commits.

## 1. Repository snapshot table

| Repo | Commit (HEAD, main) | Last commit date | Author | Primary language | Dependency manager | Python req | CI |
|---|---|---|---|---|---|---|---|
| closcall | `d192bf3cb86d96e6011f80d1d6915862397abab7` | 2026-07-06 | Chandana Nandi | Python 3.12 | uv (`pyproject.toml` + `uv.lock`) | `==3.12.*` | GitHub Actions `ci.yml` |
| neuronoc-network-ops-assistant | `5f2444742afbfd557d24d1e30fedd337f565f432` | 2026-05-31 | Chandana Nandi | Python 3.12 + TS/React | uv (`backend/pyproject.toml` + `backend/uv.lock`); pnpm/npm (`frontend/package.json`) | `>=3.12,<3.13` | GitHub Actions `ci.yml` (backend, frontend, e2e) |
| sonic-troubleshooting-agent | `eb4c8185ec6d5fab77d526f07aa9f9766d8034bb` | 2026-05-31 | Chandana Nandi | Python 3.11+ (stdlib core) | none for core; `fine_tuning/requirements.txt` (torch/transformers/peft/accelerate/datasets/sentencepiece) | unpinned | **none** |
| sonic-intent-agent | `856623e5f7731224b9a84f3a932cd94c683dca09` | 2026-05-21 | Chandana Nandi | Python | **none** (README references a `requirements.txt` that does not exist) | unpinned | **none** |
| evpn-vxlan-frr-lab | `5b5a479bff19b1ae300f97434dbb0bcdc49adbea` | 2026-05-27 | chandana-solix | bash + Python validator | none | unpinned | **none** |
| sonic-acl-validation-harness | `92a33d66d91c3a199831a13f1485d2c0e638fac3` | 2026-05-27 | chandana-solix | Python 3 stdlib | `requirements-dev.txt` (pytest only) | unpinned | **none** |
| constellation | `24d037bc1618294e5a620653c098d65ffb8f2e17` | 2026-05-03 | ChandanaNandi | Python 3.11 + TS/React | `requirements.txt` (root), `backend/pyproject.toml` (`>=3.11`), `data_engine/pyproject.toml` (`>=3.11`), `frontend/package.json` | mixed `>=3.11` | **none** |

## 2. What each repo actually is (verified, one line each)

- **closcall** — research-grade fault detection/localization/remediation platform for a containerlab
  SR Linux 2-spine/4-leaf Clos fabric; ~5.8k LOC mypy-strict `src/`, 195 unit tests, gnmic/Prometheus/
  pgvector-Postgres pinned by digest in `compose.yaml`; generated fabric from `lab/fabric.yaml`
  (single IPAM source of truth). Deep-audit caveat: integrated live loop never assembled; prechecks
  built and tested but not wired to live execution (disclosed in ADR-004).
- **neuronoc-network-ops-assistant** — FastAPI + React NetOps console with deterministic anomaly
  rules, linear LangGraph pipeline (no LLM in graph), optional Ollama RCA, FAISS runbook RAG
  (hash-embedder default), plan-only remediation enforced by AST scans in tests; 4-node FRR 8.4.1
  compose lab with fault injection; 274 test functions against real Postgres.
- **sonic-troubleshooting-agent** — SONiC-VS fault injection (3 scenarios) + evidence collectors
  (CONFIG_DB/APP_DB/COUNTERS_DB/vtysh/syslog) + 4-specialist blackboard fan-out over one Ollama
  model + diagnosis fan-in; Phase-4 LoRA/RCA eval harness is real and runnable (n=6 eval, honest
  0% RCA accuracy reported). No tests, no CI.
- **sonic-intent-agent** — NL → SONiC CONFIG_DB change with Batfish pre-verify, human approval,
  post-apply verify; 7 phase directories that are largely byte-identical copies (verified:
  `sonic_client.py` identical md5 across phases 4–6); real pybatfish usage; module-global
  `proposed_plans` list in `tools.py` (phases 4/5/6); no deps manifest, no CI.
- **evpn-vxlan-frr-lab** — FRR 8.4.1 leaf-spine EVPN/VXLAN lab (type-3/HER only; type-2
  intentionally disabled due to LinuxKit kernel), bash orchestration with blind sleeps, real
  vtysh-JSON Python validator with a probabilistic (≥4/15 ping) reachability floor.
- **sonic-acl-validation-harness** — single-scenario ACL validation CONFIG_DB→APP_DB→ASIC_DB with
  SAI ternary-mask normalization and entry fingerprinting; 35 pure-logic tests (no integration
  tests); `ast.literal_eval` parsing of `sonic-db-cli` stdout; no CI.
- **constellation** — multi-task CV (FCOS/segmentation/depth on EfficientNet-B0) + FastAPI/Postgres
  labeling backend + Gradio demo. **Out of thematic scope for VerifiedNet**; only training/eval
  infrastructure patterns may serve as design reference. Deep-audit caveats: deployment/ and
  data-engine mining are stubs; Celery/Redis declared but unused; serving normalization mismatch;
  `torch.load(weights_only=False)`; hardcoded dev password in `config.py`.

## 3. Duplicated implementations across repositories (verified by grep/md5)

> Package destinations are deliberately NOT decided here — final destinations belong to Gates 1–3.
> The right-hand column records only the *capability need* implied by each duplication.

| Duplicated capability | Occurrences | Evidence | Capability need for VerifiedNet (destination TBD, Gates 1–3) |
|---|---|---|---|
| Ollama HTTP client | 4 repos, ≥7 implementations | `sonic-troubleshooting-agent/agents/*.py` (5 copies, one per agent), `neuronoc.../backend/app/llm/ollama.py`, `closcall/src/closcall/workflow/llm.py`, `sonic-intent-agent/phase*/agent.py` | A single model-provider adapter capability; all duplicate clients become design reference |
| `docker exec` wrapper | 4 repos | `sonic-intent-agent/phase{3,4,5,6}/agent.py`, `sonic-acl-validation-harness/acl/db_checks.py`, `sonic-troubleshooting-agent/collectors/sonic_state.py`, `closcall/scripts/api_smoke.py` | A bounded, allow-listed, timeout-enforced command-execution abstraction is needed (one implementation, shared). Package destination TBD in Gates 1–3 |
| vtysh `... json` parsing | 3 repos, ≥6 files | `evpn-vxlan-frr-lab/validate/checks.py`, `neuronoc.../app/lab/collector.py`, `sonic-troubleshooting-agent/{faults,collectors,blackboard}/*.py` | A single FRR/vtysh output-normalization capability |
| FRR compose lab | 2 repos | `neuronoc.../infra/lab/docker-compose.lab.yml` (4-node routed eBGP) vs `evpn-vxlan-frr-lab/docker-compose.yml` (EVPN/VXLAN leaf-spine) | The two labs may share runtime and collector primitives (container exec, vtysh JSON parsing, convergence polling) but may require **separate backend implementations**: a routed-eBGP lab backend and an EVPN/VXLAN lab backend. Decision deferred to Gates 1–3 |
| SONiC-VS bring-up scripts | 3 repos | `sonic-troubleshooting-agent/scripts/bringup.sh`, `sonic-acl-validation-harness/scripts/bringup.sh`, `sonic-intent-agent` docs/Dockerfile | A single SONiC-VS lab-backend capability |
| Byte-identical phase files (intra-repo) | sonic-intent-agent | `sonic_client.py` md5 `09678da…` identical across phase4/5/6; `verifier.py` identical 5→6; `fixture.py` identical everywhere | Harvest exactly one canonical copy (latest phase6), treat earlier phases as history |
| `Dockerfile.sonic-fixed` | 2 repos | `sonic-intent-agent/Dockerfile.sonic-fixed` and `sonic-troubleshooting-agent/Dockerfile.sonic-fixed`, both `FROM docker-sonic-vs:latest` | A single canonical image recipe, pinned by digest |
| Fault injection scripts | 3 repos | `evpn-vxlan-frr-lab/scripts/fault_*.sh`, `neuronoc.../infra/lab/scripts` (`lab.sh inject …`), `sonic-troubleshooting-agent/faults/*.py` | A common fault-lifecycle contract (the FaultScenario contract defined in the project brief) covering inject/verify-onset/restore/verify-recovery |

## 4. Test/CI reality check (drives how much trust each source earns)

| Repo | Tests found | Nature | CI |
|---|---|---|---|
| closcall | 195 unit tests | real; integration/e2e/security/property tiers empty | yes |
| neuronoc | 274 test functions | real, incl. Postgres savepoint isolation + AST no-exec scans | yes (skips FRR lab + Ollama) |
| sonic-troubleshooting-agent | 0 unit tests | smoke scripts only (verified runnable in prior audit) | no |
| sonic-intent-agent | per-phase test scripts | require live Ollama+SONiC+Batfish; not reproducible from repo (no deps manifest) | no |
| evpn-vxlan-frr-lab | 0 | validator is itself the check; no tests of the validator | no |
| sonic-acl-validation-harness | 35 tests | pure-logic only; zero integration coverage | no |
| constellation | minimal | stubs in key subsystems | no |

## 5. Gate 0 conclusions feeding Gate 1

1. Highest-trust harvest sources (tested + typed): **closcall src/**, **neuronoc backend/app** — these
   set the bar for contract design.
2. Highest-value untested logic (needs tests added on arrival): SONiC collectors and reversible
   faults (sonic-troubleshooting-agent), SAI mask normalization + ACL fingerprinting
   (sonic-acl-validation-harness), Batfish verify pipeline (sonic-intent-agent phase6),
   EVPN validator checks (evpn-vxlan-frr-lab).
3. **constellation contributes design reference only** (training-loop/eval-report shape); no code copy.
4. Every source except closcall/neuronoc lacks CI — nothing may be harvested without new tests in
   VerifiedNet (Non-negotiable #8/Quality requirements).
5. sonic-intent-agent has no dependency manifest at all; its environment must be reconstructed and
   pinned before its behavior can be treated as reproducible.
