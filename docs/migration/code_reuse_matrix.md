# VerifiedNet — Gate 1: Capability Map and Code Reuse Matrix

Status: **audit complete** (capability mapping only; no code moved, no package folders created)
Date: 2026-07-11
Baseline: Gate 0 pinned commits (authoritative; uncommitted local changes out of scope)

| Repo key | Repository | Commit |
|---|---|---|
| CC | closcall | `d192bf3` |
| NN | neuronoc-network-ops-assistant | `5f24447` |
| STA | sonic-troubleshooting-agent | `eb4c818` |
| SIA | sonic-intent-agent | `856623e` |
| EVL | evpn-vxlan-frr-lab | `5b5a479` |
| AVH | sonic-acl-validation-harness | `92a33d6` |
| CON | constellation | `24d037b` |

All symbol names below were verified by reading the actual files at these commits (not READMEs).
Proposed destinations are candidates confirmed in Gates 2–3. Owner approved Apache-2.0 as the
proposed outbound license; LICENSE/NOTICE files are deliberately not created in this gate.

---

## 1. Executive summary

VerifiedNet needs 65+ capabilities. The seven source repos supply strong, verified starting points
for roughly two-thirds of them, concentrated in five clusters:

1. **Deterministic lab & topology substrate** — CC `domain/fabric.py` + `domain/render.py` is the
   single best asset in the portfolio: a Pydantic-typed, unit-tested (test_fabric_ipam,
   test_render_determinism), deterministic topology→IPAM→config renderer. It becomes the design
   basis for `TopologySpec`. The three lab families (routed FRR from NN, EVPN/VXLAN from EVL,
   SONiC-VS from STA/AVH) each have a workable but flawed orchestration layer needing the same
   fixes: pinned images, convergence polling instead of sleeps, parameterized names.
2. **Fault lifecycle** — STA `faults/*` is the only source implementing the full
   preconditions→inject→verify-onset→restore→verify-recovery loop with bounded polling
   (`wait_for_state`, `wait_for_admin_status`). CC `chaos/ledger.py` adds the tested undo-ledger.
   Together they map nearly 1:1 onto the FaultScenario contract.
3. **Deterministic verification** — CC `evidence/claims.py` (typed predicates, `verify()`,
   `committable()`, unit-tested) is the claim-verification core. AVH contributes the deepest
   SONiC pipeline validation (CONFIG_DB→APP_DB→ASIC_DB, SAI mask normalization, entry
   fingerprinting, cleanup verification). SIA phase6 contributes the only Batfish adapter and the
   only read-after-write settling logic (`wait_for_settled`). NN contributes the only allow-listed
   read-only executor.
4. **Dataset & evaluation machinery** — CC `datasets/splits.py` (leakage-safe, purged,
   location-inductive splits with hashed manifests — unit-tested) is the most research-critical
   harvest. STA `fine_tuning/schemas.py` + `evaluate_rca.py` provide the JSONL dataset format,
   prediction validation, and the only existing hallucination/grounding scoring, all needing
   scale-up. CC `datasets/manifest.py` provides provenance hashing.
5. **Safety patterns** — CC `executor/binding.py` + `api/approval.py` (digest-bound approval,
   pure and tested) and NN's AST no-execution test scans are small, unique, high-value harvests.

Honest absences (no source anywhere; new implementation required): **GraphRAG, knowledge-graph
claim/provenance layers, continued pretraining, confidence calibration, latency/resource
benchmarking, contract testing, a ground-truth oracle framework, STATE_DB collection, and the
IncidentRecord/BenchmarkRun/EvaluationReport contracts themselves.** Existing "agents" are one
shared-model fan-out (STA) and a no-LLM linear pipeline (NN) — reusable as scaffolding, not as a
multi-agent system. CON contributes zero code; two narrow design patterns only.

Classification totals appear in §12.

---

## 2. Capability map

