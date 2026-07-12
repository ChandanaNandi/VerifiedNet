# VerifiedNet — Gate 0: Environment-Specific Assumptions

Status: **audit complete** (every row below verified by grep/read at the recorded commits; nothing inferred from READMEs alone)
Date: 2026-07-11

> **Audit baseline.** Findings apply to the Gate 0 pinned commits (see source_inventory.md §1).
> Uncommitted or unpublished local modifications must be separately inventoried before harvesting.
> Package destinations named in the source-tree sketch of the project brief are treated as
> *candidates only*; final destinations are decided in Gates 1–3.

These are the concrete couplings that must be broken (or explicitly parameterized) before any code
crosses into VerifiedNet. Each violates at least one VerifiedNet quality rule.

## 1. Hardcoded container names

| Repo | Evidence | Value |
|---|---|---|
| sonic-troubleshooting-agent | `CONTAINER = "sonic-vs-troubleshoot"` — module constant repeated in **5 files** (collectors, faults, blackboard) | fixed name |
| sonic-acl-validation-harness | `acl/config.py`: `container: str = "sonic-vs-acl"` (dataclass default — at least centralized) | fixed name |
| evpn-vxlan-frr-lab | compose `container_name:` spine1/leaf1/leaf2/hostA/hostB; scripts `docker exec leaf1|leaf2` | fixed names |
| neuronoc | compose `container_name:` neuronoc-postgres, neuronoc-lab-edge-1/edge-2/core-1/branch-1; collector shells `docker exec neuronoc-lab-…` | fixed names |
| constellation | compose `container_name:` constellation-postgres/redis/backend | fixed names |

**VerifiedNet rule:** container identity comes from `TopologySpec`/lab metadata, never module constants.

## 2. Fixed IP addressing

| Repo | IP literals found (py/sh/conf) | Notes |
|---|---|---|
| sonic-intent-agent | 166 | scattered through phase dirs, incl. test fixtures |
| neuronoc | 109 | FRR configs + collector expectations |
| evpn-vxlan-frr-lab | 49 | FRR configs, compose subnets, validator expectations |
| closcall | 14 | **but** derived from `lab/fabric.yaml` pools (`10.0.0.0/24` p2p, `10.255.0.0/24` loopbacks, `10.100.0.0/24` mgmt) via a deterministic renderer — this is the model to adopt |
| sonic-troubleshooting-agent | 12 | BGP lab peer addressing |
| sonic-acl-validation-harness | 0 | clean |

**VerifiedNet rule:** adopt closcall's generated-IPAM pattern (`lab/fabric.yaml` → renderer) as the
`TopologySpec` design; all other repos' addressing becomes scenario parameters.

## 3. Docker Desktop / platform assumptions

- `evpn-vxlan-frr-lab`: type-2/learned-unicast EVPN **intentionally disabled** because the Docker
  Desktop **LinuxKit kernel** doesn't enforce VXLAN split-horizon (README lines 13/165/222,
  `scripts/setup_vxlan.sh:40`). Reachability check passes at a probabilistic ≥4/15 ping floor on
  that platform. → Scenario validity is platform-conditional; VerifiedNet manifests must record
  kernel/platform, and onset/recovery verifiers must not inherit the 4/15 floor.
- `neuronoc/SETUP_STATUS.md`: developed on MacBook M4 Pro (arm64), Docker Desktop 28.3.3, notes
  arm64 image gaps and `--platform linux/amd64` (Rosetta) workarounds.
- `sonic-troubleshooting-agent`: README targets Docker Desktop on Apple Silicon.
- `closcall`: planning docs prescribe OrbStack + Ubuntu ARM VM for containerlab/SR Linux.

**VerifiedNet rule:** `capture_environment_metadata()` (LabBackend contract) must record host OS,
kernel, arch, container runtime, and image digests in every run manifest.

## 4. Local / unpinned image dependencies

| Image | Where | Problem |
|---|---|---|
| `docker-sonic-vs:latest` | `sonic-intent-agent/Dockerfile.sonic-fixed:1`, `sonic-troubleshooting-agent/Dockerfile.sonic-fixed:1` | base is `:latest`; not reproducible |
| `docker-sonic-vs-fixed:latest` | both sonic bringup.sh (`IMAGE="${IMAGE:-docker-sonic-vs-fixed:latest}"`) | locally built image, **not buildable from sonic-acl-validation-harness at all** (it only consumes it) |
| `nicolaka/netshoot:latest` | `evpn-vxlan-frr-lab/docker-compose.yml:54,65` | unpinned |
| `frrouting/frr:latest` | `sonic-troubleshooting-agent/scripts/configure_bgp.sh:62` | unpinned |
| gnmic/prometheus/pgvector | `closcall/compose.yaml:9,20,33` | **pinned by sha256 digest — the standard to copy** |
| Nokia SR Linux 25.3.3 | closcall `lab/fabric.yaml` header | proprietary; pull-from-vendor only, never redistribute |

