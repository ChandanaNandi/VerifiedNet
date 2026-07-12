# Part 1 — Executive Summary

`sonic-troubleshooting-agent` is a **local, single-host, LLM-assisted network-fault-diagnosis harness** built around a SONiC virtual switch running in Docker. It injects one of three reversible faults, collects structured evidence from the switch's Redis config/state/counter databases plus `vtysh` and syslog, fans out to four prompt-specialized LLM "agents" that post hypotheses to a shared in-memory container ("blackboard"), and then fans in to a fifth LLM call that narrates a diagnosis as JSON on stdout. A separate "Phase 4" directory (`fine_tuning/`) adds a genuinely-runnable Hugging Face + PEFT LoRA training script and an RCA-generation evaluation harness with five metrics.

**Verdict: this is a real, working, honest, well-documented project — not a demo of stubs.** I ran every smoke-test path (`train_lora.py`, `baseline_predict.py`, `evaluate_rca.py`, `build_dataset_from_runs.py`, `blackboard.py`) and all executed successfully and produced real artifacts. There is **zero** dead code, no `TODO`/`FIXME`/`NotImplemented`/`mock`/`pass #` anywhere in the Python or shell (the single "hardcode" grep hit is a comment). The one thing I could **not** execute end-to-end is the live troubleshooting loop, because it requires the `docker-sonic-vs-fixed:latest` image (built in a companion repo) and a local Ollama server — neither present in this environment. That path is however fully wired, internally consistent, and cross-referenced against captured spike-findings docs.

The project's dominant strength is **engineering honesty and discipline**: defensive deep-copy isolation on the blackboard, fail-loud collectors, idempotent lab scripts with partial-state detection, an evidence filter that suppresses SONiC-VS synthetic fault noise, and a Phase-4 README that explicitly refuses to over-claim LoRA results ("did NOT improve exact root-cause accuracy … 0% for both"). Its dominant weaknesses are **scope and validation**: three hardcoded scenarios, a "blackboard" that is really a fixed fan-out/fan-in over one 7B model, **no unit tests and no CI**, a Phase-4 dataset of 10 train / 6 eval examples (partly synthetic), and no scoring harness for the live diagnosis quality.

Headline level assessment: **strong senior-level (L4/L5) individual-contributor portfolio work** in the applied-ML-for-networking niche; not staff-level systems research, and not production software.

---

# Part 2 — Architecture

## 2.1 ASCII component-and-connection diagram

```
                          $ python3 main.py --scenario <name> [--dry-run|--keep-fault]
                                           │
                                           ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  main.py  (ORCHESTRATOR / RUNNER)                                              │
 │  - SCENARIOS registry: dict[str, Scenario dataclass]                          │
 │  - parse_args(): --scenario is REQUIRED (no silent default)                   │
 │  - stdout = diagnosis JSON only ; stderr = all progress/section headers       │
 └───────┬───────────────┬───────────────┬───────────────┬──────────────┬────────┘
         │               │               │               │              │
   (if requires_bgp_lab) │               │               │              │(finally:)
         ▼               ▼               ▼               ▼              ▼
 ┌───────────────┐  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐
 │scripts/       │  │take_     │  │faults/<s>. │  │Blackboard  │  │faults/<s>.   │
 │configure_bgp  │  │snapshot()│  │inject()    │  │(shared     │  │restore()     │
 │.sh up  (subp) │  │4 collect.│  │  (subp)    │  │ workspace) │  │+ bgp.sh down │
 └──────┬────────┘  └────┬─────┘  └─────┬──────┘  └─────┬──────┘  └──────┬───────┘
        │ docker         │ docker exec  │ docker exec   │               │ docker
        ▼                ▼              ▼               │               ▼
 ┌────────────────────────────────────────────────┐    │        (test cleanup,
 │ Docker containers                               │    │         NOT remediation)
 │  ┌────────────────────────┐  ┌───────────────┐  │    │
 │  │ sonic-vs-troubleshoot  │  │ sonic-bgp-peer│  │    │
 │  │ (System Under Test)    │◄─┤ frrouting/frr │  │    │
 │  │  redis: CONFIG_DB(4)   │  │ BGP AS 65001  │  │    │
 │  │         APP_DB(0)      │  │ (test fixture,│  │    │
 │  │         COUNTERS_DB(2) │  │  NEVER read   │  │    │
 │  │  FRR/vtysh AS 65000    │  │  by agents)   │  │    │
 │  │  /var/log/syslog       │  └───────────────┘  │    │
 │  └────────────────────────┘  net: sonic-bgp-lab│    │
 │        10.10.10.3               10.10.10.2      │    │
 └────────────────────────────────────────────────┘    │
        ▲ collectors/sonic_state.py                     │
        │ (redis-cli / vtysh / tail via docker exec)    │
        │                                               ▼
        │                                   ┌──────────────────────────┐
        │  evidence dict ──(evidence_filter)┤ Blackboard._evidence     │
        │                                   │ Blackboard._hypotheses[] │
        │                                   │ Blackboard._diagnosis    │
        │                                   └───────────┬──────────────┘
        │                            fan-out (ThreadPoolExecutor, 4 workers)
        │            ┌──────────────┬───────────────┬───────────────┐
        │            ▼              ▼               ▼               ▼
        │      agents/triage  agents/interface agents/bgp     agents/logs
        │      (complaint     _specialist      _specialist    _specialist
        │       only)         (iface_state+    (bgp_summary)  (recent_logs)
        │                      counters)                          │
        │            └──────────────┴───────────────┴─────────────┘
        │                    each add_hypothesis("[tag] …")
        │                              │  fan-in
        │                              ▼
        │                    agents/diagnosis.produce_diagnosis(bb)
        │                              │  HTTP POST /api/chat (urllib)
        │                              ▼
        │                    ┌──────────────────────┐
        └────────────────────┤ Ollama localhost:11434│  qwen2.5:7b-instruct (ALL 5 calls)
                             └──────────┬───────────┘
                                        ▼
                              stdout: {diagnosis, model, evidence_summary, raw_response}


  ── SEPARATE, DECOUPLED SUBSYSTEM (offline; no Docker, no Ollama) ──
 ┌───────────────────────────────────────────────────────────────────┐
 │ fine_tuning/  (Phase 4 — LoRA + RCA eval)                          │
 │  schemas.py  ── load/validate/format/score/normalize (no deps)     │
 │  train_lora.py ─ HF Transformers + PEFT LoRA (Qwen2.5-0.5B)        │
 │  baseline_predict.py ─ base-model greedy generation                │
 │  evaluate_rca.py ─ 5 metrics base vs LoRA → results/*.json,*.md    │
 │  build_dataset_from_runs.py ─ imports main.py to capture real      │
 │                               evidence → labeled JSONL             │
 │  data/train.jsonl(10)  data/eval.jsonl(6)                          │
 └───────────────────────────────────────────────────────────────────┘
```

