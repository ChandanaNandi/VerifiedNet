# Part 1 — Executive Summary

ClosCall is a genuinely serious, unusually disciplined single-author research/systems project.
It is **not** a demo dressed up with docs: the core claims are backed by real, readable,
type-checked Python (~5,760 LOC of `src/`, 195 unit-test functions with 347 asserts, ~15,400 LOC
of Python total). The headline scientific contribution — *classical single-interface detection is
structurally blind to "gray" (subtle, non-hard-down) faults even under traffic load, while
relational/temporal learned localization recovers them* — is supported by an actual ablation
harness (`scripts/gate12_5_localization_ablation.py`) that trains a rule baseline, a scikit-learn
MLP, and a **hand-written message-passing GNN** (`class GNN(nn.Module)`), reports bootstrap
confidence intervals, uses a pre-registered location-inductive split, and includes a
leave-one-leaf-out cross-validation to resolve a confound. The negative findings are published,
not buried (`docs/LIMITATIONS.md`).

The engineering is equally strong where it exists: the evidence/claims verifier
(`src/closcall/evidence/claims.py`), the isolated executor with audit-write-first ordering
(`src/closcall/executor/executor.py`), the idempotent correlator using PostgreSQL `ON CONFLICT`
(`src/closcall/incidents/correlator.py`), and the FastAPI HITL app with CSRF/IDOR/RBAC
(`src/closcall/api/app.py`) are all real, injected-dependency, unit-testable code.

The project's defining trait is **radical intellectual honesty**. It ships an ADR
(`docs/decisions/ADR-004`) explicitly stating that the full 12-check safety precheck suite
(`run_prechecks`) is implemented and unit-tested but **NOT wired into the live execution path**,
and refuses to fake-wire it ("safety theater"). This is rare and admirable — but it is also the
central caveat of the whole system: **the end-to-end "detect → localize → LLM-diagnose → approve →
remediate on a live fabric" loop is never demonstrated running as one integrated live system.**
It exists as (a) a deterministic vertical slice against a live lab (no ML, no LLM), (b) an offline
ML ablation against a stored corpus, (c) an offline LLM qualification against 7 fixtures, and (d)
an offline HITL UI against fakes. The pieces are individually real; the integrated whole is
assembled only on paper (the planning "canon").

**Bottom line:** This is upper-tier portfolio work — the honesty, evaluation rigor, and networking
domain fluency are at or above senior level. The gaps are integration breadth (empty
integration/e2e/security test dirs, no live end-to-end), scale (a 6-switch lab, N=156 test
incidents), and a heavy planning-doc apparatus whose ceremony sometimes exceeds the delivered
surface area.

Overall grade: **strong senior-level individual project; publishable as a workshop/short paper
with more data; would need substantial hardening for production.**

---

# Part 2 — Architecture

```
                          ClosCall — component & data-flow map
                          (solid = implemented & exercised; dashed = documented/partial)

  ┌──────────────────────────────────────────────────────────────────────────────────────┐
  │                            LAB / SIMULATION PLANE (live, Docker)                        │
  │                                                                                        │
  │   lab/fabric.yaml (2 spine / 4 leaf / 4 host, ASNs, IPAM pools)                         │
  │        │  domain/fabric.py  allocate()  ── deterministic /31, /32, mgmt address math    │
  │        ▼                                                                                │
  │   domain/render.py ──► lab/generated/  { <node>.cli, topology-srl.clab.yml,             │
  │        │                                 topology.json, ipam.md, manifest.json(sha256)} │
  │        ▼  (containerlab, `make lab-up`)                                                 │
  │   ┌───────────┐  BGP/ECMP  ┌───────────┐        SR Linux 25.3.3 nodes                   │
  │   │ spine1/2  │◄──────────►│ leaf1..4  │◄─ host1..4 (netshoot: iperf3/ping/nping)       │
  │   └───────────┘  /31 p2p   └───────────┘                                                │
  │        ▲ gNMI (:57400)          ▲ docker exec (tc tbf/netem, ip link, sr_cli)           │
  │        │                        │                                                       │
  │  chaos/faults.py  ── 7 fault plugins (admin_shutdown, carrier_loss, intermittent,       │
  │        │              rate_limited_uplink[tbf], impaired_link[netem], telemetry_gap,    │
  │        │              healthy_control) + chaos/ledger.py write-ahead undo                │
  │  traffic/generator.py ── collective-shaped iperf3 bursts (all_to_all / incast)          │
  └────────┼───────────────────────────────────────────────────────────────────────────────┘
           │ gNMI subscribe
           ▼
  ┌──────────────────────────────────────────────────────────────────────────────────────┐
  │                       OBSERVATION PLANE (compose.yaml, digest-pinned)                   │
  │   gnmic (openconfig collector) ──► Prometheus (2h TSDB, :9090 loopback)                 │
  │   telemetry/counters.py, telemetry/syslog.py  ── parse counters/oper-state/syslog       │
  └────────┼───────────────────────────────────────────────────────────────────────────────┘
           │ raw §9.1 telemetry windows (parquet: datasets/telemetry_window.py)
           ▼
  ┌────────────────────────────────┐        ┌───────────────────────────────────────────────┐
  │   DETECTION (classical)         │        │   DATASET / ML PLANE (offline, corpus)         │
  │  sensors/rules/fsm.py (oper FSM)│        │  datasets/features.py  §9.2 causal features    │
  │  sensors/timeseries/statistical │        │      (util_ratio, error/discard rate, mask)    │
  │   .py  (robust-EWMA/z, CUSUM)   │        │  datasets/graph.py  typed Clos graph builder   │
  │  sensors/detection.py ensemble  │        │  datasets/splits.py  location-inductive split  │
  │  sensors/evaluator.py           │        │  scripts/gate12_5_localization_ablation.py:    │
  │        │ signal                 │        │      RULE | MLPClassifier | GNN(nn.Module)     │
  └────────┼────────────────────────┘        │      + bootstrap CIs + leave-one-leaf-out CV   │
           ▼                                 └───────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────────────────────────────────────┐
  │                        INCIDENT / DIAGNOSIS PLANE (Postgres-backed)                     │
  │  incidents/correlator.py  open-or-attach (ON CONFLICT idempotent) + audit in one txn    │
  │        ▼                                                                                │
  │  evidence/tools.py  9 read-only, budget/as-of-scoped evidence tools ──► Snapshot        │
  │        ▼                                                                                │
  │  workflow/diagnose.py  collect→hypothesize→test→commit_or_abstain→draft_plan            │
  │     ├─ workflow/llm.py  LlmHypothesizer (Ollama, untrusted, JSON-repair, budget)        │
  │     ├─ RuleHypothesizer (deterministic fallback)                                        │
  │     └─ evidence/claims.py  typed verifier (supported|contradicted|insufficient)         │
  │        ▼  VERIFIED diagnosis class ─► allow-listed plan template                        │
  └────────┼───────────────────────────────────────────────────────────────────────────────┘
           ▼
  ┌──────────────────────────────────────────────────────────────────────────────────────┐
  │                       REMEDIATION / HITL PLANE (Postgres + FastAPI)                     │
  │  db/models.py  RemediationVersion(immutable, sha256 plan_digest) ─uq─ ApprovalDecision  │
  │  api/app.py + api/ui.py  login(JWT cookie)→case file→approve (CSRF/RBAC/IDOR)           │
  │        │  approval bound to EXACT plan_digest (executor/binding.py)                     │
  │        ▼                                                                                │
  │  executor/executor.py  execute_job(): _precheck(narrow) → pre-state → AUDIT-WRITE-FIRST │
  │        → device.set_admin_state → read-back → recovery predicate → reconcile            │
  │  executor/prechecks.py  run_prechecks()  ── FULL 12-check suite (DOCUMENTED-ONLY:        │
  │                                              built + unit-tested, NOT wired live, ADR-004)│
  │  executor/fabric_device.py  Device protocol (gNMI adapter) — injected                   │
  └──────────────────────────────────────────────────────────────────────────────────────┘
```