## 5. Host-specific paths & missing manifests

- `sonic-intent-agent`: README instructs `pip install -r requirements.txt` — **file does not exist**;
  README also references `phase1/`/`phase7/` directories that don't exist. Environment is
  unreconstructible as shipped.
- `sonic-troubleshooting-agent` core: implicit "stdlib + whatever's installed"; only
  `fine_tuning/` has a requirements file (unpinned `>=` ranges).
- evpn/acl repos: no Python packaging at all (script-level execution assumptions).

## 6. Module-global state

- `sonic-intent-agent/phase{4,5,6}/tools.py`: `proposed_plans: list = []` module-global mutating
  side channel between LLM tool-dispatch and apply step (line 112/112/122). Must become an explicit
  plan store object in VerifiedNet.
- `sonic-troubleshooting-agent`: `CONTAINER` module constants (see §1); blackboard object itself is
  instance-scoped (acceptable pattern, deep-copy isolation verified in audit).

## 7. Service couplings

| Coupling | Repos (files verified) | VerifiedNet treatment |
|---|---|---|
| **Ollama** (HTTP, model `qwen2.5:7b-instruct`) | sonic-troubleshooting-agent (5 agent files, each with its own client), neuronoc (`app/llm/ollama.py` + graceful fallback), sonic-intent-agent (phase agents), closcall (`workflow/llm.py`, `scripts/qualify_llm.py`) | one shared model-provider adapter behind the `ModelAdapter` contract (destination TBD); model+tag+digest recorded in manifests; **never in ground-truth path** (Principle 11) |
| **Batfish** (pybatfish `Session`, `init_snapshot`, `q.initIssues`) | sonic-intent-agent only (9 files) | a Batfish verifier adapter behind the `Verifier` contract (destination TBD); service pinned by image digest; treat as deterministic verifier |
| **Postgres** | closcall (SQLAlchemy2/Alembic/asyncpg, 11 files), neuronoc (8 files, savepoint-per-test pattern), constellation (4) | **core schemas stay DB-free** (contract requirement); storage adapters live at the edge; neuronoc's savepoint test pattern is a harvest candidate as a testing utility |
| **Redis** | sonic repos reach Redis **indirectly** via `docker exec` + `sonic-db-cli`/`redis-cli` (CONFIG_DB/APP_DB/ASIC_DB); constellation declares Redis/Celery **but never uses them (vaporware — rejected)** | keep SONiC DB access behind an `EvidenceCollector`-contract implementation (destination TBD); parse via structured output, replacing `ast.literal_eval` on CLI stdout (`sonic-acl-validation-harness/acl/db_checks.py` fragility, confirmed in audit) |
| **SONiC image** | all three sonic repos | one canonical `Dockerfile` + digest pin under VerifiedNet infra, with build provenance |

## 8. Timing / convergence assumptions (silent flakiness sources)

- `evpn-vxlan-frr-lab/scripts/up.sh`: blind `sleep 8` / `sleep 10` instead of convergence polling.
- `sonic-acl-validation-harness` `flow`: reads ASIC_DB immediately after apply — no convergence
  polling (apply→observe race confirmed in audit).
- `sonic-intent-agent`: author measured 60–80 ms CONFIG_DB read-after-write lag and built
  `wait_for_settled` — the **right** pattern; harvest it.
- `sonic-troubleshooting-agent/scripts/bringup.sh`: has real readiness gates (good reference).

**VerifiedNet rule:** all onset/recovery verification uses bounded polling with explicit timeouts;
no bare sleeps.

## 9. Other confirmed hazards to exclude at harvest time

- constellation: `torch.load(weights_only=False)` (pickle-RCE), hardcoded dev password in
  `backend/.../config.py`, serving-vs-training normalization mismatch, stub `deployment/` and
  `data_engine/shadow_mode.py` / `hard_case_miner.py` — **rejected** as code sources.
- neuronoc: most API endpoints unauthenticated; no structured logging — patterns not to copy.
- evpn validator: ≥4/15 ping-success floor would poison ground truth if reused as a recovery
  verifier — **redesign, do not port** the threshold.