## 2.2 Every component and every connection

- **`main.py` (orchestrator).** Owns the `SCENARIOS` registry (three `Scenario` frozen dataclasses), CLI parsing, the BEFORE→inject→AFTER→populate→fan-out→fan-in→restore sequence, stdout/stderr discipline, and a `try/finally` that guarantees restore and BGP-lab teardown. Connects to: fault modules (function references), `scripts/configure_bgp.sh` (subprocess), collectors (function calls), `Blackboard`, four specialist functions (threads), and `agents.diagnosis` (function call → HTTP).
- **`Scenario` dataclass (`main.py:238`).** Metadata-driven dispatch: `inject`/`restore` callables, `user_complaint`, `interface`, `requires_bgp_lab`, `evidence_filter`, `post_inject_delay_seconds`, `manual_restore_command`. This is the extensibility seam — a new scenario is a new dict entry + a new `faults/<name>.py`.
- **`collectors/sonic_state.py`.** Four pure-Python collectors: `collect_interface_state` (CONFIG_DB db4 + APP_DB db0), `collect_interface_counters` (COUNTERS_DB db2 SAI stats via `COUNTERS_PORT_NAME_MAP`→oid→`COUNTERS:<oid>`), `collect_bgp_summary` (`vtysh show bgp summary json`), `collect_recent_logs` (`tail /var/log/syslog`). All go through `_docker_exec`. Failures return `{"error": …}` rather than raising.
- **`faults/*.py`.** Three reversible fault injectors, each with `inject`/`restore` (+ `status` for BGP), preconditions, apply, and poll-until-converged verification. Connect to the SUT via `docker exec` (config CLI or `vtysh`).
- **`blackboard/blackboard.py`.** In-memory container: `_user_complaint`, `_evidence` dict, `_hypotheses` list, `_diagnosis` (set-once). Deep-copy on every read and write. No LLM, no scheduling, no locks.
- **`agents/{triage,interface,bgp,logs}_specialist.py`.** Four near-identical modules; each builds a scoped prompt from its evidence slice, POSTs to Ollama, parses `HYPOTHESIS:`/`CONFIDENCE:` lines, and calls `blackboard.add_hypothesis` with a `[tag]` prefix.
- **`agents/diagnosis.py`.** Fan-in narrator: serializes the whole blackboard into one prompt, POSTs to Ollama, returns model text **verbatim** plus audit metadata. Does not parse or verify the diagnosis.
- **`scripts/bringup.sh`.** Recreates the SUT container from `docker-sonic-vs-fixed:latest`, gates on redis PONG → `start.sh` EXITED → `PORT|Ethernet4` present → `bgpd` RUNNING.
- **`scripts/configure_bgp.sh`.** `up`/`down`/`status` for the two-container BGP lab, with partial-state detection and idempotency.
- **`fine_tuning/`.** Decoupled offline subsystem (see Part 6). Its only coupling to the live system is `build_dataset_from_runs.py` importing `main.py` to reuse the inject/snapshot/restore building blocks.
- **External dependencies (not in repo):** Docker, the `docker-sonic-vs-fixed:latest` image (companion repo `sonic-intent-agent`), Ollama on `localhost:11434` with `qwen2.5:7b-instruct`, and (Phase 4 only) torch/transformers/peft.

---

# Part 3 — Repository Structure

40 files, ~3,755 lines of Python across 15 modules, plus ~3,300 lines of Markdown design docs and two Bash scripts.

| Path | Role | Owns |
|---|---|---|
| `main.py` (539 LOC) | **Orchestration / business logic** | Scenario registry, run sequence, stdout/stderr contract, evidence filters, dry-run planner |
| `collectors/sonic_state.py` (298) | **Networking-state I/O / storage read** | Redis DB access (CONFIG/APP/COUNTERS), vtysh, syslog; structured evidence dicts |
| `faults/interface_admin_down.py` (200) | **Networking mutation** | Interface admin shutdown via `config` CLI, poll verification |
| `faults/bgp_neighbor_removal.py` (269) | **Networking mutation** | `no neighbor` via vtysh, reachability guard on restore |
| `faults/bgp_asn_mismatch.py` (327) | **Networking mutation** | remote-as change + `clear bgp` reconvergence |
| `blackboard/blackboard.py` (209) | **Storage / shared state** | Evidence/hypotheses/diagnosis with deep-copy isolation, set-once diagnosis, validation |
| `agents/triage.py` (139) | **AI logic** | Complaint-only hypotheses |
| `agents/interface_specialist.py` (144) | **AI logic** | Interface-scoped hypotheses |
| `agents/bgp_specialist.py` (137) | **AI logic** | BGP-scoped hypotheses |
| `agents/logs_specialist.py` (137) | **AI logic** | Log-scoped hypotheses |
| `agents/diagnosis.py` (231) | **AI logic (fan-in)** | Ollama narration, audit metadata |
| `scripts/bringup.sh` | **Infra / fixture** | SUT container lifecycle + readiness gates |
| `scripts/configure_bgp.sh` | **Infra / fixture** | Two-container BGP lab up/down/status |
| `fine_tuning/schemas.py` (268) | **Evaluation core (no deps)** | JSONL IO, schema validation, prompt formatting, scoring, root-cause normalization |
| `fine_tuning/train_lora.py` (144) | **AI training** | PEFT LoRA fine-tune + smoke-test |
| `fine_tuning/baseline_predict.py` (138) | **Evaluation** | Base-model generation + deterministic smoke predictor |
| `fine_tuning/evaluate_rca.py` (215) | **Evaluation harness** | 5 metrics, base-vs-LoRA, results JSON/MD |
| `fine_tuning/build_dataset_from_runs.py` (360) | **Data engineering** | Real-evidence→labeled-JSONL (live/from-snapshot/smoke) |
| `fine_tuning/data/*.jsonl` | **Data** | 10 train / 6 eval RCA examples |
| `phase1..3/*.md` (11 docs, ~3,300 LOC) | **Design/decision/findings** | Spike evidence, ADRs, phase plans |
| `Dockerfile.sonic-fixed` | **Infra** | 4-line derivation of base SONiC VS image adding `sudo` |
| `LICENSE` | MIT (2026, Chandana Nandi) | — |