**Every connection explained.**
- `fabric.yaml → fabric.py → render.py`: a single hand-authored YAML is the sole source of truth;
  `allocate()` derives all addresses deterministically; `render.py` emits SR Linux CLI configs, a
  containerlab topology file, and a `manifest.json` of SHA-256s (byte-identical re-render is an
  acceptance criterion, A04).
- `containerlab → gnmic → Prometheus`: switches expose gNMI; the `compose.yaml` `gnmic` service
  subscribes and Prometheus stores 2h of TSDB, both attached to the containerlab-created external
  `closcall-mgmt` network, ports bound to `127.0.0.1` only.
- `chaos/faults.py → containerlab`: fault injection is `subprocess` `docker exec` into clab nodes
  (`sr_cli`, `ip link`, `tc qdisc`), with a write-ahead ledger storing the exact undo before apply.
- `telemetry → features/graph`: raw windows become causal §9.2 feature rows and a typed graph; the
  ablation harness trains models and scores against an evaluation-schema ground truth kept strictly
  out of features.
- `correlator → diagnose → executor`: a detector signal opens exactly one incident; the diagnosis
  workflow builds an immutable evidence snapshot, tests typed claims, and only a verified diagnosis
  yields an allow-listed plan; approval binds to the plan's SHA-256 digest; the executor is the
  sole holder of device-mutation capability and writes its audit intent *before* touching the
  device.

---

# Part 3 — Repository Structure

Top-level layout (walked folder by folder):

- **`src/closcall/`** — the real system. Sub-packages, each cleanly scoped:
  - `config/` — `settings.py` Pydantic-settings `CLOSCALL_`-prefixed env loader.
  - `domain/` — **networking + business logic**: `fabric.py` (topology model + IPAM allocator),
    `render.py` (deterministic config/clab/IPAM generator), `validate.py` (fabric invariants).
  - `datasets/` — **AI data plane**: `features.py` (causal features), `graph.py` (typed Clos
    graph), `splits.py` (leakage-safe location-inductive split), `telemetry_window.py` (parquet
    I/O), `manifest.py`, `schemas.py` (frozen feature-column contract + forbidden-column set).
  - `sensors/` — **classical detection**: `rules/fsm.py` (oper-state FSM), `timeseries/
    statistical.py` (robust-EWMA/z + CUSUM), `detection.py` (ensemble), `evaluator.py`.
  - `evidence/` — **grounding/verification**: `claims.py` (typed claim verifier),
    `tools.py` (9 scoped read-only evidence tools).
  - `workflow/` — **AI orchestration**: `diagnose.py` (the §12.1 state machine), `llm.py`
    (LLM hypothesizer + Ollama adapter), `slice_diagnose.py` (deterministic Gate-6 path),
    `report.py`.
  - `executor/` — **remediation**: `executor.py` (isolated executor), `prechecks.py` (full safety
    suite), `plan.py`, `binding.py` (approval↔digest gate), `rollback.py`, `audit_guard.py`,
    `fabric_device.py`.
  - `incidents/` — `correlator.py` (idempotent open-or-attach).
  - `db/` — `models.py` (SQLAlchemy 2, 3 schemas: core/evaluation/audit), `engine.py`.
  - `api/` — **web**: `app.py` (FastAPI factory), `auth.py` (JWT+Argon2), `ui.py`, `ui_repo.py`,
    `dashboard.py`, `charts.py`, `gates.py`, `adapters.py`, `approval.py`, `static/`, `templates/`.
  - `chaos/` — `faults.py` (fault plugins), `ledger.py` (write-ahead undo).
  - `telemetry/` — `counters.py`, `syslog.py`.
  - `traffic/` — `generator.py` (collective-shaped flow planner), `lab.py`.
  - `observability/` — `logging.py`.