| # | Capability group | Verdict (best source → classification) |
|---|---|---|
| 1 | Core schemas & contracts | CC claims/schemas + SIA ChangePlan as references → new contracts (Gate 3) |
| 2 | Runtime command execution | NN collector allow-list runner → REFACTOR_AND_REUSE |
| 3 | Lab lifecycle | STA bringup.sh readiness gates → REFACTOR_AND_REUSE (pattern) |
| 4 | Routed FRR lab backend | NN infra/lab → REFACTOR_AND_REUSE |
| 5 | FRR EVPN/VXLAN lab backend | EVL compose+scripts → REFACTOR_AND_REUSE |
| 6 | SONiC-VS lab backend | STA bringup.sh (primary), AVH bringup.sh (secondary) → REFACTOR_AND_REUSE |
| 7 | SR Linux/containerlab backend | CC lab/fabric.yaml + render_clab → REFACTOR_AND_REUSE (deferred priority) |
| 8 | Topology spec & deterministic IPAM | CC domain/fabric.py → REFACTOR_AND_REUSE (flagship) |
| 9 | Scenario registry | STA main.py Scenario/SCENARIOS → DESIGN_REFERENCE_ONLY |
| 10 | Fault lifecycle | STA faults/* → REFACTOR_AND_REUSE (flagship) |
| 11 | Undo ledger & cleanup | CC chaos/ledger.py → REFACTOR_AND_REUSE |
| 12 | Preconditions | CC executor/prechecks.py → REFACTOR_AND_REUSE; STA _check_preconditions secondary |
| 13 | Onset verification | STA wait_for_state/wait_for_admin_status → REFACTOR_AND_REUSE |
| 14 | Recovery verification | STA restore+wait (primary); EVL checks minus ping floor → REFACTOR_AND_REUSE |
| 15 | Evidence collection framework | CC evidence/tools.py (Budget, ToolContext, EvidenceSource) → REFACTOR_AND_REUSE |
| 16 | Raw evidence preservation | STA take_snapshot pattern → DESIGN_REFERENCE_ONLY; new impl |
| 17 | Evidence normalization | CC telemetry/syslog.py + counters.py → DIRECT_REUSE (pure, tested) |
| 18 | FRR/vtysh parsing | NN collector.py _vtysh_json + parsers → REFACTOR_AND_REUSE |
| 19 | SONiC CONFIG_DB collection | SIA phase6 sonic_client.py → REFACTOR_AND_REUSE |
| 20 | SONiC APP_DB collection | AVH app_db_observe + STA collectors → REFACTOR_AND_REUSE |
| 21 | SONiC STATE_DB collection | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** (verified: zero STATE_DB references in any repo) |
| 22 | SONiC COUNTERS_DB collection | STA collect_interface_counters → REFACTOR_AND_REUSE |
| 23A | SONiC ASIC_DB pure evaluation | AVH SAI helpers (strip_sai_mask, evaluate_asic_acl_entry_attrs, compute_asic_entry_delta, find_scenario_entry) → DIRECT_REUSE |
| 23B | SONiC ASIC_DB collection & I/O | AVH asic_db_observe + docker/sonic-db-cli I/O → REFACTOR_AND_REUSE |
| 24 | Interface-state collection | STA collect_interface_state → REFACTOR_AND_REUSE |
| 25 | BGP-state collection | NN _peers_from/_scrape_router (primary); STA collect_bgp_summary → REFACTOR_AND_REUSE |
| 26 | Route-table collection | NN collector.py route parsing (`show ip route json` → bounded evidence) → REFACTOR_AND_REUSE |
| 27 | Log collection | STA collect_recent_logs + CC syslog.normalize → REFACTOR_AND_REUSE |
| 28 | Telemetry collection (gNMI/Prom) | CC gnmic/prometheus stack + telemetry_window.py → REFACTOR_AND_REUSE (deferred) |
| 29 | ACL verification | AVH validate_acl_state / validate_cleanup → REFACTOR_AND_REUSE |
| 30 | Batfish verification | SIA phase6 batfish_client+snapshot_builder+verifier → REFACTOR_AND_REUSE |
| 31 | Claim verification | CC evidence/claims.py verify/committable → DIRECT_REUSE |
| 32 | Ground-truth oracle framework | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** (components exist; framework doesn't) |
| 33 | Incident record construction | CC incidents/correlator.py + manifests → DESIGN_REFERENCE_ONLY; contract is new |
| 34 | Dataset serialization | STA fine_tuning/schemas.py → REFACTOR_AND_REUSE (no committed tests; vocab/format must align to contracts) |
| 35 | Dataset manifests & provenance | CC datasets/manifest.py → DIRECT_REUSE |
| 36 | Leakage-safe dataset splitting | CC datasets/splits.py assemble_location_inductive → REFACTOR_AND_REUSE (flagship) |
| 37 | Rule-based baselines | CC sensors (EWMA/CUSUM/FSM) + NN anomaly/rules.py → REFACTOR_AND_REUSE |
| 38 | Diagnosis evaluation | STA evaluate_rca.py summarize → REFACTOR_AND_REUSE |
| 39 | Grounding evaluation | STA schemas/evaluate_rca grounding fields → REFACTOR_AND_REUSE (thin; extend) |
| 40 | Hallucination evaluation | STA evaluate_rca hallucination scoring → REFACTOR_AND_REUSE (thin; extend) |
| 41 | Remediation-safety evaluation | CC prechecks+binding as substrate → DESIGN_REFERENCE_ONLY; metric is new |
| 42 | Robustness evaluation | CC gate12_5 ablation scripts → DESIGN_REFERENCE_ONLY |
| 43 | Confidence calibration | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** (bootstrap CIs exist in CC eval scripts; no calibration code anywhere) |
| 44 | Latency & resource evaluation | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| 45 | Vector RAG | NN knowledge/rag.py + retriever.py → REFACTOR_AND_REUSE (replace hash-embedder default) |
| 46 | GraphRAG | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** (absent in all repos) |
| 47 | Knowledge-graph construction | CC datasets/graph.py TypedGraph (topology graph only — NOT a knowledge graph) → REFACTOR_AND_REUSE as substrate; KG layer new |
| 48 | Model-provider adapters | NN llm/ollama.py generate_ollama_json (primary) + CC LlmBudget → REFACTOR_AND_REUSE |
| 49 | Base SLM evaluation | STA baseline_predict.py + evaluate_rca.py → REFACTOR_AND_REUSE |
| 50 | Fine-tuning (LoRA) | STA fine_tuning/train_lora.py → REFACTOR_AND_REUSE |
| 51 | Continued pretraining | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| 52 | Blackboard orchestration | STA blackboard/blackboard.py → REFACTOR_AND_REUSE |
| 53 | Multi-agent orchestration | STA main.py fan-out + agents/* → DESIGN_REFERENCE_ONLY (single model, 5 duplicated clients) |
| 54 | Human approval & safety binding | CC executor/binding.py + api/approval.py → DIRECT_REUSE |
| 55 | Persistence | CC db/* + NN db/* → DESIGN_REFERENCE_ONLY (core stays DB-free; storage deferred) |
| 56 | Structured logging | CC observability/logging.py → DIRECT_REUSE |
| 57 | Reproducibility (manifests, digests, seeds) | CC emit_manifest*.py + digest-pinned compose.yaml → REFACTOR_AND_REUSE |
| 58 | CI | CC + NN ci.yml → DESIGN_REFERENCE_ONLY (new workflow required) |
| 59 | Unit testing patterns | CC tests/unit (29 files) → DESIGN_REFERENCE_ONLY |
| 60 | Contract testing | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** (CC contract/ dir exists but is empty — verified in audit) |
| 61 | Integration testing | NN conftest.py savepoint-per-test Postgres pattern → REFACTOR_AND_REUSE (as test utility) |
| 62 | Failure-path testing | STA fault error paths + NN tests → DESIGN_REFERENCE_ONLY; new suite |
| 63 | Security testing | NN AST no-execution scans (test_remediation/test_validation/test_telemetry) → REFACTOR_AND_REUSE |
| 64 | CLI | AVH acl_harness.py subcommand pattern → DESIGN_REFERENCE_ONLY; new CLI |
| 65 | Reports & experiment artifacts | CC consolidate_eval_v3.py + workflow/report.py → DESIGN_REFERENCE_ONLY |

---

## 3. Master reuse table

Legend — class: DR=DIRECT_REUSE, RR=REFACTOR_AND_REUSE, WA=WRAP_WITH_ADAPTER, DREF=DESIGN_REFERENCE_ONLY, RET=RETAIN_ONLY_IN_ORIGINAL_REPO, REJ=REJECT, NAS=NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED. status: impl=implemented, part=partially implemented, abs=absent. prio: P1 (Gates 3–4), P2 (Gates 5–8), P3 (Gates 9+). risk: L/M/H. Destinations are candidates (final in Gates 2–3). Provenance action "hdr+NOTICE" = per-file provenance header + NOTICE entry once outbound license lands; "CC-lic" = closcall public-license action from Gate 0 applies before public release.

| id | capability | repo | commit | source path | symbol | what it actually does | status | strengths | weaknesses | int deps | ext deps | env assumptions | side effects | tests | repro | class | destination (candidate) | required refactoring | tests required | provenance | prio | risk | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C01 | Core contracts | CC/SIA/NN | multi | `src/closcall/evidence/claims.py`; `phase6/change_plan.py`; `backend/app/schemas/*` | `Claim`,`Verdict`,`Predicate`; `ChangePlan`,`PredictedKey`; Pydantic schemas | typed claim/plan/API models in 3 styles | part | typed, small, proven shapes | none is the VerifiedNet contract set; repo-specific naming | varies | pydantic (CC/NN) | none | none | CC: `test_claims.py`; SIA: `test_change_plan.py` | high | DREF | `schemas/` (new, Gate 3) | write fresh v1 contracts informed by all three | JSON round-trip + contract tests | CC-lic | P1 | L | contracts must be repo-neutral by principle |
| C02 | Runtime cmd execution | NN | 5f24447 | `backend/app/lab/collector.py` | `_assert_show_command`,`_assert_known_router`,`_default_runner`,`CommandRunner` | allow-lists vtysh show-cmds, bounded runner, injectable for tests | impl | only allow-listed executor in portfolio; runner injection = testable | read-only only; FRR-specific allow-list; container-name map local | app config | stdlib subprocess | docker CLI present; `neuronoc-lab-*` names | spawns docker exec | `test_lab_collector.py` | high | RR | shared exec primitive (dest TBD Gate 2/3) | generalize allow-list policy object; timeout param; strip NN naming | unit + failure-path (timeout, denied cmd, dead container) | hdr+NOTICE | P1 | M | strongest safety properties of the 4 exec impls |
| C03 | Lab lifecycle | STA | eb4c818 | `scripts/bringup.sh` | readiness-gated bringup | starts SONiC-VS, polls readiness, idempotent | impl | real readiness gates (no blind sleep) | bash; hardcoded name/image; not a Python API | none | docker | `docker-sonic-vs-fixed:latest`; fixed container name | starts containers | none | med | RR | LabBackend impls | port to LabBackend.start/health_check; pin digest; parameterize | integration (lab up/down), failure-path | hdr+NOTICE | P1 | M | best lifecycle pattern; needs contract shape |
| C04 | Routed FRR lab | NN | 5f24447 | `infra/lab/docker-compose.lab.yml`, `infra/lab/scripts/lab.sh` | 4-router eBGP lab + inject/restore verbs | compose lab: edge-1/edge-2/core-1/branch-1, fault verbs | impl | real FRR 8.4.1 lab; fault verbs; used by tests | fixed names/IPs; FRR image tag not digest-pinned; bash | none | docker compose, FRR | Docker Desktop dev'd; fixed subnets | containers/netns | `test_lab.sh` (manual) | med | RR | routed-eBGP lab backend | parameterize via TopologySpec; digest pins; convergence polling | integration + repeatability | hdr+NOTICE | P1 | M | only routed multi-router lab in portfolio |
| C05 | EVPN/VXLAN lab | EVL | 5b5a479 | `docker-compose.yml`, `scripts/up.sh`,`setup_vxlan.sh`,`restore.sh` | leaf-spine EVPN lab | 3×FRR EVPN + 2 hosts; VXLAN/HER setup | impl | technically correct EVPN (spine `next-hop-unchanged`); fault+restore scripts | blind `sleep 8/10`; `netshoot:latest`; type-2 disabled (LinuxKit); hardcoded topology | none | docker compose, FRR, iproute2 | Docker Desktop kernel limits documented | containers, kernel netdevs | none | med | RR | EVPN/VXLAN lab backend | replace sleeps w/ convergence polls; pin images; parameterize; platform check in env metadata | integration + repeatability + cleanup-after-failure | hdr+NOTICE | P2 | M | only EVPN lab; correctness verified in audit |
| C06 | SONiC-VS lab | STA | eb4c818 | `scripts/bringup.sh`,`scripts/configure_bgp.sh`, `Dockerfile.sonic-fixed` | bringup + BGP peer lab + image recipe | SONiC-VS container + optional FRR peer, readiness-gated | impl | readiness gates; reversible BGP lab; image recipe exists | `:latest` base; `sonic-vs-troubleshoot` name in 5 files; `frrouting/frr:latest` peer | none | docker | Apple-Silicon Docker Desktop dev'd | containers | none (smoke only) | med | RR | SONiC-VS lab backend | digest-pin; name from TopologySpec; py-API wrapper | integration + failure-path | hdr+NOTICE | P1 | M | most complete SONiC bringup; AVH's is simpler secondary |
| C07 | SR Linux/clab lab | CC | d192bf3 | `lab/fabric.yaml`, `src/closcall/domain/render.py`, `scripts/clab.sh` | `render_clab`,`render_srl_config`,`render_all` | generates containerlab topo + SRL configs from spec | impl | deterministic generation; tested (`test_render_determinism.py`) | SR Linux proprietary image; heavy resource need; clab-specific | domain/fabric | containerlab, SRL 25.3.3 | OrbStack/VM prescribed | writes generated configs | yes (unit) | high | RR | SR Linux lab backend (deferred) | decouple from CC paths; keep as optional backend | unit (render) + deferred integration | CC-lic | P3 | M | premium backend but heaviest; defer past Gate 4 |
| C08 | Topology spec + IPAM | CC | d192bf3 | `src/closcall/domain/fabric.py` | `FabricSpec`,`ResolvedTopology`,`ResolvedNode`,`ResolvedLink`,`ResolvedHostNetwork` | Pydantic spec → deterministic address/link resolution | impl | typed; deterministic math; unit-tested (`test_fabric_ipam.py`); single source of truth pattern | Clos/SRL-shaped assumptions (roles, iface naming) | none | pydantic | none (pure) | none | yes (unit) | high | RR | `TopologySpec` basis in `schemas/`+`labs/` | generalize roles/iface naming beyond Clos/SRL; rename repo-specific fields | port CC tests + property tests (address uniqueness) | CC-lic | P1 | L | flagship harvest; pure + tested |
| C09 | Scenario registry | STA | eb4c818 | `main.py` | `Scenario` + scenario dispatch | maps scenario name → fault module + evidence filter | impl | simple, explicit | flat dict; no params, no versioning, no families | faults/, collectors/ | none | fixed container | none | none | med | DREF | `scenarios/` (new) | new registry w/ ScenarioDefinition contract (params, families, versions) | unit + contract | hdr+NOTICE | P1 | L | pattern right, shape too thin for platform |
| C10 | Fault lifecycle | STA | eb4c818 | `faults/interface_admin_down.py`,`faults/bgp_asn_mismatch.py`,`faults/bgp_neighbor_removal.py` | `inject`,`restore`,`_check_preconditions`,`read_*`,`wait_for_state`,`wait_for_admin_status` | reversible faults w/ precondition checks + bounded polling verify | impl | maps ~1:1 to FaultScenario contract; bounded polls; reversibility proven in audit | `CONTAINER` module constant; per-file `_docker_exec` copies; SONiC-only | collectors implicitly | docker | fixed container name | mutates device config | none (smoke) | med | RR | `faults/` impls behind FaultScenario | extract exec dep; parameterize target; split onset/recovery verifies per contract | unit (logic) + integration + repeatability + cleanup-after-failure | hdr+NOTICE | P1 | M | only full fault lifecycle in portfolio |
| C11 | Undo ledger | CC | d192bf3 | `src/closcall/chaos/ledger.py` | `Ledger`,`LedgerRecord`,`Phase`,`now_record` | append-only fault/undo ledger with phases | impl | tested (`test_ledger.py`); explicit phase transitions | CC-specific phase vocab | none | stdlib | none | file/db writes at edges | yes (unit) | high | RR | fault lifecycle bookkeeping | align Phase enum w/ FaultScenario states | port tests + failure-path | CC-lic | P1 | L | tested cleanup bookkeeping, rare asset |
| C12 | Preconditions | CC | d192bf3 | `src/closcall/executor/prechecks.py` | `run_prechecks`,`executable`,`failures`,`PrecheckContext`,`PrecheckResult` | 12-check gate before mutation; pure decision fns | impl | tested (`test_prechecks.py`); pure; audit-confirmed real | never wired live in CC (ADR-004) — harness must wire it | executor/plan | stdlib | none | none | yes (unit) | high | RR | precondition stage of fault/remediation loops | decouple from CC Plan type → contract types | port tests + integration wiring test | CC-lic | P1 | L | built+tested but unwired; VerifiedNet supplies the wiring |
| C13 | Onset verification | STA | eb4c818 | `faults/*` | `wait_for_state(predicate,timeout)`,`wait_for_admin_status` | polls typed predicate to confirm fault took effect | impl | bounded, interval-based; no blind sleeps | predicate strings ad hoc; SONiC-only sources | collectors | docker | fixed container | none | none | med | RR | onset stage (`verify_onset()`) | generalize predicate to VerificationCheck contract | unit + integration (timeout paths) | hdr+NOTICE | P1 | L | right pattern, needs contract typing |
| C14 | Recovery verification | STA+EVL | multi | STA `faults/*.restore`; EVL `validate/checks.py` | `restore`+`wait_for_state`; `bgp_underlay_established`,`vxlan_iface_exists`,`bridge_fdb_has_her_to` | restore then re-verify healthy; EVPN state checks | impl | STA: symmetric restore; EVL: real vtysh/iproute2 JSON checks | EVL `host_can_ping` passes at ≥4/15 replies — **unacceptable as ground truth**; hardcoded names | none | docker, vtysh | fixed names | none | none | med | RR | recovery stage (`verify_recovery()`) | port EVL checks minus ping floor; deterministic reachability criterion; parameterize | unit + integration + repeatability | hdr+NOTICE | P1 | M | ping floor would poison GroundTruth; must be redesigned |
| C15 | Evidence framework | CC | d192bf3 | `src/closcall/evidence/tools.py` | `Budget`,`BudgetExhausted`,`ToolContext`,`Record`,`EvidenceSource`(Protocol),`get_interface_state`,`get_bgp_state`,`get_metric_window`,`get_log_events`,`get_topology_neighbors`,`get_ranked_links` | budgeted, typed evidence-gathering tool layer over a source protocol | impl | Protocol-based source abstraction; budget enforcement; tested (`test_tools.py`) | CC evidence vocab; sources are CC adapters | evidence/claims | stdlib | none | none | yes (unit) | high | RR | `collectors/` framework + EvidenceCollector contract | rename to contract vocab; adapt EvidenceSource → EvidenceCollector | port tests + contract tests | CC-lic | P1 | L | best evidence abstraction in portfolio |
| C16 | Raw evidence preservation | STA | eb4c818 | `main.py` | `take_snapshot`,`print_snapshot` | pre/post full-state snapshots per scenario | impl | simple; proven in runs | dict-shaped, no schema/hashes; stdout-oriented | collectors | none | fixed container | files | none | med | DREF | EvidenceRecord/EvidenceBundle (new) | new impl: hashed, schema'd raw capture | unit + contract | hdr+NOTICE | P1 | L | pattern only; contract needs hashes+provenance |
| C17 | Evidence normalization | CC | d192bf3 | `src/closcall/telemetry/syslog.py`,`telemetry/counters.py` | `normalize`,`LogEvent`,`counter_deltas`,`Sample`,`Delta`,`Quality` | raw syslog→typed events; counter samples→quality-tagged deltas | impl | pure; tested (`test_syslog.py`,`test_counters.py`); quality flags | SRL syslog dialect assumptions | none | stdlib | none | none | yes (unit) | high | DR | normalization layer | none for logic; add dialect param for non-SRL logs | port tests; add FRR/SONiC dialect cases | CC-lic | P1 | L | pure+tested = direct reuse |
| C18 | FRR/vtysh parsing | NN | 5f24447 | `backend/app/lab/collector.py` | `_vtysh_json`,`_vtysh_text`,`_peers_from`,`_scrape_router`,`RouterScrape`, route parsers (L407–470) | vtysh JSON → typed peer/route/interface scrape | impl | tested (`test_lab_collector.py`); bounded output; injectable runner | NN evidence shapes; router name map | C02 | stdlib | docker exec | none | yes (unit) | high | RR | FRR collector | detach from NN schemas → EvidenceRecord; reuse alongside C02 | port tests + golden-output fixtures | hdr+NOTICE | P1 | L | strongest of 3 vtysh parsers (EVL, STA secondary) |
| C19 | CONFIG_DB collection | SIA | 856623e | `phase6/sonic_client.py` | `list_interface_keys`,`get_interface_ip`,`list_configured_interfaces`,`get_bgp_summary`,`_validate_interface_name`,`_validate_ip_address` | validated read (and write) ops on CONFIG_DB via docker exec | impl | input validation before exec (rare); read/write separation | `_run_docker_exec` local copy; fixed container; no deps manifest in repo | none | docker | SONiC-VS running | writes on apply_* | phase6 test scripts (live-env) | low | RR | SONiC CONFIG_DB collector (+ mutation ops for faults) | extract exec dep; split read vs write surfaces; pin env | unit w/ fake runner + integration | hdr+NOTICE | P1 | M | best-validated SONiC client of the 3 repos |
| C20 | APP_DB collection | AVH | 92a33d6 | `acl/db_checks.py` | `app_db_observe`,`sonic_db_cli`,`keys`,`hgetall` | reads APP_DB ACL state via sonic-db-cli | impl | works; centralized target config | `parse_hgetall_output` uses `ast.literal_eval` on stdout — fragile | acl/config | docker | `sonic-vs-acl` name | none | pure-logic tests only | med | RR | SONiC APP_DB collector | replace literal_eval w/ structured redis JSON; extract exec | unit + integration (live DB) | hdr+NOTICE (Apache-2.0 attribution) | P2 | M | only APP_DB reader; parsing must be hardened |
| C21 | STATE_DB collection | — | — | — | — | absent (verified: zero STATE_DB refs in any repo) | abs | — | — | — | — | — | — | — | — | NAS | SONiC STATE_DB collector | new implementation | unit + integration | n/a | P2 | L | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| C22 | COUNTERS_DB collection | STA | eb4c818 | `collectors/sonic_state.py` | `collect_interface_counters`,`_parse_redis_hgetall` | reads COUNTERS_DB per interface | impl | proven in runs | same fragile stdout parsing family; fixed container | none | docker | fixed container | none | none | med | RR | SONiC COUNTERS_DB collector | shared exec + structured parse | unit + integration | hdr+NOTICE | P2 | M | only COUNTERS_DB reader |
| C23A | ASIC_DB pure evaluation | AVH | 92a33d6 | `acl/db_checks.py` | `strip_sai_mask`,`evaluate_asic_acl_entry_attrs`,`compute_asic_entry_delta`,`find_scenario_entry` | SAI ternary-mask normalize + attr evaluation + entry fingerprint/delta (pure functions, no I/O) | impl | real SAI understanding; pure; covered by the repo's 35 pure-logic tests | scenario-shaped expectations (DATAACL/tcp443) baked into some evaluator defaults | acl/config (constants) | stdlib | none (pure) | none | yes (pure logic) | high | DR | SAI evaluation layer of ACL verifier | none for function bodies; parameterize scenario constants at call sites | port relevant tests + property tests (mask edge cases) | hdr+NOTICE (Apache-2.0) | P1 | L | pure + tested = direct reuse, subject to provenance and ported tests |
| C23B | ASIC_DB collection & I/O | AVH | 92a33d6 | `acl/db_checks.py` | `asic_db_observe`,`sonic_db_cli`,`run_in_container`,`keys`,`hgetall` | reads ASIC_DB SAI objects via docker exec + sonic-db-cli | impl | working end-to-end path proven in the repo's live run log | apply→observe race (no convergence poll); fragile stdout parsing family; fixed container name; binding-to-port not verified at SAI layer | acl/config | docker | `sonic-vs-acl` container running | spawns docker exec | pure-logic tests only (I/O untested) | med | RR | SONiC ASIC_DB collector | shared bounded exec; structured queries instead of stdout parsing; convergence polling; add SAI port-binding check | unit w/ fake runner + integration + race test | hdr+NOTICE (Apache-2.0) | P1 | M | I/O layer needs the same hardening as every other docker-exec path |
| C24 | Interface-state collection | STA | eb4c818 | `collectors/sonic_state.py` | `collect_interface_state` | CONFIG_DB+APP_DB view of one interface | impl | proven | fixed container; dict shapes | none | docker | fixed container | none | none | med | RR | interface collector | shared exec; EvidenceRecord shapes | unit + integration | hdr+NOTICE | P1 | L | works; needs contract shapes |
| C25 | BGP-state collection | NN | 5f24447 | `backend/app/lab/collector.py` | `_peers_from`,`_scrape_router` | typed BGP peer table from vtysh JSON | impl | tested; bounded | NN shapes | C02,C18 | stdlib | docker | none | yes | high | RR | BGP collector | contract shapes | port tests | hdr+NOTICE | P1 | L | strongest; STA `collect_bgp_summary` secondary |
| C26 | Route-table collection | NN | 5f24447 | `backend/app/lab/collector.py` (L407–470) | route parse + missing-loopback summary | `show ip route json` → bounded evidence | impl | tested; bounded-output discipline | routed-lab-specific expectations | C18 | stdlib | docker | none | yes | high | RR | route collector | generalize expected-prefix logic to scenario params | port tests + EVPN cases | hdr+NOTICE | P2 | L | only route-table parser |
| C27 | Log collection | STA+CC | multi | STA `collectors/sonic_state.py`; CC `telemetry/syslog.py` | `collect_recent_logs`; `normalize` | tail syslog; normalize to typed events | impl | CC side pure+tested | STA side fixed container; dialect coverage narrow | none | docker | fixed container | none | CC yes | med | RR | log collector + normalizer | shared exec; dialect adapters | unit + fixtures per NOS | both | P1 | L | pair covers capture+normalize |
| C28 | Telemetry (gNMI/Prom) | CC | d192bf3 | `compose.yaml`, `deployments/gnmic/gnmic.yaml`, `src/closcall/datasets/telemetry_window.py` | digest-pinned gnmic+prometheus; windowing | streaming telemetry stack + leakage-aware windows | impl | digest pins; tested windowing (`test_telemetry_window.py`) | SRL/gNMI-specific paths; heavy | domain | gnmic, prometheus | SRL lab | containers | yes (window logic) | high | RR | telemetry collector (SRL backend first) | keep; parameterize gNMI paths per NOS | port window tests | CC-lic | P3 | M | premium but backend-specific; defer w/ C07 |
| C29 | ACL verification | AVH | 92a33d6 | `acl/db_checks.py`,`acl/acl_harness.py` | `validate_acl_state`,`validate_cleanup`,`evaluate_config_db_state`,`evaluate_cleanup_state`,`render_acl_json`,`_emit_flow_report` | end-to-end ACL pipeline validation incl. cleanup proof | impl | cleanup verification is rare+valuable; honest flow report | single hardcoded scenario (DATAACL/tcp443/Ethernet4); race (C23) | C20,C23 | docker | fixed container | applies/removes ACL | 35 pure tests | med | RR | ACL verifier behind Verifier contract | parameterize scenario; add SAI port-binding check; convergence poll | port tests + integration + parameterized cases | hdr+NOTICE (Apache-2.0) | P2 | M | deepest verifier; needs generalization |
| C30 | Batfish verification | SIA | 856623e | `phase6/batfish_client.py`,`snapshot_builder.py`,`verifier.py` | `open_session`,`init_snapshot`,`get_init_issues`,`summarize_issues`; `apply_plan_to_config_db`,`build_candidate_snapshot`; `verify_plan`,`VerificationResult` | candidate-config snapshot → Batfish diff verdict w/ timeout | impl | real pybatfish use (audit-verified); current-vs-candidate issue diff; timeout guard | frr.conf stub documented; Batfish blind spots documented (subnets); no pinned service | change_plan | pybatfish, pandas | Batfish container reachable | snapshot dirs | `test_snapshot_builder.py`,`test_agent_verify.py` (live-env) | low | RR | Batfish verifier | pin batfish image digest; isolate session mgmt; contract-shape results | unit w/ recorded frames + integration | hdr+NOTICE | P2 | M | only formal-verification integration in portfolio |
| C31 | Claim verification | CC | d192bf3 | `src/closcall/evidence/claims.py` | `Claim`,`Predicate`,`Verdict`,`Snapshot`,`verify`,`committable` | typed predicate claims verified against snapshots | impl | pure; tested (`test_claims.py`); verdict semantics incl. committable gate | predicate vocab CC-scoped | none | stdlib | none | none | yes | high | DR | claim layer of `verifiers/` | none for core; extend predicate vocab additively | port tests + property tests | CC-lic | P1 | L | pure+tested; cornerstone for VerificationCheck |
| C32 | Ground-truth oracle | — | — | (components: C10 fault metadata, C13/C14 verifies, `scripts/corpus_verify.py` CC) | — | absent as a framework | abs | components exist | no unified oracle that stamps GroundTruth from injected-fault metadata + verifier outcomes | — | — | — | — | — | — | NAS | `incidents/` oracle (new) | new: oracle assembling GroundTruth strictly from injection metadata + deterministic verifier results (Principles 11–12) | unit + contract + failure-path | n/a | P1 | M | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| C33 | Incident record construction | CC | d192bf3 | `src/closcall/incidents/correlator.py`, `datasets/manifest.py` | `correlate_signal`,`_append_event`; manifest patterns | correlates alarms into incident rows w/ idempotency | part | idempotency discipline; async-safe | DB-coupled; not the IncidentRecord contract | db/ | sqlalchemy | postgres | db writes | partial | med | DREF | IncidentRecord builder (new, DB-free) | new builder honoring required 24-field contract | contract + unit | CC-lic | P1 | L | contract fields mandated by brief; no source matches |
| C34 | Dataset serialization | STA | eb4c818 | `fine_tuning/schemas.py` | `load_jsonl`,`write_jsonl`,`validate_example`,`validate_dataset`,`validate_prediction`,`format_prompt`,`format_target`,`extract_json_object`,`normalize_root_cause` | JSONL IO + row/prediction validation + prompt formatting | impl | pure; clean; audit-verified runnable | **no committed tests in source repo**; label vocabulary tied to 3 STA scenarios; prompt/target formats are STA-shaped | none | stdlib | none | file IO | none | high | RR | `datasets/` serialization | align prompt/target formats and label vocabulary with VerifiedNet contracts; JSONL IO utilities may carry over near-verbatim, but the module is not accepted unchanged | **new unit tests required (rule 5)** before acceptance | hdr+NOTICE | P1 | L | no source tests + contract alignment needed → refactor, not direct |
| C35 | Dataset manifests | CC | d192bf3 | `src/closcall/datasets/manifest.py` | `DatasetManifest`,`sha256_file`,`build_manifest` | content-hashed dataset manifests | impl | tested (`test_manifest.py`); hash discipline | CC field names | none | stdlib | none | file reads | yes | high | DR | DatasetManifest contract impl | align field names to contract | port tests | CC-lic | P1 | L | exactly the required provenance pattern |
| C36 | Leakage-safe splits | CC | d192bf3 | `src/closcall/datasets/splits.py` | `assemble_location_inductive`,`IncidentRef`,`PurgeParams`,`SplitManifest`,`_hash_manifest` | grouped, purged, location-inductive splits w/ hashed manifest | impl | tested (`test_splits.py`); leakage-aware (purge windows); hashed | policy vocab CC-specific (location=leaf) | none | stdlib | none | none | yes | high | RR | DatasetSplit engine | generalize grouping key (scenario family/template/topology) beyond leaf-location | port tests + new grouping-key tests | CC-lic | P1 | L | research-critical; closest thing to the brief's split spec |
| C37 | Rule baselines | CC+NN | multi | CC `sensors/timeseries/statistical.py`,`sensors/rules/fsm.py`,`sensors/detection.py`; NN `anomaly/rules.py` | `RobustEwmaZScore`,`Cusum`,`OperStateDetector`,`detect_incident`; `rule_bgp_neighbor_down`,`rule_route_withdrawal`,`rule_interface_error_spike`,`rule_link_down` | statistical + FSM + event-rule detectors | impl | both tested; deterministic; CC evaluated vs ML (AUROC baselines) | CC: telemetry shapes; NN: NN event models | resp. schemas | numpy (CC) | none | none | yes both | high | RR | `models/rules/` baseline track | detach input shapes → normalized evidence | port tests + benchmark-harness tests | both | P2 | L | required baseline track; two complementary rule families |
| C38 | Diagnosis evaluation | STA | eb4c818 | `fine_tuning/evaluate_rca.py` | `summarize`,`_generate_predictions`,`write_summary` | RCA accuracy + structure validity scoring | impl | honest (reported 0% RCA); runnable smoke modes | n=6 eval; vocab tied to 3 scenarios; HF-coupled generation | schemas.py | transformers/peft (gen path) | Ollama/HF local | files | smoke only | med | RR | `evaluation/` diagnosis metrics | decouple metrics from generation; contract ModelPrediction input | new unit tests + metric-correctness tests | hdr+NOTICE | P2 | M | only diagnosis scorer; needs scale + decoupling |
| C39 | Grounding evaluation | STA | eb4c818 | `fine_tuning/schemas.py`,`evaluate_rca.py` | `validate_prediction` evidence-citation fields; summarize checks | checks predictions cite provided evidence | part | exists at all | shallow (string membership); tiny n | schemas | stdlib | none | none | none | med | RR | grounding metrics | extend: per-claim grounding vs EvidenceBundle; provenance-aware | new tests + adversarial fixtures | hdr+NOTICE | P2 | M | thin but honest start |
| C40 | Hallucination evaluation | STA | eb4c818 | `fine_tuning/evaluate_rca.py` | hallucination-rate fields in `summarize` | flags unsupported entities/claims in predictions | part | exists; honest | crude matching; offline only | schemas | stdlib | none | none | none | med | RR | hallucination metrics | formalize claim extraction + verifier-backed refutation | new tests + labeled fixtures | hdr+NOTICE | P2 | M | same as C39 |
| C41 | Remediation-safety eval | CC | d192bf3 | `executor/prechecks.py`,`executor/binding.py`,`api/approval.py` | (see C12/C54) | substrate for scoring unsafe-action proposals | part | strong substrate | no metric exists (would-this-plan-pass-prechecks scoring absent) | executor | stdlib | none | none | yes (substrate) | high | DREF | safety-eval metric (new) | new metric harness over precheck substrate | new tests | CC-lic | P3 | M | metric itself is new work |
| C42 | Robustness evaluation | CC | d192bf3 | `scripts/gate12_5_localization_ablation.py`,`gate12_5_localization_cv.py` | ablation + leave-one-leaf-out CV harnesses | perturbation/ablation experiment scripts | impl | real methodology (audit-verified GNN/MLP/rule ablations, bootstrap CIs) | script-grade; CC corpus coupling | datasets | torch, sklearn | CC corpus | files | none | med | DREF | robustness harness (new) | new harness; scripts inform design | new tests | CC-lic | P3 | M | methodology reference, not portable code |
| C43 | Confidence calibration | — | — | — | — | absent (bootstrap CIs ≠ calibration; no ECE/reliability code anywhere) | abs | — | — | — | — | — | — | — | — | NAS | calibration metrics (new) | new (ECE, reliability diagrams, abstention) | new tests | n/a | P3 | L | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| C44 | Latency/resource eval | — | — | (CC `scripts/bench_2s4l_NONCANONICAL.py` explicitly non-canonical) | — | absent as a metric suite | abs | — | — | — | — | — | — | — | — | NAS | latency/resource metrics (new) | new (wall-clock, tokens, memory per run) | new tests | n/a | P3 | L | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| C45 | Vector RAG | NN | 5f24447 | `backend/app/knowledge/rag.py`,`retriever.py` | `RunbookChunk`,`RagRunbookHit`,`RagEvaluationCase/Result`,`_embed_texts`,`_hash_embedding`,`_chunk_text`; `retrieve_runbooks` | FAISS IP index over chunked runbooks + eval harness | impl | real FAISS; eval harness exists; citation plumbing | default embedder is BLAKE2b hash (non-semantic); corpus=5 docs; eval n=5 | app models | faiss, numpy, (opt) sentence-transformers | none | index files | `test` coverage in NN suite | med | RR | `knowledge/vector_rag/` | make semantic embedder the default; corpus + eval scale-up; decouple from NN models | port tests + retrieval-quality tests | hdr+NOTICE | P3 | M | only RAG in portfolio; honest about its own thinness |
| C46 | GraphRAG | — | — | — | — | absent in all 7 repos (verified) | abs | — | — | — | — | — | — | — | — | NAS | `knowledge/graph_rag/` (future gate) | new per brief's GraphRAG rules (provenance-tagged edges) | new tests | n/a | P3 | H | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED**; do not manufacture a source |
| C47 | Knowledge-graph construction | CC | d192bf3 | `src/closcall/datasets/graph.py` | `TypedGraph`,`DeviceNode`,`InterfaceNode`,`TypedEdge`,`build_topology_graph`,`attach_features`,`graph_schema_hash` | typed topology graph w/ schema hash — **a topology graph, not a knowledge graph** | part | typed; tested (`test_graph.py`); hashed schema | topology-only; no claims/provenance/RFC nodes | domain | stdlib | none | none | yes | high | RR | KG substrate (topology layer) | extend node/edge taxonomy per GraphRAG rules; add provenance classes | port tests + new taxonomy tests | CC-lic | P3 | M | honest scope: substrate only; KG semantics are new |
| C48 | Model-provider adapters | NN | 5f24447 | `backend/app/llm/ollama.py`; secondary CC `workflow/llm.py` | `generate_ollama_json`,`OllamaUnavailableError`; `LlmBudget`,`ollama_chat`,`ChatResponse` | JSON-mode Ollama call w/ explicit unavailability error; CC adds call budgets | impl | graceful degradation; tested in NN suite; CC budget idea | 7 duplicate clients portfolio-wide; no streaming; single provider | none | httpx/requests | Ollama at localhost:11434 | network | NN yes; CC `test_llm.py` | high | RR | ModelAdapter impl (single shared) | merge NN error semantics + CC budget; model+digest manifest fields | port both test sets + contract tests | both | P1 | L | comparison winner (§4A) |
| C49 | Base SLM evaluation | STA | eb4c818 | `fine_tuning/baseline_predict.py`,`evaluate_rca.py` | baseline prediction + summarize | base-model predictions over dataset rows → metrics | impl | runnable; honest | tiny n; single model | schemas | HF stack | local model | files | smoke | med | RR | `benchmarks/` base-model track | decouple gen from scoring; ModelPrediction contract | new tests | hdr+NOTICE | P2 | M | thin but real starting point |
| C50 | Fine-tuning (LoRA) | STA | eb4c818 | `fine_tuning/train_lora.py` | `main`,`run_smoke_test`,`parse_args` | LoRA SFT over JSONL rows (HF/PEFT) | impl | audit-verified runnable; smoke mode | unpinned dep ranges; no seeds fixed; tiny data | schemas | torch, transformers, peft, accelerate | GPU/MPS optional | checkpoints | smoke | med | RR | `models/fine_tuning/` Track B | pin deps; fix seeds; config-file driven; dataset-manifest input | smoke + reproducibility test (fixed-seed) | hdr+NOTICE | P3 | M | only training code in scope (CON's is CV-specific → excluded) |
| C51 | Continued pretraining | — | — | — | — | absent | abs | — | — | — | — | — | — | — | — | NAS | Track B extension (future) | new | new tests | n/a | P3 | H | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| C52 | Blackboard orchestration | STA | eb4c818 | `blackboard/blackboard.py` | `Blackboard` | deep-copy-isolated shared evidence/hypothesis store | impl | isolation verified in audit; self-test exists | no typing of entries; no locking semantics documented | none | stdlib | none | none | self-test only | high | RR | `agents/` blackboard core | type entries w/ contracts; document concurrency semantics | new unit tests (concurrency, isolation) | hdr+NOTICE | P3 | L | small, sound, reusable |
| C53 | Multi-agent orchestration | STA | eb4c818 | `main.py` + `agents/{triage,interface_specialist,bgp_specialist,logs_specialist,diagnosis}.py` | ThreadPoolExecutor fan-out; `produce_diagnosis` | 4 specialists fan-out → diagnosis fan-in, one shared model | impl | working fan-out/fan-in; honest labeling in repo | 5 copy-pasted Ollama clients; prompt-scoped "specialization"; no iteration/interaction | blackboard | Ollama | fixed container/model | network | none | med | DREF | `agents/` comparison track (Gate 13) | rebuild on ModelAdapter + typed blackboard; keep fan-out topology as design | new tests | hdr+NOTICE | P3 | M | architecture reusable; implementation isn't |
| C54 | Approval & safety binding | CC | d192bf3 | `src/closcall/executor/binding.py`,`api/approval.py`,`executor/audit_guard.py` | `approval_authorizes_plan`,`guard_execution`,`SideDoorRejected`,`guarded_mutation` | digest-binds human approval to exact plan; audit-before-mutate guard | impl | pure; tested (`test_rollback_audit.py`,`test_auth.py`); audit-verified real | CC plan-digest format | none | stdlib | none | none | yes | high | DR | safety layer of executor/fault paths | none for logic; adopt digest format into contracts | port tests + property tests (tamper cases) | CC-lic | P1 | L | unique, small, exactly matches Principle 54 needs |
| C55 | Persistence | CC+NN | multi | CC `db/models.py`,`db/engine.py`; NN `db/models.py`,`db/session.py` | SQLAlchemy models/engines | async (CC) and sync (NN) Postgres layers | impl | mature patterns; Alembic in both | contract rule: core schemas DB-free; both are app-coupled | app schemas | sqlalchemy, alembic, asyncpg/psycopg | postgres | db | yes both | high | DREF | storage adapter (post-Gate 4) | design edge-adapter later; do not import models | n/a now | both | P3 | L | deliberately deferred; files stay put |
| C56 | Structured logging | CC | d192bf3 | `src/closcall/observability/logging.py` | `JsonFormatter`,`configure_logging` | JSON structured logging setup | impl | tiny; pure; tested (`test_logging.py`) | minimal feature set | none | stdlib | none | none | yes | high | DR | `common/` logging | none; add run_id/incident_id context fields | port tests | CC-lic | P1 | L | pure+tested |
| C57 | Reproducibility | CC | d192bf3 | `scripts/emit_manifest.py`,`emit_manifest_v3.py`, `compose.yaml` digest pins, `datasets/schemas.py schema_hash` | manifest emitters; digest pinning; schema hashing | run manifests binding code/config/image/data hashes | impl | the strongest reproducibility discipline in portfolio | script-grade emitters; CC-specific fields | datasets | stdlib | none | files | partial | high | RR | run/environment manifest layer | contract-shape manifests (RunManifest/EnvironmentManifest) | new unit tests | CC-lic | P1 | L | directly required by Gate 4 artifacts |
| C58 | CI | CC+NN | multi | `.github/workflows/ci.yml` (both) | lint+type+test pipelines | GH Actions with uv, ruff, mypy, pytest (+NN frontend/e2e) | impl | proven configs | repo-specific matrices/paths | n/a | GH Actions | GH | n/a | n/a | high | DREF | `.github/workflows/` (new file) | author fresh workflow (uv, ruff, mypy --strict, pytest tiers) | CI itself gates tests | n/a | P1 | L | configs referenced, not copied |
| C59 | Unit-testing patterns | CC | d192bf3 | `tests/unit/` (29 files) | e.g. `test_claims.py`,`test_splits.py`,`test_prechecks.py` | deterministic pure-logic unit suites | impl | discipline exemplar | CC-specific | n/a | pytest, hypothesis | none | none | n/a | high | DREF | `tests/unit/` conventions | adopt style; tests written per harvested symbol | n/a | CC-lic | P1 | L | style guide, not code |
| C60 | Contract testing | — | — | (CC `tests/contract/` exists but is **empty** — verified) | — | absent | abs | — | — | — | — | — | — | — | — | NAS | `tests/contract/` (new) | new: JSON round-trip, schema-version, cross-impl conformance | is itself tests | n/a | P1 | M | **NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED** |
| C61 | Integration testing | NN | 5f24447 | `backend/tests/conftest.py` | savepoint-per-test Postgres fixtures | real-DB tests with `begin_nested` rollback isolation | impl | fast, isolated, real DB; proven across 274 tests | Postgres-specific | sqlalchemy | postgres | postgres service | db | self-hosting | high | RR | test utility (when storage lands) | extract fixture pattern as utility | fixture self-tests | hdr+NOTICE | P2 | L | best integration-test pattern in portfolio |
| C62 | Failure-path testing | STA+NN | multi | STA fault error paths; NN failure tests | timeout/denied/dead-container cases | scattered failure-path coverage | part | patterns exist | not systematic | n/a | pytest | none | none | partial | med | DREF | `tests/failure/` (new suite) | dedicated suite per Gate 4 requirements | is itself tests | both | P1 | M | required by brief; sources only inform |
| C63 | Security testing | NN | 5f24447 | `backend/tests/test_remediation.py`,`test_validation.py`,`test_telemetry.py` | AST scans banning exec/network imports in protected packages | CI-enforced no-execution guarantee | impl | audit-verified real; unique asset | package lists NN-specific | ast, pytest | none | none | none | yes (they are tests) | high | RR | `tests/security/` AST guards | parameterize protected-package list + banned-import policy | guard self-tests | hdr+NOTICE | P1 | L | directly enforces Principles 11–14 boundaries |
| C64 | CLI | AVH | 92a33d6 | `acl/acl_harness.py` | `build_parser`,`main`, subcommands apply/validate/status/cleanup/flow | argparse multi-verb CLI w/ exit codes | impl | clean verb structure; deterministic exit codes | single-scenario wiring | acl modules | argparse | docker | varies | partial | high | DREF | `cli/` (new, likely typer/argparse) | new CLI over contracts; verb taxonomy informed by AVH+STA | CLI smoke tests | hdr+NOTICE | P2 | L | pattern good; wiring is scenario-specific |
| C65 | Reports & artifacts | CC | d192bf3 | `scripts/consolidate_eval_v3.py`, `src/closcall/workflow/report.py` | eval consolidation; report rendering | merges eval outputs into versioned reports | impl | v2→v3 correction kept immutable (integrity exemplar) | script-grade; CC shapes | datasets | stdlib | CC corpus | files | `test_report.py` partial | med | DREF | `artifacts/reports/` conventions + EvaluationReport impl | new, contract-shaped | new tests | CC-lic | P2 | L | conventions harvested, code rewritten |

---

## 4. Duplicate-capability comparisons

### A. Ollama clients (7+ implementations across 4 repos)
- **Primary:** NN `backend/app/llm/ollama.py::generate_ollama_json` — JSON-mode generation, explicit
  `OllamaUnavailableError`, deterministic fallback path proven by NN's test suite.
- **Secondary ideas:** CC `workflow/llm.py::LlmBudget` (hard call/token budgets — matches the
  quality rule against unbounded calls); CC `ChatResponse` typed result; SIA's single tool-call
  round-trip structure as tool-calling reference.
- **Rejected:** STA's 5 per-agent copies (`agents/{triage,interface_specialist,bgp_specialist,logs_specialist,diagnosis}.py`) — copy-paste duplication, no error taxonomy.
- **Reason:** correctness + tests + graceful degradation beat feature count (decision rule 1).

### B. FRR/vtysh collectors (3 repos)
- **Primary:** NN `backend/app/lab/collector.py` — allow-listed commands, injectable runner,
  bounded output, unit-tested parsers (`test_lab_collector.py`).
- **Secondary ideas:** EVL `validate/checks.py` EVPN-specific checks (`evpn_imet_from_peer`,
  `bridge_fdb_has_her_to`) — port as EVPN check functions on top of the NN runner.
- **Rejected:** STA's inline vtysh parsing in fault modules (mixed concerns: fault code parses state).
- **Reason:** NN is the only one with tests and safety properties.

### C. SONiC DB collectors (3 repos)
- **Primary:** split by DB — SIA `phase6/sonic_client.py` (CONFIG_DB; input validation);
  AVH `acl/db_checks.py` (APP_DB/ASIC_DB; SAI-aware evaluators, 35 pure-logic tests);
  STA `collectors/sonic_state.py` (COUNTERS_DB, logs).
- **Secondary ideas:** SIA `_validate_interface_name`/`_validate_ip_address` input guards adopted
  portfolio-wide; SIA `wait_for_settled` read-after-write settling.
- **Rejected:** all three repos' stdout parsing styles as-is (AVH `ast.literal_eval` on redis-cli
  output; STA `_parse_redis_hgetall` string splitting) — replace with structured queries.
- **Reason:** each repo is strongest on a different DB; none covers STATE_DB (C21 NAS).

### D. Fault injectors (3 repos)
- **Primary:** STA `faults/*` — full precondition→inject→verify→restore→verify loop, bounded polling.
- **Secondary ideas:** EVL `scripts/fault_*.sh` EVPN fault recipes (as scenario definitions, not
  code); NN `lab.sh inject bgp-down|iface-down` verbs for the routed lab; CC `chaos/faults.py`
  SRL-specific injection (defer with C07).
- **Rejected:** none outright; all bash faults are recipes to re-express behind FaultScenario.
- **Reason:** STA is the only implementation of the full contract lifecycle.

### E. Recovery logic (3 repos)
- **Primary:** STA `restore()` + `wait_for_state` symmetric-undo pattern.
- **Secondary ideas:** CC `chaos/ledger.py` (tested undo bookkeeping); CC `executor/rollback.py`
  `RollbackOutcome` semantics; EVL `restore.sh` as EVPN recipe.
- **Rejected:** EVL `host_can_ping` ≥4/15 threshold as any part of recovery ground truth.
- **Reason:** recovery verification must be deterministic (Principle 12); the ping floor is not.

### F. Evidence schemas (3 repos)
- **Primary:** CC `evidence/claims.py` (`Evidence`, `Snapshot`) + `evidence/tools.py` (`Record`,
  budgeted tools) — typed, tested, source-abstracted.
- **Secondary ideas:** STA snapshot dict layout (what a full SONiC evidence bundle contains);
  NN bounded-evidence discipline (caps on rows/chars per scrape).
- **Rejected:** raw dict passing as the contract (STA), DB-model-coupled evidence (NN
  IncidentEvidence rows) for the core schema.
- **Reason:** contract must be DB-free and typed; CC is closest.

### G. Ground-truth verification (4 repos)
- **Primary:** composition, not a single source — CC `evidence/claims.py::verify` (claim verdicts) +
  STA fault metadata + AVH pipeline evaluators + SIA `post_apply_check.py::check_plan_applied`.
- **Secondary ideas:** SIA `wait_for_settled` (eliminates read-after-write races in truth checks);
  AVH `validate_cleanup` (proving absence after recovery — rare and required by the Gate 4 loop).
- **Rejected:** any LLM output in the truth path (none found in truth paths of any repo — verified;
  NN's LLM RCA is explicitly advisory, STA's diagnosis is explicitly downstream of collection).
- **Reason:** the oracle framework itself is NAS (C32); these are its verified building blocks.

### H. Dataset split logic (1 repo)
- **Primary:** CC `datasets/splits.py::assemble_location_inductive` — grouped, purged, hashed.
- **Secondary ideas:** CC `datasets/features.py::preprocessor_hash` + `schemas.py::schema_hash`
  (hash-everything discipline).
- **Rejected:** STA fine_tuning's implicit whole-file splits (no grouping, leakage-unsafe).
- **Reason:** only leakage-aware implementation in the portfolio; generalize grouping key.

### I. RAG implementations (1 repo)
- **Primary:** NN `knowledge/rag.py` + `retriever.py` (FAISS, chunking, eval harness, citations).
- **Secondary ideas:** none elsewhere (CC pulls a pgvector image but contains zero vector code —
  confirmed in audit; **documented only**).
- **Rejected:** NN's `_hash_embedding` BLAKE2b default as the default embedder (keep only as a
  deterministic test/CI backend, clearly labeled non-semantic).
- **Reason:** single source; its own eval harness comes along.

### J. Agent/blackboard implementations (2 repos)
- **Primary:** STA `blackboard/blackboard.py` (state core) — small and sound.
- **Secondary ideas:** STA fan-out/fan-in topology (design); NN LangGraph 6-node pipeline as the
  deterministic-workflow pattern (its nodes are rule-based — useful for the rules track, not as an
  "agent").
- **Rejected:** STA agent implementations as-is (5 duplicated clients, prompt-only specialization);
  presenting either repo's system as "multi-agent AI" without qualification.
- **Reason:** honesty rules — orchestration exists, agent depth does not.

### K. Evaluation metrics (2 repos)
- **Primary:** STA `fine_tuning/evaluate_rca.py::summarize` + `schemas.py::validate_prediction`
  (diagnosis accuracy, structure validity, grounding/hallucination fields).
- **Secondary ideas:** CC eval scripts' bootstrap-CI + ablation + leave-one-leaf-out methodology
  (`gate12_5_*`); CC `sensors/evaluator.py::evaluate` alarm-vs-event matching.
- **Rejected:** CON's `validate` IoU metric (non-standard binary IoU, audit-flagged as inflated) —
  explicitly not a pattern to import.
- **Reason:** STA is contract-closest; CC supplies statistical rigor; calibration/latency are NAS.

### L. Lab orchestration (4 repos)
- **Primary:** pattern-level: STA `bringup.sh` readiness gating; structure-level: CC
  `domain/fabric.py`→`render.py` generation pipeline.
- **Secondary ideas:** NN `lab.sh` verb interface (up/down/inject/restore); EVL compose network
  layout.
- **Rejected:** EVL `up.sh` blind sleeps; all `:latest` tags; all hardcoded `container_name`s.
- **Reason:** no single orchestrator is contract-ready; the LabBackend contract absorbs the best
  pattern from each.

### M. Testing patterns (2 repos)
- **Primary:** CC `tests/unit/` discipline (29 deterministic pure-logic suites incl. property-style
  determinism tests like `test_render_determinism.py`).
- **Secondary ideas:** NN `conftest.py` savepoint-per-test real-Postgres isolation; NN AST security
  scans; SIA phase6 test scripts (shapes of live-env checks worth automating properly).
- **Rejected:** AVH's mock-only integration posture as sufficient for a lab platform (its pure-logic
  tests are good; the absence of integration tiers is the gap VerifiedNet must not inherit).
- **Reason:** brief requires unit/contract/integration/failure/property/security tiers; CC+NN cover
  unit/integration/security patterns; contract/failure tiers are new.

### N. Reproducibility patterns (2 repos)
- **Primary:** CC — digest-pinned `compose.yaml`, `datasets/manifest.py` content hashing,
  `schema_hash`/`preprocessor_hash`/`graph_schema_hash`, manifest emitters, immutable corrected
  reports (v2→v3 kept).
- **Secondary ideas:** NN lockfile + CI discipline (uv).
- **Rejected:** SIA (no deps manifest; README references nonexistent files), STA core (no
  packaging), EVL/AVH `:latest` images — as reproducibility patterns.
- **Reason:** CC is the only repo meeting the brief's manifest/pinning bar today.

### O. Safety and approval patterns (2 repos)
- **Primary:** CC `executor/binding.py::approval_authorizes_plan` + `api/approval.py::guard_execution`
  + `executor/audit_guard.py::guarded_mutation` (digest-bound approval; audit-write-before-mutate).
- **Secondary ideas:** NN AST no-execution scans (C63); NN `remediation/planner.py`
  `set_recommendation_approval` session-derived approver identity; SIA human y/n approval gate
  placement (approve-before-apply, verify-after-apply).
- **Rejected:** approval flows without digest binding (SIA's y/n prompt authorizes "the plan on
  screen", not a hashed artifact — design reference for UX placement only).
- **Reason:** Principle 54 requires tamper-evident binding; only CC implements it.

---

## 5. Top 20 highest-value harvest candidates

| # | Symbol(s) | Source | Why |
|---|---|---|---|
| 1 | `FabricSpec`/`ResolvedTopology` (C08) | CC | typed deterministic topology+IPAM; tested; TopologySpec basis |
| 2 | `faults/*` lifecycle (C10) | STA | only full inject/verify/restore/verify implementation |
| 3 | `claims.py` `verify`/`committable` (C31) | CC | pure tested claim verification — VerificationCheck core |
| 4 | `assemble_location_inductive` (C36) | CC | leakage-safe splits — the research backbone |
| 5 | `approval_authorizes_plan`+`guard_execution` (C54) | CC | digest-bound human approval; unique |
| 6 | `evidence/tools.py` Budget/ToolContext/EvidenceSource (C15) | CC | budgeted typed evidence framework |
| 7 | AVH SAI helpers `strip_sai_mask`/`compute_asic_entry_delta`/`find_scenario_entry` (C23A) | AVH | deepest SONiC/SAI validation logic; tested pure fns |
| 8 | NN collector allow-list runner (C02+C18) | NN | only safe executor + tested vtysh parsing |
| 9 | `chaos/ledger.py` (C11) | CC | tested undo/cleanup bookkeeping |
| 10 | `datasets/manifest.py` (C35) | CC | content-hashed manifests, required by Gate 4 artifacts |
| 11 | `prechecks.py` `run_prechecks` (C12) | CC | 12 tested safety checks awaiting wiring |
| 12 | SIA `wait_for_settled`+`post_apply_check` (C30/G) | SIA | read-after-write settling; post-change truth checks |
| 13 | SIA Batfish trio `batfish_client`/`snapshot_builder`/`verifier` (C30) | SIA | only formal-verification integration |
| 14 | `telemetry/syslog.py`+`counters.py` (C17) | CC | pure tested normalization |
| 15 | STA `fine_tuning/schemas.py` (C34) | STA | dataset JSONL format + prediction validation |
| 16 | NN AST security scans (C63) | NN | CI-enforced no-execution — enforces Principles 11–14 |
| 17 | NN `generate_ollama_json`+CC `LlmBudget` (C48) | NN+CC | the one ModelAdapter to rule out 7 duplicates |
| 18 | `validate_acl_state`/`validate_cleanup` (C29) | AVH | pipeline + cleanup verification pattern |
| 19 | CC `sensors/` EWMA/CUSUM/FSM + NN `anomaly/rules.py` (C37) | CC+NN | required rules baseline track |
| 20 | `observability/logging.py`+`emit_manifest*` patterns (C56/C57) | CC | structured logs + run manifests from day one |

## 6. Top 20 rejected or design-reference-only candidates

| # | Item | Source | Classification | Reason |
|---|---|---|---|---|
| 1 | All CV model code (`model/`, FCOS, heads, losses) | CON | RETAIN_ONLY_IN_ORIGINAL_REPO | rule 6: CV-specific; out of scope |
| 2 | CON `deployment/` (ONNX/quantization/benchmark) | CON | REJECT | audit: empty stubs/TODO |
| 3 | CON Celery/Redis infra | CON | REJECT | declared, never used (vaporware) |
| 4 | CON `torch.load(weights_only=False)` + hardcoded dev password | CON | REJECT | security hazards |
| 5 | CON backend inference path | CON | REJECT | normalization mismatch (serves out-of-distribution) |
| 6 | STA 5 per-agent Ollama clients | STA | REJECT | duplication; superseded by C48 |
| 7 | EVL `host_can_ping` ≥4/15 floor | EVL | REJECT | probabilistic pass unacceptable in truth path |
| 8 | EVL `up.sh` blind sleeps | EVL | REJECT (pattern) | convergence must be polled |
| 9 | All `:latest` image refs (EVL, SIA, STA, AVH) | multi | REJECT (pattern) | quality rule: no latest tags |
| 10 | SIA phases 2–5 directories | SIA | RETAIN_ONLY_IN_ORIGINAL_REPO | byte-identical/superseded copies; phase6 is canonical |
| 11 | SIA `tools.py` module-global `proposed_plans` | SIA | REJECT (pattern) | module-global state; replace with explicit plan store |
| 12 | NN LangGraph pipeline as "agent" | NN | DESIGN_REFERENCE_ONLY | linear, no LLM in graph; useful as deterministic workflow shape only |
| 13 | NN `_hash_embedding` as default embedder | NN | REJECT (as default) | non-semantic; retain only as labeled CI/test backend |
| 14 | NN frontend console | NN | RETAIN_ONLY_IN_ORIGINAL_REPO | app-specific UI; platform has no dashboard yet (no fake dashboards) |
| 15 | CC FastAPI app + HITL UI (`api/`) | CC | RETAIN_ONLY_IN_ORIGINAL_REPO | product surface, not platform primitive; approval logic (C54) harvested separately |
| 16 | CC DB models / Alembic tree | CC | DESIGN_REFERENCE_ONLY | core stays DB-free; storage deferred |
| 17 | CC empty `schemas/`,`prompts/`,`dashboards/`, empty test tiers | CC | REJECT | audit: empty decorative dirs; brief forbids importing that pattern |
| 18 | STA `Scenario` flat registry | STA | DESIGN_REFERENCE_ONLY | too thin: no params/families/versions |
| 19 | AVH `parse_hgetall_output` (`ast.literal_eval`) | AVH | REJECT (implementation) | fragile stdout parsing; function contract kept, body rewritten |
| 20 | CC `bench_2s4l_NONCANONICAL.py` | CC | RETAIN_ONLY_IN_ORIGINAL_REPO | self-labeled non-canonical; not benchmark-grade |

## 7. Capabilities requiring completely new implementation

NO_ACCEPTABLE_SOURCE — NEW IMPLEMENTATION REQUIRED (7 table rows): C21 STATE_DB collection;
C32 ground-truth oracle framework; C43 confidence calibration; C44 latency/resource evaluation;
C46 GraphRAG; C51 continued pretraining; C60 contract testing. Additionally, the **16 common
contracts themselves** (LabBackend…EvaluationReport) are new implementations — recorded under
C01 (DESIGN_REFERENCE_ONLY, since reference shapes exist) rather than as a NAS row.
Near-new (thin partial sources): C39/C40 grounding+hallucination metrics (STA start is shallow),
C33 IncidentRecord builder (24 mandated fields; no source matches), C53 agent implementations.

## 8. Dependencies that must be introduced

Pinned via `pyproject.toml` + `uv.lock` from day one: Python 3.12; pydantic v2 (contracts);
pytest (+hypothesis for property tests); ruff + mypy(strict); uv. Pulled in by specific
capabilities when their gate arrives (not before): numpy (C37), faiss-cpu + sentence-transformers
(C45), pybatfish + pandas (C30), torch/transformers/peft/accelerate/datasets (C50), httpx (C48).
Runtime services, digest-pinned when adopted: FRR 8.4.1 image, docker-sonic-vs (rebuilt + pinned),
Batfish, Ollama (model tag+digest in manifests), Postgres (post-Gate 4 only).

## 9. Dependencies that must be avoided

Celery/Redis task queues (CON's vaporware pattern; no need exists); any ORM import inside
`schemas/`/contracts (DB-free rule); paramiko/netmiko/ansible-runner and any remote-exec library
in collector/verifier packages (NN AST guards will enforce); LangGraph for Gate ≤4 (deterministic
pipelines don't need it; reconsider only at Gate 13 with justification); Docker-Desktop-only
kernel behaviors as correctness assumptions (EVL lesson); `:latest` anything; sentence-transformers
at import time in core paths (heavy optional extra, as NN correctly does).

## 10. Gate 2 priorities

**Gate 4 lab decision (owner-approved, recorded):** the first vertical slice uses a **minimal
two-router FRR eBGP lab derived from NeuroNOC's lab**: `router_a`, `router_b`, one point-to-point
link, one eBGP session, with optional loopbacks or advertised prefixes only where necessary to
verify route restoration. The four-router NeuroNOC topology is retained as a **later
backend/profile** for blast-radius, multi-hop, and topology-generalization experiments. Gate 4's
BGP remote-AS-mismatch scenario runs on this two-router lab, with STA's `bgp_asn_mismatch.py`
lifecycle logic re-expressed for FRR.

### Wave A — first FRR vertical-slice requirements (Gate 2 inspects these first)

| Requirement | Capability rows | Primary source files |
|---|---|---|
| Bounded command execution | C02 | NN `backend/app/lab/collector.py` (runner + allow-list) |
| Minimal FRR lab definition | C04 (two-router derivation) | NN `infra/lab/docker-compose.lab.yml`, `lab.sh` |
| Topology/addressing contract inputs | C08 | CC `domain/fabric.py` |
| BGP remote-AS fault lifecycle | C10 | STA `faults/bgp_asn_mismatch.py` (re-expressed for FRR) |
| Onset/recovery checks | C13, C14 | STA `wait_for_state` pattern; EVL `checks.py` (minus ping floor) |
| BGP/interface/reachability/route evidence | C15, C18, C25, C26, C27 | CC `evidence/tools.py`; NN collector parsers; CC `syslog.py` |
| Undo/cleanup | C11 | CC `chaos/ledger.py` |
| Incident provenance/manifests | C33 (new builder), C35, C57 | CC `datasets/manifest.py`, `emit_manifest*.py` patterns |
| Structured logging | C56 | CC `observability/logging.py` |
| Security boundary tests | C63 | NN AST-scan tests |

### Wave B — later harvest (Gate 2 documents; migration deferred to their gates)

| Item | Capability rows | Gate |
|---|---|---|
| SONiC DB collectors | C19, C20, C22, C24 | Gate 5+ |
| ASIC/ACL validation | C23A, C23B, C29 | Gate 5+ |
| Batfish | C30 | Gate 5+ |
| Dataset splitting | C34, C35 (dataset side), C36 | Gate 6 |
| Approval binding | C54 | with remediation-bearing scenarios |
| Telemetry | C28 (with C07 SRL backend) | Gate 5+ |
| Model/Ollama adapter | C48 | Gate 8 (only post-truth explanation before that) |

Also in Gate 2 scope: symbol-level dependency and side-effect inventory per selected file, exact
adapter interfaces each harvested symbol needs, and provenance requirements per file.

## 11. Open questions

1. ~~Gate 4 lab choice~~ — **RESOLVED (owner decision):** minimal two-router FRR eBGP lab derived
   from NeuroNOC (see §10); four-router topology retained as a later profile.
2. Shared exec primitive destination (`common/` vs `labs/`) — Gate 3 decision; both defensible.
3. Does VerifiedNet adopt CC's Claim/Predicate vocabulary into the VerificationCheck contract, or
   define a superset? (Gate 3.)
4. `docker-sonic-vs` upstream tag/digest to pin for the rebuilt `-fixed` image (needs a build
   experiment — Gate 2 can specify, Gate 5+ executes).
5. Whether NN's routed-lab FRR image can be digest-pinned without behavior change (expect yes).

## 12. Claims not yet proven & classification totals

**Not yet proven:** (a) runnability of any harvested code inside VerifiedNet — all reuse
classifications are static-analysis judgments until Gate 3+ tests run; (b) SIA/STA/EVL/AVH behavior
in a pinned fresh environment (no lockfiles); (c) that CC's prechecks wire cleanly into a live loop
(never done in CC — ADR-004); (d) that the EVL EVPN lab converges deterministically once sleeps are
replaced with polling (LinuxKit variance documented in its README); (e) Batfish verdict stability
across Batfish versions (unpinned today); (f) the STA "LoRA improves RCA" hypothesis (its own eval
honestly showed 0% both arms at n=6 — no performance claims carried forward).

**Classification totals — counted directly from the 66 master-table rows (C01–C65, with C23 split
into C23A/C23B):**

| Classification | Count | Rows |
|---|---|---|
| DIRECT_REUSE | 6 | C17, C23A, C31, C35, C54, C56 |
| REFACTOR_AND_REUSE | 40 | C02–C08, C10–C15, C18–C20, C22, C23B, C24–C30, C34, C36–C40, C45, C47–C50, C52, C57, C61, C63 |
| WRAP_WITH_ADAPTER | 0 | — |
| DESIGN_REFERENCE_ONLY | 13 | C01, C09, C16, C33, C41, C42, C53, C55, C58, C59, C62, C64, C65 |
| NO_ACCEPTABLE_SOURCE | 7 | C21, C32, C43, C44, C46, C51, C60 |
| Total capability rows | 66 | |

REJECT / RETAIN_ONLY_IN_ORIGINAL_REPO are recorded at item level in §6 (20 items), not as
capability rows. Per-symbol classifications total ≈96 symbols across the master table and §6.