Notable structural facts: **no `tests/` directory, no `.github/` CI, no top-level `requirements.txt`** (the live system is stdlib-only by design; only Phase 4 has `fine_tuning/requirements.txt`). Packages lack `__init__.py`; imports work via `sys.path.insert` in `main.py:61` and in each script's `__main__` guard.

---

# Part 4 — Complete Execution Flow

Tracing `python3 main.py --scenario interface_admin_down` (and noting the BGP branches):

1. **Import time (`main.py:60-75`).** `REPO_ROOT` computed, inserted on `sys.path`. Imports the four specialist functions, `produce_diagnosis`/`DiagnosisError`, `Blackboard`, the four collectors, and the three fault modules.
2. **`parse_args()` (`main.py:377`).** `--scenario` is `required=True` with `choices=sorted(SCENARIOS)`. Bare `python3 main.py` → argparse error, exit 2. `scenario = SCENARIOS[args.scenario]`.
3. **`--dry-run` branch (`main.py:418`).** Calls `run_dry_run(scenario)` which prints the numbered plan to stderr and returns 0. **No mutation, no Ollama, no collectors, no Docker.** Verified by reading `run_dry_run` (`main.py:313`).
4. **Container gate (`main.py:422`).** `is_container_running("sonic-vs-troubleshoot")` runs `docker ps --filter name=… --format {{.Names}}`. If absent → stderr error + exit 2.
5. **BGP lab up (only if `requires_bgp_lab`).** For `interface_admin_down` this is skipped. For the BGP scenarios, `_run_configure_bgp("up")` → `subprocess.run([configure_bgp.sh, "up"], timeout=180)`; the script creates network `sonic-bgp-lab` (10.10.10.0/24), starts `sonic-bgp-peer` from `frrouting/frr:latest`, flips `bgpd=no→yes`, `docker restart`, waits for `bgpd`, configures peer BGP (AS 65001 → neighbor 10.10.10.3 AS 65000), connects the SUT at 10.10.10.3, configures SUT BGP (AS 65000 → neighbor 10.10.10.2 AS 65001), polls up to 60 s for `Established`. Non-zero exit → runner exit 7.
6. **BEFORE snapshot (`main.py:443`).** `take_snapshot("Ethernet4")` calls all four collectors. Each issues `docker exec sonic-vs-troubleshoot …`:
   - `collect_interface_state`: `redis-cli -n 4 EXISTS PORT|Ethernet4` → `HGET … admin_status` → `redis-cli -n 0 HGET PORT_TABLE:Ethernet4 oper_status`.
   - `collect_interface_counters`: `redis-cli -n 2 HGET COUNTERS_PORT_NAME_MAP Ethernet4` → `HGETALL COUNTERS:<oid>` → parse SAI fields.
   - `collect_bgp_summary`: `vtysh -c "show bgp summary json"` → parse `ipv4Unicast`/`ipv6Unicast` peers.
   - `collect_recent_logs(20)`: clamps line count to [0,500], `sh -c 'if [ ! -f … ]; then echo __SYSLOG_MISSING__; … tail -n 20 …'`.
   `print_snapshot(before, "BEFORE")` writes one-line per-collector summaries to stderr.
7. **INJECT (`main.py:446`).** `_call_with_stdout_to_stderr(scenario.inject)` runs `interface_admin_down.inject()` with its stdout captured and re-emitted to stderr. `inject` checks preconditions, reads admin status, runs `config interface shutdown Ethernet4` inside the container, then `wait_for_admin_status("down", timeout=2.0)` polling `HGET` every 50 ms (accommodating CONFIG_DB read-after-write lag). Raises `FaultInjectionError` on mismatch. `injected = True`.
8. **Sleep (`main.py:450`).** `time.sleep(1.0)` (`post_inject_delay_seconds`).
9. **AFTER snapshot (`main.py:452`).** Same four collectors; `print_snapshot(after, "AFTER")`.
10. **Evidence filter (`main.py:455`).** For `interface_admin_down`, `_admin_down_evidence_filter` runs `_filter_logs_for_interface`: keeps only syslog lines containing `Ethernet4`, then drops lines containing `oper error event:` (the SONiC-VS synthetic `mac_local_fault`/`fec_sync_loss` cascade). BGP scenarios have `evidence_filter=None`. The BEFORE/AFTER stderr summaries see raw output; only the blackboard sees the filtered view.
11. **Populate blackboard (`main.py:462`).** `bb = Blackboard(user_complaint)`; loop `bb.add_evidence(name, data)` (deep-copies each collector dict).
12. **Fan-out (`main.py:473-490`).** `ThreadPoolExecutor(max_workers=4)` submits the four specialist functions, each receiving the same `bb`. Each specialist: `bb.to_dict()` (deep copy) → build scoped prompt → `_call_ollama` (urllib POST `/api/chat`, `stream:false`, `temperature:0.2`, 60 s timeout) → `_parse_hypotheses` → `bb.add_hypothesis("[tag] …", confidence, supporting_evidence)`. `as_completed` prints `"<name>: posted hypotheses"` or `"<name>: failed (<exc>)"`. **Individual failures are non-fatal.**
13. **Fan-in (`main.py:492`).** `produce_diagnosis(bb)` serializes complaint + evidence + hypotheses into one user message under a NARRATOR system prompt, POSTs to Ollama, extracts `message.content`, and returns `{diagnosis, model, evidence_summary, raw_response}`. `DiagnosisError` → exit 3.
14. **Emit (`main.py:499`).** `print(json.dumps(diagnosis, indent=2))` to **stdout** — the only stdout write on the happy path.
15. **`finally` (`main.py:506`).** If injected and not `--keep-fault`: `restore()` (bring Ethernet4 back up, poll to `up`). BGP scenarios also run `configure_bgp.sh down`. `--keep-fault` instead prints manual cleanup commands. Restore failure → exit 4 (if not already set).
16. **Return exit code.** 0 success; 1 unexpected; 2 no container/argparse; 3 diagnosis; 4 restore/teardown; 7 BGP-lab-up.