- **`scripts/`** — **orchestration + evaluation harnesses** (40 files): corpus runners
  (`corpus_run_v3.py`), the ML ablation (`gate12_5_localization_ablation.py`), CV
  (`gate12_5_localization_cv.py`), LLM qualification (`qualify_llm.py`), the vertical slice
  (`vertical_slice.py`), API/UI seeds and smokes, `doctor.py`, `gen_readme_tables.py`, PKI gen.
- **`tests/`** — `unit/` (29 files, 195 test functions). **`contract/`, `e2e/`, `failure/`,
  `integration/`, `property/`, `security/` all exist but are EMPTY (`.gitkeep` only).**
- **`planning/`** — 5 large "canon" docs (~2,214 lines): Project Spec, Build Bible, Canonical
  Execution Bible (Gates 0–13, 871 lines), Data/API/State Contracts, Acceptance Matrix.
- **`docs/`** — `LIMITATIONS.md`, `RESULTS.md`, `TRACEABILITY.md`, `DATA_CARD.md`, `MODEL_CARD.md`,
  `threat-model.md`, `backlog.md`, `toolchain.md`, and `decisions/` (6 ADRs).
- **`evals/`** — `reports/` (30+ gate reports/logs), `protocols/` (pre-registration docs),
  `manifests/`.
- **`lab/`** — `fabric.yaml` (the one source of truth) + empty `generated/`, `pki/`, `configs/`,
  `traffic/` (all `.gitkeep` — generated at build time).
- **`migrations/`** — Alembic, 3 versions (`0001_initial`, `0002_evaluation_and_roles`,
  `0003_app_users`).
- **`deployments/`** — `prometheus/prometheus.yml`, `gnmic/gnmic.yaml`.
- **`artifacts/`** — `manifests/` (immutable dataset manifests) + `reports/`.
- **`schemas/json/`, `schemas/openapi/`** — **EMPTY (`.gitkeep` only)** despite the README's
  "executable schema" claim; `prompts/`, `dashboards/`, `deployments/compose/` also empty.
- Root: `Makefile` (44 targets — the true entrypoint catalogue), `compose.yaml`, `pyproject.toml`,
  `uv.lock`, `alembic.ini`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`.

**Ownership:** orchestration lives in `scripts/` + `workflow/diagnose.py`; business/networking
logic in `domain/`; AI logic in `datasets/` + `workflow/` + `evidence/` + the ablation scripts;
storage in `db/` + `migrations/`; evaluation in `scripts/gate12_5_*` + `evals/`.

---

# Part 4 — Complete Execution Flow

There is no single `main()`; the entrypoints are `Makefile` targets. Four concrete flows:

**Flow A — build the lab (`make lab-up`, depends on `render`).**
1. `scripts/`/`domain/render.py` loads `lab/fabric.yaml` → `fabric.load_fabric()` →
   `fabric.allocate()` computes every /31 p2p, /32 loopback, mgmt address deterministically
   (`p2p index = 2*(leaf-1)+(spine-1)`).
2. `render.render_srl_config()` emits per-node `set /` CLI configs (SR Linux 25.3.3 syntax);
   `topology-srl.clab.yml` references the digest-pinned `ghcr.io/nokia/srlinux@sha256:…` and
   `netshoot` host image; `manifest.json` records SHA-256 of every generated file.
3. `scripts/clab.sh` invokes containerlab; `compose.yaml` brings up `gnmic` + `prometheus` +
   `postgres` (pgvector image, though pgvector is unused).

**Flow B — deterministic vertical slice (`make vertical-slice`, `scripts/vertical_slice.py`).**
The most complete *live* end-to-end path, deliberately **NO LLM/neural**:
1. `subprocess` injects `admin_shutdown` on `leaf1:ethernet-1/1`.
2. Rules detect oper-down; `correlate_signal()` opens exactly one incident via `ON CONFLICT`
   upsert (a duplicate signal attaches, not re-opens), appending an incident event + audit row in
   the same transaction.
3. `evaluate_oper_state_claim()` returns `supported`; `build_link_down_plan()` produces a plan dict
   and its SHA-256 `plan_digest`.
4. An `ApprovalDecision` + durable `ExecutionJob` are written (same txn); the DB unique constraints
   (`uq_job_remv`, `uq_exec_job`) enforce one job/one execution.
5. `executor.execute_job(session, job_id, device)`:
   `_precheck` validates a matching approval bound to the exact digest via
   `approval_authorizes_plan`, checks the action/value allowlist and non-mgmt interface →
   captures `before = device.get_oper_state()` → **writes the audit "apply.intent" row and flushes
   BEFORE mutating** (if audit write fails → `AuditUnavailable`, mutation blocked) →
   `device.set_admin_state("enable")` → read-back → recovery predicate (`up` = succeeded,
   `""`/`unknown` = `outcome_unknown`, else `failed`) → `RecoveryCheck` + audit "apply" row.
6. Asserts the injector's `clear()` was never called — the executor's re-enable, not chaos cleanup,
   restored service. Writes `evals/reports/gate6-slice.txt`.

**Flow C — ML localization study (`make reports-v3`, offline).**
`scripts/gate12_5_localization_ablation.py`: loads settled `EvalFaultInjection` rows for campaign
`gate8-full-corpus-v3` from Postgres, joins parquet telemetry windows, builds per-interface causal
features (`causal_features`, v1 or v2 temporal), assembles the fabric graph + `edge_index`, then
runs three scorers over the pre-registered TEST split (leaf3+leaf4): `rule_score_fn` (oper-down
→ 1), `fit_mlp` (`StandardScaler` + `MLPClassifier(64,32)` on per-link concatenated endpoint
features with within-incident robust-z), and `fit_gnn` (2-layer mean-aggregation
`GNN(nn.Module)`, 120 epochs Adam, `BCEWithLogitsLoss`, link score = head over concatenated
endpoint embeddings). Reports per-class top-1/top-3/MRR/AUROC with a bootstrap 95% CI
(`boot_auroc`, a hand-rolled LCG for determinism). `gate12_5_localization_cv.py` adds a 4-fold
leave-one-leaf-out CV.

**Flow D — HITL approval UI (`make api-up` / `make demo-ui`).**
`scripts/api_serve.py` builds the app via `api/app.create_app(secret, users, repo, ui_repo)`;
`scripts/api_demo_seed.py` seeds a fresh un-approved incident. Browser flow: `POST /login`
(Argon2id verify → mint JWT into HttpOnly/Secure/SameSite=Strict cookie + double-submit CSRF
cookie) → case-file `/ui` page (RBAC `require_operator`, IDOR check returns 404 to hide existence)
→ approve (CSRF header echoed, `secrets.compare_digest`) drives the *same* `execute_job` through
the shared `approval_authorizes_plan` gate. LLM diagnosis (`workflow/llm.py`) is exercised
**only** offline against 7 fixtures in `scripts/qualify_llm.py`, never inside Flow B or D.

**Never demonstrated as one process:** detection → LLM diagnosis → live approval → live device
mutation with the full precheck suite. The live executor path uses the narrow precheck (ADR-004).

---

# Part 5 — Networking Concepts

- **Clos / leaf-spine fabric** — `lab/fabric.yaml` 2-spine/4-leaf/4-host; the canonical AI-DC
  topology. Root cause is defined as a physical link (`§4.2`).
- **eBGP underlay + ECMP** — unique private ASNs per switch (spines 65101/2, leaves 65001-4);
  `scripts/b09_ecmp.py`, `b10_b11_convergence.py`, `measure_convergence.py` validate ECMP flow
  distribution and reconvergence. Protocol: BGP over SR Linux.
- **IPAM / deterministic addressing** — `domain/fabric.allocate()`: /31 point-to-point links,
  /32 loopbacks on `system0`, mgmt /24, per-leaf host /24 (`172.16.{leaf}.0/24`), summary route
  `172.16.0.0/16`. Verified against SR Linux 25.3.3 syntax.
- **gNMI telemetry** — OpenConfig `gnmic` collector (`deployments/gnmic/gnmic.yaml`) subscribes to
  interface counters + oper-state; the only structured telemetry protocol used.
- **SR Linux config plane** — `render_srl_config()` emits `set /` CLI; faults use `sr_cli`.
- **Interface counters** — in/out octets, error packets, discarded packets, oper-state; converted
  to per-second rates by `datasets/features._rate()` (endpoint-delta, counter-reset aware).
- **Data-plane fault mechanisms** — Linux `tc tbf` (bandwidth shaping = `rate_limited_uplink`),
  `tc netem loss/delay` (`impaired_link`), `ip link set down` (`carrier_loss`), carrier flap
  (`intermittent_link`), gNMI admin-state (`admin_shutdown`). Taxonomy is honest: mechanism ==
  label, explicitly NOT PFC/ECN or degraded optics (`chaos/faults.py` `FAULT_TAXONOMY`).
- **Collective-communication traffic shape** — `traffic/generator.py` plans all-to-all / incast
  iperf3 bursts to load leaf uplinks (shape only, explicitly not NCCL/all-reduce validation).
- **Syslog** — `telemetry/syslog.py` parses device syslog as untrusted evidence.
- **RIB/FIB + reachability** — `scripts/lab_check.py`, `traffic_smoke.py`, `fault_smoke.py` verify
  routing/reachability; ping/nping/iperf3 via `netshoot`.
- **Loopback-only exposure** — every browser-facing port bound to `127.0.0.1` (compose + threat
  model) as the sole claimed network control.

---

# Part 6 — AI Concepts

**Actually implemented:**
- **Grounding / evidence verification (the strongest AI idea here)** — `evidence/claims.py`:
  typed `Claim` propositions verified deterministically against an immutable `Snapshot`, returning
  `supported`/`contradicted`/`insufficient`. `sustained` predicates require *every* in-window
  sample to satisfy (defeats cherry-picking); unit/type mismatch → `insufficient` not spurious
  support. This is a real, unit-tested anti-hallucination mechanism (`test_claims.py`).
- **LLM tool use / scoped retrieval** — `evidence/tools.py`: 9 read-only tools behind one envelope
  enforcing incident scope, as-of causal upper bound (no future reads), result limit, and
  call/row budget. `EvidenceSource` deliberately exposes no ground-truth method (ground truth is
  inaccessible by design). `get_metric_window` accepts only allow-listed template IDs.
- **LLM as untrusted hypothesizer + verifier gate** — `workflow/llm.py` + `diagnose.py`: the LLM
  proposes ≤3 structured JSON hypotheses; output is parsed defensively, JSON-repair-reprompted up
  to a cap, then abstains; a token `LlmBudget` forces abstention over fabrication. Every claim
  still runs the deterministic verifier, and a diagnosis commits only if a *supported* claim
  *entails* a recognized class (`DiagnosisDef.entailed_by`) — so an invented class or a
  supported-but-irrelevant claim cannot fabricate a diagnosis.
- **Prompt-injection resistance** — `build_prompt()` frames evidence explicitly as untrusted DATA,
  never instructions; logs/runbooks are tagged `trusted=False` and must never populate action
  parameters. Tested via an injection fixture (`test_diagnose.py`, `gate10-llm.txt` reports
  `injection_held=True`).
- **Learned fault localization (MLP + GNN)** — `scripts/gate12_5_localization_ablation.py`: a
  scikit-learn `MLPClassifier` and a **from-scratch message-passing GNN** (mean neighbor
  aggregation, link = concat of endpoint embeddings, `BCEWithLogitsLoss`). Real relational learning
  over the Clos graph.
- **Classical anomaly detection** — robust-EWMA/z-score + two-sided CUSUM + oper-state FSM
  ensemble (`sensors/`), causal (each decision uses only strictly-earlier samples).
- **Rigorous ML evaluation** — pre-registered location-inductive split (leakage-safe by
  physical-link disjointness, `datasets/splits.py`), bootstrap CIs, leave-one-leaf-out CV, feature
  ablation (v1 aggregate vs v2 temporal), honest healthy-control-at-chance sanity check, refuted
  sub-hypotheses recorded. This is genuine research methodology.
- **Local LLM qualification** — `scripts/qualify_llm.py` benchmarks `qwen2.5:7b`/`14b` via Ollama
  on 7 fixtures with accuracy + injection-held + schema-repair + token/latency metrics.

**Documented-only / absent:**
- **RAG with embeddings / vector search** — the Postgres image is `pgvector` but **no embedding or
  vector-similarity code exists**; `search_runbooks`/`similar_incidents` are keyword/record tools,
  not semantic retrieval. `prompts/` is empty.
- **Multi-step agent loop / planning** — the "agent" is a single hypothesize→verify pass, not an
  iterative tool-calling agent. The NIKA agent-only external benchmark is **NOT RUN** (documented).
- The LLM is never invoked inside a live end-to-end run; only in the offline qualification.

---

# Part 7 — Software Engineering

**Strengths (genuinely high).**
- **Modularity & abstraction:** consistent dependency-injection via `Protocol`s (`Device`,
  `EvidenceSource`, `Chat`, `FlowRunner`, `UserStore`, `Repo`) makes every lab-bound side effect
  isolated and the pure logic unit-testable offline. Pure functions everywhere (feature builder,
  verifier, prechecks, diagnose) explicitly documented as side-effect-free.
- **Config:** `pydantic-settings` `CLOSCALL_`-prefixed loader; `.env.example`; no secrets in code
  (gitleaks in CI).
- **Dependency management:** `uv` with a committed `uv.lock`; `requires-python == 3.12.*`; CI runs
  `uv sync --frozen`. Container images pinned by **SHA-256 digest** (srlinux, netshoot, gnmic,
  prometheus, pgvector) — "latest" explicitly forbidden. GitHub Actions pinned by commit SHA.
- **Code quality:** ruff (E,F,W,I,UP,B,SIM,RUF), **mypy `strict = true`**, pre-commit hooks, 100-col
  lines. The code is clean, densely but purposefully commented with spec cross-references.
- **Error handling:** fail-closed patterns (audit-write-first, `outcome_unknown` never coerced to
  success, budget exhaustion → abstain, `executable()` requires all prechecks pass).
- **Storage:** SQLAlchemy 2 typed models across 3 schemas (core/evaluation/audit), Alembic
  migrations, meaningful constraints (`uq_remv_digest`, `uq_job_idem`, `uq_exec_job`,
  `CHECK_JOB_STATUS`), FK `ondelete=RESTRICT` to preserve audit integrity.
- **Reproducibility:** immutable dataset manifests content-bound by SHA-256, fixed seeds,
  deterministic rendering (byte-identical), pre-registration files committed before results,
  README result tables machine-generated from an immutable run id.
- **Logging:** structured `observability/logging.py`.

**Weaknesses.**
- **Test breadth is a facade in places:** `tests/{contract,e2e,failure,integration,property,
  security}/` are all empty. 195 unit tests are real and good, but there is **zero** integration,
  end-to-end, property-based (Hypothesis is a dependency but unused in committed tests?), or
  security test coverage committed. ADR-004 itself cites "no integration-test scaffold" as a reason
  not to wire the precheck suite — a self-reinforcing gap.
- **Empty scaffolding:** `schemas/json`, `schemas/openapi`, `prompts`, `dashboards`,
  `deployments/compose` are `.gitkeep`-only, contradicting the "executable schema"/OpenAPI framing.
- **Script sprawl:** 40 scripts with v1/v2/v3 and `_NONCANONICAL` variants; considerable
  archaeology (`consolidate_eval.py` vs `consolidate_eval_v3.py`, `corpus_run.py` vs `_v3`). The
  ablation harness (`gate12_5_localization_ablation.py`) mixes model definition, DB access, feature
  engineering, and reporting in one 413-line module — not library-grade.
- **Doc-to-code ratio:** 31 markdown files and ~3,400 lines of planning/doc "canon" for ~5,760 LOC
  of `src`. The Gate 0–14 methodology and 5-doc precedence hierarchy are impressive discipline but
  also heavy ceremony for a solo lab project.

---

# Part 8 — Research Quality

**What reviewers (NSDI/SIGCOMM/NeurIPS ML-for-systems workshop) would praise:**
- A crisp, falsifiable thesis with a **mechanistic explanation** (single-interface absolute
  detection cannot see gray faults; the recoverable signal is temporal instability + cross-link
  comparison) — and a controlled feature ablation (v1→v2) that *localizes which feature class
  carries the signal*.
- **Pre-registration** of split + hypotheses before results, immutable run anchoring, bootstrap
  CIs, a leave-one-leaf-out CV that explicitly resolves the GNN-impaired confound as data scarcity
  rather than overclaiming, and a healthy-control-at-chance negative control. This is textbook
  good ML-for-networking hygiene, rarely seen in student projects.
- **Published negative findings** (`LIMITATIONS.md`): the refuted octet-asymmetry hypothesis, the
  window-length leakage bug found and fixed, the v2→v3 corrected conclusion preserved in git.

**What reviewers would criticize (rejection risks):**
- **Scale & external validity:** one 6-switch containerlab, N≈156 test incidents, 26 per class.
  AUROC CIs on gray classes are wide (impaired MLP 0.910 [0.839,0.967]; GNN 0.721 [0.602,0.836]).
  A SIGCOMM/NSDI bar would demand a larger fabric, more topologies, and ideally some real or
  higher-fidelity traffic — the authors concede clab veths enforce no real capacity, so
  `util_ratio` is normalized throughput, not hardware utilization.
- **Baselines:** the "classical" baseline is EWMA/CUSUM/FSM on a *single interface*. Reviewers
  would ask for stronger multivariate/relational classical baselines (e.g. peer-relative
  thresholding, PCA/subspace methods, existing fault-localization systems) before crediting the
  GNN. The MLP already matches/beats the GNN with temporal features, weakening the "need a GNN"
  story.
- **Detection vs localization framing:** the detector is deliberately frozen and blind; showing
  localization beats a *provably-chance* rule (AUROC exactly 0.500) is a low bar. The honest top-1
  numbers (impaired 0.731) show the problem is recovered-but-not-solved.
- **No comparison to prior fault-localization literature or the cited NIKA benchmark** (explicitly
  not run). No ablation of GNN depth/architecture, no statistical test between MLP and GNN beyond
  overlapping CIs.

Verdict: **a strong workshop/short paper or a compelling systems-course capstone; not yet a
top-tier venue paper without more data, topologies, and stronger baselines.**

---

# Part 9 — Hiring Committee Review

**Would it impress NVIDIA / Cisco / Arista / Juniper / Azure & GCP Networking / Meta Infra?**
Yes — meaningfully, for a networking-focused SWE/research role. Concretely demonstrated skills:
- **AI-datacenter networking fluency:** Clos fabric, eBGP/ECMP underlay, gNMI/OpenConfig telemetry,
  SR Linux, containerlab, tc-based data-plane fault modeling, collective-traffic shaping. This is
  exactly the domain vocabulary Arista/NVIDIA/Juniper/Meta fabric teams use.
- **ML-for-systems done rigorously:** causal features, leakage-safe splits, GNN over topology,
  pre-registration, CIs — the kind of rigor Azure/GCP applied-science-for-networking teams want.
- **Production-systems instincts:** idempotency (ON CONFLICT + unique constraints), audit-write-
  first ordering, plan-digest-bound approval, `outcome_unknown` reconciliation, CSRF/IDOR/RBAC,
  digest-pinned supply chain, fail-closed safety. This is real SRE/infra judgment.
- **Exceptional engineering communication + honesty:** ADRs, threat model, data/model cards, an
  honest limitations ledger, and the refusal to fake-wire safety.

**Level assessment:** The *breadth and judgment* (safety architecture, evaluation methodology,
supply-chain discipline, honest scoping) read as **senior (L5) individual work**. The *delivered
scope* — a lab-scale system where the integrated live loop is not demonstrated, empty
integration/e2e test suites, and a heavy planning apparatus around a modest code surface — pulls
the *shipped artifact* toward **strong L4/new-grad-plus to L5**. Net: **a standout new-grad/L4
portfolio that argues credibly for L5 in interviews**, especially at a networking-infra org. It
does *not* yet demonstrate staff-level org-scale impact or production-at-scale operation, so it
would not by itself justify staff (L6+); it would, however, make a memorable senior-loop artifact.

---

# Part 10 — Weaknesses (brutally honest)

- **The integrated system is a paper assembly, not a running whole.** The four real subsystems
  (live deterministic slice, offline ML, offline LLM, offline UI) are never one live pipeline. The
  most safety-critical component — the full 12-check precheck suite — is **built and unit-tested
  but not wired to live execution** (ADR-004). The author is admirably honest about this, but it
  remains the central functional gap.
- **Empty test tiers.** `contract/e2e/failure/integration/property/security` are all empty.
  Hypothesis and httpx are dev deps but there is no committed property or HTTP-integration test.
  For a system whose entire thesis is safety and integrity, the absence of integration/failure
  tests is the biggest technical-debt item and is self-admittedly why safety wiring was deferred.
- **Empty scaffolding contradicts claims.** `schemas/openapi` and `schemas/json` are empty though
  the README/canon talk about an "executable schema"; `prompts/`, `dashboards/` empty. This is
  aspirational structure presented alongside real structure.
- **RAG is claimed by association, not implemented.** pgvector image is pulled; no embeddings, no
  vector search. `similar_incidents`/`search_runbooks` are non-semantic.
- **Evaluation scale is small and single-topology.** N≈26/class, one 6-switch fabric, wide CIs on
  the headline gray classes; `util_ratio` is a fidelity-limited proxy. External validity is thin.
- **The GNN may be unnecessary.** The simpler MLP with temporal features matches/beats it; the
  "need relational learning" narrative is weakened by the authors' own numbers.
- **Overengineering of process.** A 5-document precedence "canon," Gates 0–14, and append-only
  research logs are a lot of governance for ~5.7k LOC by one person; some reviewers will read it as
  ritual. Script directory carries dead/superseded variants (`*_NONCANONICAL`, v1/v2/v3 pairs).
- **Scalability not addressed.** Single executor, single Postgres, 6-switch lab; the deferred
  backlog (leasing/outbox, restart matrix, rate limits, hash-chain audit) is exactly the
  production-scale work, and it is all waived.
- **Security caveats acknowledged but real:** relies on Docker Desktop VM boundary not a
  hypervisor VM; no defense against same-host privileged attacker; no token rotation/revocation or
  rate limiting (waived). Fine for a lab, not for exposure.

---

# Part 11 — Reusable Components (for a future "NetworkGym")

**Directly reusable (library-grade, minimal change):**
- `domain/fabric.py` + `domain/render.py` — deterministic fabric spec → SR Linux/containerlab/IPAM
  generator. Excellent standalone "fabric compiler."
- `evidence/claims.py` — the typed-claim verifier. Domain-agnostic grounding primitive; extract
  verbatim.
- `evidence/tools.py` — the scoped/budgeted/as-of read-only tool envelope. Reusable RAG/agent
  guardrail pattern.
- `sensors/timeseries/statistical.py` + `sensors/rules/fsm.py` + `sensors/common.py` — causal
  EWMA/CUSUM/FSM detectors; clean, seedless, reusable.
- `datasets/features.py`, `datasets/graph.py`, `datasets/splits.py`, `datasets/schemas.py` — the
  leakage-safe causal-feature + typed-graph + split machinery is the crown jewel for a NetworkGym
  dataset layer.
- `chaos/faults.py` + `chaos/ledger.py` — the fault plugin + write-ahead-undo pattern (would need
  parameterizing off the hardcoded `clab-closcall-2s4l-` node prefix).
- `executor/` (executor, prechecks, binding, audit_guard, rollback) — the isolated-executor +
  digest-bound-approval + fail-closed-precheck pattern is broadly reusable for any human-gated
  actuator.
- `db/models.py` + `incidents/correlator.py` — idempotent incident/audit schema.

**Needs rewriting before reuse:**
- The `scripts/gate12_5_*` ablation/CV harnesses — sound methodology, but must be refactored into a
  `models/` library (separate `GNN`/`MLP`/scorer/metrics/bootstrap modules) with tests; currently
  monolithic and DB-coupled.
- The `api/` layer — usable as a template but tightly coupled to this incident schema.
- The `corpus_run*`/`consolidate_eval*` v1/v2/v3 sprawl — consolidate to one parameterized runner.

**Should stay independent:** the `planning/` canon, `evals/reports/*`, ADRs, and manifests — these
are project-specific provenance, not reusable code.

---

# Part 12 — Portfolio Positioning

**Recommendation: keep ClosCall as a standalone flagship repository, and extract 2–3 libraries.**

- **Stay independent:** ClosCall is a coherent, complete-in-scope research artifact with a
  narrative arc, provenance trail, and honest limitations. Merging it into a larger monorepo would
  dilute that story. It is the single best "show, don't tell" artifact for a networking-infra or
  ML-for-systems application.
- **Extract as libraries (submodules/PyPI):**
  1. `evidence/` (claims verifier + scoped tool envelope) → a small "grounded-claims" package —
     the most broadly valuable, domain-independent piece.
  2. `domain/fabric.py`+`render.py` → a "clos-fabric-compiler" package.
  3. `datasets/` split+feature+graph machinery → the seed of the NetworkGym dataset library.
- **Do NOT** fold the whole thing into a NetworkGym monorepo yet — the empty test tiers and
  unwired executor mean it is not yet a dependable foundation; harvest the pure modules instead.

Positioning statement for a resume/portfolio: *"Evidence-grounded incident-command research system
for AI-datacenter fabrics — GNN/MLP gray-fault localization with pre-registered, CI-bounded
evaluation, on a containerlab SR Linux Clos, with a fail-closed human-gated remediation executor."*
Lead with the ML evaluation rigor and the safety architecture; disclose the unwired-precheck and
lab-scale caveats up front (the repo already does).

---

# Part 13 — Interview Questions (Staff-level, specific to this repo)

1. In `executor.execute_job`, why is the audit "apply.intent" row flushed *before* `set_admin_state`, and what failure does the ordering prevent?
2. `_precheck` enforces a narrower set than `run_prechecks`. Walk through ADR-004: what are the two concrete blockers to wiring the full suite, and why is a hardcoded-`True` context "safety theater"?
3. `approval_authorizes_plan` is the single shared gate for both the UI approve path and the executor. Prove that there is no side door — which test enforces it and what does it tamper with?
4. In `claims.verify`, a `sustained` claim requires every matched sample to satisfy. How does this defeat cherry-picking, and what happens on a unit mismatch — why `insufficient` and not `contradicted`?
5. `RuleHypothesizer` gathers *all* oper-state evidence per subject, not just down samples. Why is that necessary for a correct `sustained down` claim under link flap?
6. The GNN's `conv` does `x + agg/deg.clamp(min=1)`. Identify the aggregation scheme, the self-loop treatment, and why `deg.clamp(min=1)` matters for isolated nodes.
7. The MLP matches or beats the GNN on `impaired_link` v2 (0.910 vs 0.721). Given that, defend or reject building the GNN at all.
8. Explain the location-inductive split. Why does physical-link disjointness make an explicit purge gap unnecessary, and where is that argued in `datasets/splits.py`?
9. `causal_features` reads only samples with `event_time ∈ [as_of-W, as_of]`. Which columns are in `FORBIDDEN_FEATURE_COLUMNS` and what specific leakage does each prevent?
10. `docs/LIMITATIONS.md` §2.3 describes a window-length leakage bug that scored gray faults 52/52. Explain the mechanism and the fix (common 25s truncation).
11. Why is `util_ratio` described as "normalized throughput, not hardware utilization"? What clab fidelity limit forces that caveat, and how does it bound the claims?
12. The oper-state rule is AUROC exactly 0.500 on gray faults. Why *exactly* 0.500, and why is beating it a weak bar?
13. `boot_auroc` uses a hand-rolled LCG rather than numpy RNG. Why, and what reproducibility property does it buy?
14. Healthy-control AUROC is ~0.50 for all methods. Why is a *high* control number the tell of a broken evaluation here?
15. In `correlate_signal`, walk through the ON CONFLICT upsert. What exact concurrency anomaly do `uq_signal_source_event` + `on_conflict_do_nothing` prevent?
16. `RemediationVersion` has `uq_remv_digest` and `ExecutionJob` has `uq_job_idem`/`uq_exec_job`. Describe the idempotency guarantees these give the executor without any application locking.
17. Why is `outcome_unknown` never coerced to success on ambiguous read-back, and how does `reconcile_job` resolve it after an executor restart?
18. The LLM output is parsed with `text[text.index("{"):text.rindex("}")+1]`. What attacks/malformations does this tolerate, and where does the budget force abstention over fabrication?
19. `build_prompt` labels evidence as untrusted DATA and tags logs/runbooks `trusted=False`. Trace how that trust bit is prevented from reaching action parameters end-to-end.
20. `DiagnosisDef.entailed_by` gates commit on a *supported claim that entails the class*. Why isn't "all claims supported" sufficient? What fixture found this?
21. The evidence `Budget` charges calls and rows. Why bound both, and what does `BudgetExhausted` protect against in an adversarial-LLM setting?
22. `EvidenceSource` deliberately omits any ground-truth method. Why is that an architectural (not just conventional) guarantee against eval leakage?
23. `render.py` excludes PKI material from the determinism manifest. Why would including cert serials break byte-identical re-rendering, and why keep PKI out of the hash?
24. Container images are pinned by digest and Actions by SHA. What supply-chain attacks does this stop that tag-pinning does not?
25. The threat model assumes no same-host privileged attacker and relies on the Docker Desktop VM boundary (ADR-002). What concrete attacks are explicitly out of scope, and is that defensible?
26. CSRF uses double-submit with a non-HttpOnly cookie compared via `secrets.compare_digest`. Why non-HttpOnly for CSRF, and why constant-time compare?
27. IDOR returns 404 (not 403) for unauthorized incidents. What does that choice protect, and what's the tradeoff?
28. JWT lives in an HttpOnly/Secure/SameSite=Strict cookie with no rotation/revocation (waived, I02). What attacks remain, and how bad are they for this deployment?
29. The corpus is anchored to an immutable manifest hash `dd8def51…`. Describe how `make reports-v3` refuses to run without it and why that matters for integrity.
30. v2 (traffic-free) produced the *wrong* conclusion, preserved in git. Why keep it immutable rather than delete it? What scientific-integrity principle is that?
31. The refuted octet-asymmetry hypothesis scored AUROC 0.42 (< chance). What does sub-chance tell you, and why record a refuted hypothesis at all?
32. Faults are injected via `subprocess docker exec` with a write-ahead undo ledger. Why store the exact undo *before* apply, and how does `verify_onset` differ from command completion?
33. `intermittent_link.verify_onset` returns `True` unconditionally. Is that a bug or justified? Defend it.
34. Detection feeds *rate* series, not raw counters. Why, and where does `_rate` handle counter resets?
35. The robust-EWMA detector holds its baseline during a crossing. Explain the self-masking failure that would occur if it folded the anomaly into the mean.
36. CUSUM resets accumulators after a detection and tracks a slow reference mean. What drift does the slow reference handle, and what does the reset prevent?
37. Feature `missingness_mask` is a 4-bit field. Why encode missingness explicitly as a feature rather than imputing, given the `telemetry_gap` fault?
38. `topology_hash` binds a plan to a topology. How does `no_topology_drift` in `run_prechecks` use it, and why is drift a fail-closed condition?
39. `run_prechecks` returns "executable only if EVERY check passed AND results is non-empty." Why the non-empty guard?
40. The MLP uses within-incident robust z-scores (MAD-based) appended to raw features. Why within-incident normalization for localization, and what leakage does it avoid vs global scaling?
41. GNN training is full-batch over all incidents for 120 epochs. What are the risks at this dataset size, and how would you detect overfitting given the CV numbers?
42. Explain the 4-fold leave-one-leaf-out CV's role. Why is it a *supplement* and not allowed to replace the pre-registered leaf3/leaf4 headline?
43. `schema_hash`/`preprocessor_hash` version the feature contract. How does changing feature computation force a new benchmark version (§16), and why is that desirable?
44. The pgvector Postgres image is used but no vector search exists. If asked to add real RAG over runbooks, where exactly would it plug into `evidence/tools.py` and what would you *not* let it influence?
45. `tests/{integration,e2e,security,failure}` are empty. Design the minimal integration test that would let ADR-004's precheck wiring proceed safely.
46. The executor allowlist permits only `set_admin_state=enable` on non-mgmt fabric interfaces. Why so narrow, and how would you safely widen it without weakening the digest-approval binding?
47. Walk the full transaction boundaries of the vertical slice: which writes must be in one txn with the audit row, and why?
48. `plan_digest` is `sha256(json.dumps(plan, sort_keys=True))`. What canonicalization pitfalls (float formatting, unicode, key order) could break digest stability across environments?
49. If you had to defend the GNN∕MLP result to an NSDI reviewer citing N=26/class, what additional experiments (topologies, baselines, statistical tests) would you run first?
50. The system claims "the UI cannot claim more safety than the executor enforces." Formalize that invariant and identify every code path and test that must hold for it to be true.

---

# Part 14 — Overall Score

| Dimension | Score | One-line justification |
|---|---|---|
| Architecture | 8/10 | Clean plane separation, injected boundaries, fail-closed executor; loses points because the integrated live loop is never assembled and safety prechecks are unwired. |
| Networking | 9/10 | Fluent Clos/eBGP/ECMP/gNMI/SR Linux/containerlab/tc fault modeling with an honest fidelity ledger; only lab-scale and single-topology hold it back. |
| AI | 7/10 | Real grounding/verification, tool-scoping, MLP+hand-written GNN, rigorous eval; but no RAG/embeddings, no agent loop, LLM never in the live path. |
| Systems Design | 8/10 | Idempotency, audit-write-first, digest-bound approval, reconciliation, supply-chain pinning; single-node scale and waived durability backlog cap it. |
| Code Quality | 9/10 | mypy strict, ruff, Protocol-based DI, pure testable cores, dense purposeful comments; script sprawl and empty scaffolds are the only blemishes. |
| Research | 8/10 | Pre-registration, CIs, CV, ablation, published negatives — excellent method; small N, single topology, weak-ish baseline temper it. |
| Reproducibility | 9/10 | Immutable manifests, digest pins, frozen lock, deterministic render, machine-generated result tables; near-exemplary. |
| Open Source Quality | 7/10 | Great docs/ADRs/CI/threat model; but empty test tiers, empty schema/prompt dirs, and no packaged library reduce turnkey usability. |
| Portfolio Value | 9/10 | A rare, coherent, honest AI-DC-networking + ML-for-systems artifact that reads as senior-level judgment. |
| Resume Value | 8/10 | Signals exactly the skills NVIDIA/Arista/Meta-Infra/Azure-Networking want; slightly discounted by lab scale and the unshipped integrated loop. |
| Hiring Impact | 8/10 | Would strongly differentiate a new-grad/L4 candidate and credibly argue L5 in a networking-infra loop; not a standalone staff-level proof. |