**Phase-4 offline flow** (independent): `train_lora.py` loads `train.jsonl`, `validate_dataset`, then either smoke-writes `SMOKE_TEST.json` or loads Qwen2.5-0.5B, applies `LoraConfig(r=8,alpha=16, target_modules=[q,k,v,o,gate,up,down]_proj)`, tokenizes with prompt-masked labels (`-100`), trains via HF `Trainer`, saves adapter. `evaluate_rca.py` generates base + LoRA greedy predictions (or loads prediction files), scores via `schemas.score_prediction`, writes `eval_results.json` + `eval_summary.md`.

---

# Part 5 — Networking Concepts

| Concept | Where | Protocol/Mechanism | How implemented |
|---|---|---|---|
| **SONiC architecture (Redis-backed control plane)** | `collectors/sonic_state.py` | Redis DBs inside container | CONFIG_DB (db4, `PORT|<name>`), APP_DB (db0, `PORT_TABLE:<name>`), COUNTERS_DB (db2) queried via `redis-cli` over `docker exec` |
| **Interface admin vs oper state** | `collect_interface_state`, `faults/interface_admin_down.py` | SONiC config model | `admin_status` from CONFIG_DB, `oper_status` from APP_DB; SONiC convention that missing admin_status ⇒ "up" |
| **SAI port counters** | `collect_interface_counters` | SAI stat keys | `COUNTERS_PORT_NAME_MAP` name→oid indirection, then `SAI_PORT_STAT_IF_IN/OUT_UCAST_PKTS/ERRORS/DISCARDS` |
| **BGP (eBGP peering)** | `configure_bgp.sh`, `collect_bgp_summary`, both BGP faults | BGP-4 via FRR | SUT AS 65000 ↔ peer AS 65001 over 10.10.10.0/24; state read from `show bgp summary json` |
| **BGP FSM states** | `bgp_asn_mismatch.py`, `bgp_neighbor_removal.py` | BGP finite state machine | Categorizes Established/Idle/Active/Connect/OpenSent/OpenConfirm; ASN-mismatch drives Established→Idle |
| **BGP ASN mismatch / OPEN error** | `faults/bgp_asn_mismatch.py` | BGP OPEN / NOTIFICATION | Sets wrong `remote-as` (65002); documented Bad-Peer-AS (0202) NOTIFICATION though collector only reads FSM state |
| **BGP neighbor teardown** | `faults/bgp_neighbor_removal.py` | FRR config | `no neighbor 10.10.10.2`; empty summary `{}` categorized as "removed" |
| **BGP reconvergence / backoff** | `bgp_asn_mismatch._apply_restore` | `clear bgp <peer>` | `clear` forces ~2 s reconvergence vs ~15 s under deep connect-retry backoff (per `2D_ASN_MISMATCH_RESTORE_FINDINGS.md`) |
| **FRR / vtysh / watchfrr** | `configure_bgp.sh` | FRR daemon model | Enables `bgpd=yes` in `/etc/frr/daemons`, restarts under tini, verifies watchfrr supervises bgpd |
| **ICMP reachability** | `_peer_reachable` (both BGP faults) | ICMP echo | `ping -c 1 -W 1 10.10.10.2` guards restore against a missing fixture |
| **Docker networking** | `configure_bgp.sh` | Bridge network + static IPs | `docker network create --subnet`, `--ip`, `network connect/disconnect` |
| **Syslog observability** | `collect_recent_logs` | File tail | `/var/log/syslog` tail with missing-file sentinel |
| **Read-after-write consistency** | `wait_for_admin_status` | Polling | 50 ms polling to absorb 60–80 ms CONFIG_DB propagation lag |

The system-under-test / test-fixture separation is a genuine networking-test-methodology concept: `configure_bgp.sh` and the peer container are explicitly excluded from what the agents observe (`main.py:26-28`, `configure_bgp.sh:31-36`).

---

# Part 6 — AI Concepts

**Actually present:**

- **Multi-agent fan-out/fan-in (real).** `main.py:480` `ThreadPoolExecutor(max_workers=4)` genuinely runs four LLM calls concurrently; a fifth synthesizes. Verified in code.
- **Blackboard pattern (partial / "inspired").** `blackboard/blackboard.py` is a real shared-state container with evidence + hypotheses + diagnosis, deep-copy isolation, and set-once diagnosis. It is **not** an opportunistic blackboard scheduler — there is no control component picking knowledge sources by activation; the specialist set is fixed and invoked once. The README and `phase3/README.md` state this honestly ("not a full opportunistic blackboard scheduler").
- **Prompt-based specialization / role prompting.** All five calls hit the same `qwen2.5:7b-instruct`; "specialization" = distinct system prompts + evidence slices (`agents/*_specialist.py`). Explicitly acknowledged in README.
- **Grounding (prompt-enforced).** Every specialist and the diagnosis prompt demand quoting specific evidence fields ("admin_status=\"down\""); each specialist is scoped to one evidence slice. Grounding is enforced by **instruction only** in the live path — there is no code that rejects an ungrounded diagnosis.
- **Attribution / provenance.** Hypotheses are prefixed `[triage]`/`[interface]`/`[bgp]`/`[logs]` (e.g. `agents/bgp_specialist.py:134`) so the synthesizer can weigh agreement and flag contradictions.
- **Confidence modeling.** Specialists emit `high/medium/low` mapped to `0.8/0.5/0.2` (`_CONFIDENCE_MAP`); stored per hypothesis.
- **Safety framing.** The diagnosis system prompt forbids recommending remediation and forbids inventing facts; the runner repeatedly separates "diagnosis" from "remediation." Fault restore is framed as test cleanup, not autonomous action.
- **LoRA fine-tuning (real, runnable).** `train_lora.py` uses PEFT `LoraConfig` (r=8, α=16, dropout=0.05, 7 target projections), prompt-masked causal-LM labels, HF `Trainer`. Not a stub — the import-guarded real path is complete; only the model download is deferred.
- **Instruction tuning / supervised RCA generation.** `schemas.format_prompt`/`format_target` build `### Instruction/### SONiC Evidence/### Response` prompts with JSON targets.
- **Evaluation harness with hallucination detection.** `evaluate_rca.py` + `schemas.score_prediction` compute root-cause accuracy (normalized exact match with an alias table), JSON schema validity, evidence-grounding (token/`contains`/`=`-value heuristic in `_evidence_item_supported`), **hallucination rate (grounding < 0.75)**, and latency.
- **Data engineering / weak supervision from ground truth.** `build_dataset_from_runs.py` derives grounded-evidence strings and known root-cause labels from the deliberately injected fault.

**Claimed-adjacent but NOT present (be explicit):**

- **RAG / embeddings / vector store:** **Not implemented.** No retrieval, no embeddings anywhere. Evidence is directly injected into the prompt.
- **Tool calling / function calling:** **Not implemented in the live path.** `agents/diagnosis.py:14-16` explicitly notes it chose raw urllib precisely because Phase 1 does "not use tool-calling." Collectors are Python calls, not model-invoked tools.
- **Planning / ReAct / iterative reasoning:** **Not implemented.** The flow is a single fixed pass; the model never decides what to collect next. The narrator prompt actively forbids proposing next steps.
- **Programmatic hallucination/verification in the live diagnosis:** **Not implemented** — the diagnosis is returned verbatim (`agents/diagnosis.py:116`), unverified. Hallucination scoring exists **only** in the offline Phase-4 eval.
- **Cross-agent debate/critique:** **Not implemented** — no agent reads another's hypotheses; only the final synthesizer does.

---

# Part 7 — Software Engineering

**Strengths.**
- **Modularity & separation of concerns.** Clean layering: orchestration (`main.py`) / networking mutation (`faults/`) / state read (`collectors/`) / shared state (`blackboard/`) / AI (`agents/`) / offline eval (`fine_tuning/`). The `Scenario` dataclass is a well-chosen extensibility seam.
- **Error handling.** Collectors never raise — they return `{"error": …}` so "this collector failed" becomes evidence. Faults raise typed `FaultInjectionError` with actionable messages. `main.py`'s `try/finally` guarantees restore/teardown. Distinct exit codes (1/2/3/4/7).
- **Defensive programming.** Blackboard deep-copies on read and write, validates types/ranges, enforces set-once diagnosis (`blackboard.py:97`). `collect_recent_logs` clamps the tail count before shell interpolation (injection defense). Fault verification polls rather than assuming synchronous writes.
- **Config.** Constants centralized per module (container name, DB numbers, timeouts, ASNs, IPs). Scripts read env overrides (`CONTAINER`, `IMAGE`, `READY_TIMEOUT_SECONDS`).
- **Reproducibility of infra.** `bringup.sh` and `configure_bgp.sh` are idempotent with explicit readiness gates and partial-state detection — unusually rigorous for lab scripts.
- **Logging/observability discipline.** Strict stdout(JSON)/stderr(everything) split so diagnosis pipes cleanly (`… | jq -r .diagnosis`).
- **Documentation.** Module docstrings are exceptional — they explain *why*, cite spike-findings docs, and record deliberate trade-offs. 11 phase docs form a real decision record.
- **Dependency management.** Live system is stdlib-only (no supply chain); Phase 4 deps are isolated in `fine_tuning/requirements.txt` with version floors. `.gitignore` covers venvs, caches, large image tarballs, and eval intermediates.
- **Honesty tooling.** Smoke tests write to `smoke-test`/`smoke` subpaths so they never clobber real artifacts (`train_lora.py:60`, `evaluate_rca.py:179`); `write_summary` embeds "Do not claim LoRA improvement …".

**Weaknesses.**
- **No automated tests.** Zero `tests/`, zero pytest. "Tests" are inline `__main__` smoke blocks (`blackboard.py`, `diagnosis.py`, collectors). No assertions on collectors/faults/agents; no mock of `docker`/Ollama. The most testable pure logic — `_parse_hypotheses`, `_filter_logs_for_interface`, `extract_json_object`, `normalize_root_cause`, `_evidence_item_supported` — is entirely untested.
- **No CI/CD.** No `.github/`, no lint/type-check/format gate. Type hints are present but never checked (no mypy config).
- **Code duplication.** The ~70-line Ollama block (`OLLAMA_URL`, `_call_ollama`, `_parse_hypotheses`, `SpecialistError`, `_CONFIDENCE_MAP`) is copy-pasted verbatim across all four specialists. It's a *documented deliberate* choice (session-scoping) but is real debt: a bug fix must be applied four times.
- **No packaging.** No `__init__.py`, no `pyproject.toml`/`setup.py`; imports rely on `sys.path.insert`. Not `pip install`-able.
- **External coupling.** The live system cannot run without an out-of-repo Docker image and a running Ollama; there is no containerized/mocked path to exercise the full loop in CI.
- **Concurrency without explicit synchronization.** Fan-out mutates a shared `Blackboard` from four threads with no lock (relies on CPython GIL making `list.append` atomic). Correct today, but undocumented as a GIL dependency and fragile to future refactors.

---

# Part 8 — Research Quality

If submitted to NeurIPS/ICLR/NSDI/SIGCOMM/OSDI/SOSP, this would be **rejected as a research paper** but is respectable as an engineering artifact/workshop demo. Reviewers would say:

**Praise.**
- Realistic, reproducible testbed (SONiC VS + FRR + injected reversible faults) with ground-truth labels — a genuinely useful evaluation substrate.
- Intellectual honesty: negative LoRA results reported plainly (0% root-cause accuracy both base and LoRA); metrics defined precisely; heuristic limitations disclosed.
- Grounding-first framing (LLM as narrator over collected evidence, not free generator) is a sensible hallucination-mitigation stance.

**Criticism (fatal for a top venue).**
- **No baselines.** No comparison to a deterministic rule-based diagnoser (which for these three faults would trivially hit ~100%), no comparison to a larger model, no prompt-only vs multi-agent ablation. There is no evidence the multi-agent blackboard beats a single grounded prompt.
- **No ablations.** Does fan-out help vs a single diagnosis call? Does the `[tag]` attribution matter? Does temperature/model size matter? Unmeasured.
- **No statistical evaluation.** n=3 live scenarios; Phase-4 eval n=6 with no seeds, no variance, no confidence intervals. Percentages over 6 examples are noise.
- **No task difficulty.** Three faults, each near-deterministically identifiable from a single field (`admin_status=down`, empty BGP summary, `remoteAs=65002`). This under-tests LLM reasoning; a `grep` would diagnose most.
- **No live-diagnosis metric.** The README concedes there is "no detection/localization scoring harness" for the live runner — so the headline multi-agent system is unquantified.
- **Related work is thin** (five reference points, some unverified) and there's no formal problem statement or novelty claim.

Verdict: a solid **systems/ML engineering demonstration**, not a research contribution. Closest fit would be a demo/poster at an applied-AI-for-networking workshop.

---

# Part 9 — Hiring Committee Review

**Would it impress NVIDIA/Cisco/Arista/Juniper/Azure/GCP Networking/Meta Infra?** Yes, materially — for an ML-infra / network-automation / NOS-tooling role, this is an above-average portfolio project because it demonstrates the exact intersection those teams hire for.

**Skills demonstrated (evidenced):**
- Deep SONiC internals: Redis DB layout (CONFIG/APP/COUNTERS), SAI counter indirection, `bgpcfgd` vs `vtysh` trade-off decision (`2C_CONTROL_PLANE_DECISION.md`), supervisord/FRR/watchfrr process models.
- Real BGP operational knowledge: FSM states, OPEN/NOTIFICATION on ASN mismatch, `clear`-driven reconvergence under backoff — backed by measured spikes.
- Distributed-systems hygiene: read-after-write polling, idempotent fixtures, partial-state detection, deep-copy isolation, deterministic exit codes.
- Applied LLM engineering: multi-agent orchestration, prompt design, PEFT/LoRA training, evaluation-metric design with hallucination detection, and — crucially — the judgment to report a negative result honestly.
- Communication: exemplary docstrings and ADR-style decision records.

**Level calibration.** The individual artifacts are **senior (L5)**-quality in craft and judgment (the ADRs, the honesty about scope, the fixture rigor). The *scope* is **new-grad/L3-to-L4** (three hardcoded scenarios, no tests, no CI, single host, one project of a two-project portfolio). Net: this reads as a **strong L4 / borderline-L5 IC** signal — a mid-to-senior engineer who writes carefully, documents decisions, and doesn't oversell. It would **not** by itself signal staff (no cross-team system design, no production hardening, no scale/experimental rigor). For an intern/new-grad it would be a standout; for a senior/staff candidate it's a good supporting sample that needs the tests/CI/baselines gap addressed in interview.

---

# Part 10 — Weaknesses (brutally honest)

1. **No tests, no CI — the single biggest gap.** Pure, deterministic, high-value logic (`_parse_hypotheses`, `_filter_logs_for_interface`, `extract_json_object`, `normalize_root_cause`, `_evidence_item_supported`, blackboard validation) is untested. For a project whose selling point is *reliability and honesty*, the absence of a test suite undercuts the thesis.
2. **The "blackboard" and "multi-agent" framing oversells a fixed fan-out/fan-in.** It's a shared dict written by four one-shot LLM calls to one model. No opportunistic control, no agent-to-agent interaction, no iteration. The README is honest about this, but the terminology invites over-reading.
3. **Trivial task difficulty / no baseline.** All three faults are single-field-identifiable; a 20-line rule engine would match or beat the LLM. Without that baseline, the multi-agent machinery's value is unproven — likely **overengineering** for the problem shown.
4. **Duplication debt.** Four verbatim copies of the Ollama client. Deliberate, but a maintenance liability that a shared `agents/_ollama.py` would fix without touching the "one file per specialist" spirit.
5. **No verification/grounding enforcement in the live path.** The diagnosis is returned verbatim; grounding relies entirely on prompt obedience. A hallucinating narrator would pass through unchecked. The excellent Phase-4 hallucination scorer is not wired into the live loop.
6. **Phase-4 evaluation is statistically meaningless in isolation.** 10 train / 6 eval, partly synthetic, n=6 with no seeds/variance. The authors say so — but the numbers still shouldn't be cited as evidence of anything beyond "the pipeline runs."
7. **Reproducibility cliff.** Cannot run the live system without an out-of-repo image (`docker-sonic-vs-fixed`) and Ollama; no mock/replay path for the full loop. Only the offline smoke tests are self-contained here.
8. **Scalability/extensibility ceilings.** Concurrency is unlocked GIL-dependent; scenarios are hardcoded; there's no plugin discovery for faults/collectors/specialists; single-host only.
9. **Security posture (acknowledged).** Containers run `--privileged`; no auth, no audit log beyond the in-memory blackboard, no multi-operator coordination. Shell interpolation surface is small and defended, but `--privileged` Docker + `config`/`vtysh` mutation is a real-network no-go (fine for a lab, disclosed in README).
10. **Packaging/portability.** `sys.path.insert` hacks, no `__init__.py`, not installable, no type-checking despite type hints.

---

# Part 11 — Reusable Components (for a future "NetworkGym")

**Directly reusable (lift-and-use):**
- `blackboard/blackboard.py` — clean, dependency-free shared-state container with isolation and validation. Reusable as-is as an evidence/hypothesis store.
- `collectors/sonic_state.py` — the `_docker_exec`, `_parse_redis_hgetall`, and four collectors are a solid SONiC observation library. Generalize the container name to a parameter and it's a reusable NOS-state SDK.
- `fine_tuning/schemas.py` — no-dependency JSONL IO + schema validation + prompt formatting + scoring + label normalization. Reusable across any structured-generation eval.
- `fine_tuning/evaluate_rca.py` metric core (`summarize`, `score_prediction`) — reusable eval harness for JSON-generation tasks.
- `scripts/configure_bgp.sh` and `bringup.sh` — reusable fixture-management patterns (idempotency, readiness gates, partial-state detection) for any container-lab environment.
- `Scenario` dataclass + registry pattern (`main.py`) — a good scaffold for a scenario/task registry.

**Needs rewriting/refactoring before reuse:**
- `agents/*_specialist.py` — extract the shared Ollama client into one module; parameterize model/endpoint; add a specialist base class or registry. Currently four copies.
- `agents/diagnosis.py` — factor out the same Ollama client; add optional programmatic grounding/verification hooks.
- `main.py` orchestration — split the run engine from the scenario registry and the stderr formatting so it can drive arbitrary environments, not just SONiC-VS.
- `faults/*.py` — deduplicate `_docker_exec`/precondition/poll scaffolding into a shared fault base; parameterize interface/peer/ASN constants.

**Should stay independent / project-specific:**
- The phase docs (`phase1..3`) — they're this project's decision record, not library material.
- The BGP-lab specifics (fixed IPs/ASNs, `docker-sonic-vs-fixed` image assumptions) — environment-bound.
- `Dockerfile.sonic-fixed` — trivial, tied to the companion repo's base image.

---

# Part 12 — Portfolio Positioning

**Recommendation: keep it as an independent, top-level portfolio repository — but extract a small shared library over time.**

- **Independent repo (now):** It tells a coherent story (SONiC troubleshooting agent + honest LoRA appendix) and pairs with the companion `sonic-intent-agent` as a deliberate two-project portfolio. Merging it into the companion would blur two distinct narratives (intent/verification vs troubleshooting/diagnosis).
- **Library extraction (later):** The reusable pieces in Part 11 (`blackboard`, `collectors`, `schemas`/eval core, the Ollama client once deduped) belong in a lightweight `netdiag-core`/"NetworkGym" package that both this repo and future ones depend on. That would fix the duplication and the packaging gap in one move.
- **Submodule?** No. The fixture scripts and image assumptions are too environment-specific to be a clean submodule; a versioned library dependency is the right coupling.
- **Not a monorepo merge.** Do not fold `fine_tuning/` into a generic ML repo — its value is precisely that it's grounded in *this* project's real evidence and ground truth.

To maximize portfolio value: add a `tests/` suite + a GitHub Actions CI that runs the pure-logic tests and all four smoke tests (all self-contained, no Docker/Ollama needed), and add a rule-based baseline to the Phase-4 eval. These three changes convert a "strong L4" signal into a "clear L5" one at low cost.

---

# Part 13 — Interview Questions (Staff-level, specific to this repo)

1. `main.py` mutates a single `Blackboard` from four `ThreadPoolExecutor` workers with no lock. Why is `add_hypothesis` safe today, and exactly which future change breaks that safety?
2. `Blackboard.add_evidence`/`get_evidence` deep-copy on both read and write. Quantify when this becomes a latency/memory problem, and what you'd replace it with while preserving the audit guarantee.
3. `set_diagnosis` is set-once but `add_evidence` is last-write-wins. Justify the asymmetry; when would last-write-wins on evidence corrupt an investigation?
4. The four specialists share one `qwen2.5:7b-instruct`. Defend or refute the claim that this is "multi-agent" rather than four prompts. What experiment would settle it?
5. `_filter_logs_for_interface` drops lines containing `"oper error event:"`. Argue why suppressing real syslog lines before the LLM is defensible here and where it becomes evidence tampering.
6. The diagnosis is returned verbatim with no verification. Design a programmatic grounding check that rejects an ungrounded diagnosis without introducing a second LLM call.
7. `collect_bgp_summary` derives `bgp_instance_present` from peers-or-`as`-key presence. Construct a SONiC/FRR state where this returns a wrong boolean.
8. `wait_for_admin_status` polls at 50 ms citing "60–80 ms CONFIG_DB lag." Why poll CONFIG_DB rather than APP_DB/STATE_DB to confirm the shutdown actually took effect on the data plane?
9. `bgp_asn_mismatch._apply_restore` adds `clear bgp <peer>` to cut reconvergence from ~15 s to ~2 s. Explain the FRR connect-retry backoff mechanism that makes `clear` necessary.
10. The ASN-mismatch collector reads only FSM `state`/`remoteAs`, not the Bad-Peer-AS (0202) NOTIFICATION. What diagnosis errors does this omission permit, and how would you enrich the collector?
11. `configure_bgp.sh` refuses to auto-clean partial state on `up`. Argue this design against an auto-heal alternative for a CI environment.
12. Why does `bringup.sh` gate on `start.sh` reaching `EXITED` specifically, and what race remains if you gated only on redis `PONG`?
13. The peer container is deliberately never observed by the agents. What class of real bugs does this fixture/SUT boundary hide, and is that the right call?
14. `_docker_exec` uses `check=True` + timeout. A `vtysh` command that hangs at 9.9 s vs one that fails fast produce different failure modes downstream — trace both through `collect_bgp_summary` into the blackboard into the diagnosis.
15. `_call_with_stdout_to_stderr` redirects fault-script stdout to stderr to keep stdout JSON-clean. What breaks if a fault script writes to fd 1 directly (not via Python `print`)?
16. Give a concrete injection input to `collect_recent_logs` that the `max(0, min(line_count, 500))` clamp is defending against, and prove the clamp closes it.
17. `extract_json_object` hand-rolls a brace-matching parser with string/escape tracking. Why not `json.JSONDecoder.raw_decode`? Find an input where the hand-rolled version diverges.
18. `normalize_root_cause` uses a static alias table. The README says LoRA predicts `interface_down` for `interface_admin_down` and scores it wrong. Design an accuracy metric that credits semantic proximity without becoming trivially gameable.
19. `hallucination = grounding_score < 0.75`. Derive the false-positive/false-negative behavior of this threshold on the 6-example eval and defend 0.75.
20. `_evidence_item_supported` falls back to "≥60% of ≥3-char tokens appear in haystack." Construct a hallucinated evidence string that passes this heuristic.
21. `train_lora.py` masks prompt tokens with `-100` and appends EOS to the target. What degenerate behavior appears at inference if you forget the EOS, given only 10 training rows?
22. LoRA targets all seven projection matrices at r=8. For a 0.5B model on 10 examples, argue whether that's over- or under-parameterized and what you'd change.
23. The measured run: JSON validity 0%→100% but root-cause accuracy 0%→0%. Explain mechanistically what LoRA learned and why accuracy didn't move.
24. Design the minimal experiment (data + metric + baseline) that would let you *claim* improved root-cause classification. How many labeled examples per class, and why?
25. `build_dataset_from_runs.py --live` imports `main.py` and calls its private `_run_configure_bgp`/`_call_with_stdout_to_stderr`. Critique this coupling and propose a public interface.
26. The dataset is 10 train / 6 eval with overlapping label vocab. Quantify the leakage/overfitting risk and how you'd construct a held-out set for a 9-label task.
27. Both `train_lora.py` and `baseline_predict.py` use `do_sample=False` (greedy). What does that hide about model calibration, and how would sampling change the eval metrics?
28. There is no live-diagnosis scoring harness. Design detection + localization + explanation metrics for the live multi-agent path, including a ground-truth protocol.
29. Propose a rule-based baseline for the three faults. If it hits 100%, what remaining justification exists for the LLM pipeline?
30. Fan-out is `max_workers=4` with a 60 s per-call timeout. Compute worst-case wall-clock and design a partial-result policy when one specialist times out.
31. `as_completed` prints per-specialist results in completion order. Why is that acceptable for stderr but would be a bug if it drove the diagnosis prompt ordering?
32. A specialist raises inside a thread; `future.result()` re-raises and it's logged non-fatally. What silent-degradation failure mode does this create for diagnosis quality, and how would you surface it?
33. All Ollama calls use `temperature=0.2`. Argue for per-role temperatures (triage vs synthesis) and the risk of raising the synthesizer's.
34. The system prompt forbids the narrator from recommending remediation, yet `next_steps` is a required Phase-4 output field. Reconcile the two design stances.
35. Redis DB numbers (4/0/2) are hardcoded. What breaks across SONiC versions/multi-ASIC platforms, and how would you discover them at runtime?
36. `collect_interface_counters` reports missing SAI fields as 0 and notes "flex_counter has not populated." How does a real 0 vs a not-populated 0 change an interface specialist's hypothesis, and how would you disambiguate?
37. Design the abstraction that removes the four-way Ollama duplication without violating the "one self-contained file per specialist" constraint the author set.
38. The blackboard has no timestamps/ordering on hypotheses. Add causal ordering needed to support iterative (multi-round) specialists and show the schema change.
39. To add a `route_missing_after_bgp_loss` *live* scenario (currently synthetic-only), enumerate the new fault script, collector, evidence filter, and eval-label work.
40. `bringup.sh` recreates the container every run (idempotent-by-destroy). What investigation-state or history does that destroy, and when is it the wrong default?
41. The runner's `try/finally` guarantees restore, but if `restore` raises, the BGP lab may stay up. Trace the exit-code and residual-state matrix across inject/restore/teardown failures.
42. Security: containers are `--privileged` and mutate via `config`/`vtysh`. Design the guardrails to make an agent like this safe against a *production* switch.
43. There's no persistence of runs. Design storage for reproducible investigation replay (evidence, hypotheses, model version, prompt) and its privacy implications.
44. The evidence filter runs only for `interface_admin_down`. Generalize per-scenario evidence hygiene into a composable pipeline and state the ordering hazards.
45. If you swapped Ollama qwen2.5:7b for a hosted API, what in `diagnosis.py`/specialists changes, and what new failure/rate-limit/cost controls are required?
46. Propose a metric for whether the fan-in synthesizer actually *reconciles* contradictory specialist hypotheses vs just restating the highest-confidence one.
47. The `[tag]` attribution is a string prefix inside the claim, not a schema field. Argue the trade-off vs a first-class `source` field and what downstream tooling it blocks.
48. Given the GIL, would `ProcessPoolExecutor` or `asyncio` be a better fan-out primitive here? Justify from the actual workload (network-bound urllib POSTs).
49. Design a CI that exercises this repo without Docker or Ollama. Exactly which code paths get real coverage and which stay untested, and is that acceptable?
50. Rank the three highest-leverage changes to move this from "L4 portfolio" to "publishable applied result," with the experiment each unlocks.

---

# Part 14 — Overall Score

| Dimension | Score | One-line justification |
|---|---|---|
| **Architecture** | 7/10 | Clean layering and a real fan-out/fan-in with a genuine shared-state store; loses points for the oversold "blackboard" framing and single-host, hardcoded-scenario scope. |
| **Networking** | 8/10 | Deep, correct SONiC/FRR/BGP knowledge backed by measured spikes; loses points because the faults are single-field-trivial and the ASN collector ignores NOTIFICATION detail. |
| **AI** | 6/10 | Solid multi-agent orchestration, prompt grounding, and a real LoRA+eval harness with honest hallucination metrics; no RAG/tool-calling/planning and no live-path verification. |
| **Systems Design** | 7/10 | Idempotent fixtures, readiness gates, deterministic exit codes, deep-copy isolation; unlocked GIL-dependent concurrency and hard external-image coupling cap it. |
| **Code Quality** | 8/10 | Exceptional docstrings, defensive programming, clean error handling, zero dead code; four-way Ollama duplication and no packaging pull it back. |
| **Research** | 4/10 | Honest negative results and a reusable testbed, but no baselines, no ablations, n=3/n=6, and trivial task difficulty — not a research contribution. |
| **Reproducibility** | 6/10 | Offline smoke tests fully self-contained and verified runnable; live loop blocked on an out-of-repo image + Ollama with no mock/replay path. |
| **Open Source Quality** | 6/10 | MIT license, thorough README/ADRs, good `.gitignore`; no tests, no CI, no `__init__.py`/packaging, no contribution guide. |
| **Portfolio Value** | 8/10 | Rare, credible SONiC + applied-LLM intersection with visible engineering judgment and honesty — strong differentiator for infra/ML roles. |
| **Resume Value** | 8/10 | Demonstrates NOS internals, BGP ops, multi-agent LLM, and PEFT/eval in one coherent artifact; the honesty about limits reads as senior maturity. |
| **Hiring Impact** | 7/10 | Clear strong-L4/borderline-L5 IC signal for networking-ML/infra teams; the missing tests/CI/baselines are what hold it below a clean staff signal. |

**Overall: ~6.8/10 — a genuinely real, honestly-scoped, well-crafted senior-IC portfolio project whose ceiling is limited by the absence of tests/CI, statistical rigor, and a baseline that would prove the multi-agent machinery earns its complexity.**
