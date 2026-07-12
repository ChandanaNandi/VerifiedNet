# Part 1 — Executive Summary

`sonic-acl-validation-harness` is a small, deliberately-scoped Python + shell
project that validates exactly one SONiC ACL scenario end-to-end: apply
`DATAACL` with rule `drop_https` (drop TCP dst-port 443, IP protocol 6, priority
100, on `Ethernet4`, INGRESS/L3) into a SONiC virtual-switch (VS) container, then
verify the intent propagates from CONFIG_DB down to the SAI object layer in
ASIC_DB, and cleanly reverses on teardown. It is honest about being non-AI and
honest about VS-vs-hardware limitations.

Total footprint is ~1,050 lines of first-party code (per `git log --stat` on the
initial commit: 1,023 insertions), across 9 source/doc files plus 3 test files.
It is 5 commits, all created the same day, with a real feature-branch merge
(`74573c6 Merge ASIC delta validation flow`) rather than a single dump — the
`flow` command with ASIC delta validation was genuinely built as a second
increment on top of the base harness (commit `302c5a1`).

The engineering quality is above what the trivial scope suggests. The single
most important architectural decision — separating **pure evaluators**
(`evaluate_config_db_state`, `evaluate_asic_acl_entry_attrs`,
`evaluate_cleanup_state`) from **Redis I/O** — is correct and is what makes the
35 unit tests possible without docker. The author clearly understands the
SONiC CONFIG_DB → orchagent → APP_DB → syncd → ASIC_DB → SAI pipeline, the SAI
ternary `value&mask:0x...` match model, and the difference between
software-visible SAI translation and hardware enforcement.

The equally important caveat: **no test exercises real integration.** Every
test feeds hand-built Python dicts into the pure evaluators. The docker/`sonic-db-cli`/
`sonic-cfggen` code paths, and the entire `flow` ASIC-delta orchestration, are
**never executed in CI** (there is no CI at all) and are validated only by the
author running them once by hand against a local `docker-sonic-vs-fixed:latest`
image on 2026-05-27 (`docs/findings.md`). The Redis output parsing
(`parse_hgetall_output`) is genuinely thoughtful about SONiC's three
serialization shapes, but it is still fragile string-parsing of `sonic-db-cli`
stdout rather than a proper `swsssdk`/`redis-py` client. The packet-testing
module is real Scapy code but is aspirational in practice — it `skip`s unless the
caller supplies a working dataplane path, which the VS topology here does not
provide by default.

Overall: a clean, honest, well-tested-at-the-unit-level demonstration piece.
Strong signal of SONiC/SAI domain literacy and disciplined Python; weak signal
of production systems engineering (no real integration harness, one hardcoded
scenario, shell-parsing coupling). Verdict: a strong new-grad / junior-to-mid
portfolio artifact, not a staff-level systems contribution.

# Part 2 — Architecture

```text
                          HOST (developer machine / CI runner)
 ┌───────────────────────────────────────────────────────────────────────────┐
 │                                                                             │
 │   scripts/bringup.sh ──docker run/start──►  ┌───────────────────────────┐  │
 │   scripts/apply_acl.sh   ┐                  │  SONiC VS container        │  │
 │   scripts/cleanup_acl.sh ├─ wrap ─►         │  name: sonic-vs-acl        │  │
 │   scripts/show_state.sh  ┘  python3 -m      │  image: docker-sonic-      │  │
 │                             acl.acl_harness │        vs-fixed:latest      │  │
 │                                             │  (--privileged)             │  │
 │   ┌─────────────────────────────────────┐  │                             │  │
 │   │ acl/acl_harness.py  (CLI/orchestr.) │  │   ┌─────────────────────┐   │  │
 │   │  argparse subcommands:              │  │   │ Redis (multi-DB)    │   │  │
 │   │   apply / validate / status /       │  │   │  CONFIG_DB  (4)     │   │  │
 │   │   cleanup / flow                    │  │   │  APPL_DB    (0)     │   │  │
 │   └───────┬───────────────┬─────────────┘  │   │  ASIC_DB    (1)     │   │  │
 │           │               │                │   └─────────▲───────────┘   │  │
 │           │               │                │             │               │  │
 │           │        docker exec ────────────┼──► sonic-db-cli <DB> <cmd>  │  │
 │           │        docker cp   ────────────┼──► /tmp/acl_drop_https.json │  │
 │           │        docker exec ────────────┼──► sonic-cfggen -j ... \    │  │
 │           │                                │        --write-to-db        │  │
 │           │                                │             │               │  │
 │           │                                │      writes CONFIG_DB       │  │
 │           │                                │             │               │  │
 │           │                                │        orchagent (swss)     │  │
 │           │                                │             ▼               │  │
 │           │                                │        APPL_DB (maybe)      │  │
 │           │                                │             │               │  │
 │           │                                │        syncd / SAI vs       │  │
 │           │                                │             ▼               │  │
 │           │                                │        ASIC_DB SAI objects  │  │
 │           │                                └─────────────┼───────────────┘  │
 │           │                                              │                   │
 │   ┌───────▼──────────────────────────────────────┐      │                   │
 │   │ acl/db_checks.py  (I/O + PURE evaluators)     │◄─────┘ (parse stdout)    │
 │   │  run_in_container / sonic_db_cli / keys /     │                          │
 │   │  hgetall / parse_hgetall_output               │  ← fragile string parse  │
 │   │  render_acl_json (intent → CONFIG_DB JSON)    │                          │
 │   │  evaluate_config_db_state    (PURE)           │  ◄── unit tests           │
 │   │  evaluate_asic_acl_entry_attrs (PURE)         │  ◄── unit tests           │
 │   │  evaluate_cleanup_state       (PURE)          │  ◄── unit tests           │
 │   │  compute_asic_entry_delta / find_scenario_... │                          │
 │   └───────────────────────────────────────────────┘                          │
 │                                                                             │
 │   ┌───────────────────────────────────────────────┐                          │
 │   │ acl/packet_tests.py  (optional Scapy dataplane)│  ─► sr1() TCP SYN        │
 │   │  run_packet_checks(dst_ip, iface) → skip/…     │     (skip unless given)  │
 │   └───────────────────────────────────────────────┘                          │
 │                                                                             │
 │   acl/config.py  (frozen dataclasses: SonicTarget, AclScenario) — single     │
 │                   source of truth for all names/ports/values                 │
 └───────────────────────────────────────────────────────────────────────────┘
```

Component-by-component:

- **`acl/config.py`** — two `@dataclass(frozen=True)` objects. `SonicTarget`
  holds container name (`sonic-vs-acl`) and image
  (`docker-sonic-vs-fixed:latest`). `AclScenario` holds every scenario constant
  and derives `table_key` (`ACL_TABLE|DATAACL`) and `rule_key`
  (`ACL_RULE|DATAACL|drop_https`) as properties. Everything downstream imports
  `SCENARIO`/`TARGET` singletons.

- **`acl/acl_harness.py`** — the CLI and orchestration layer. Owns process
  control (`docker ps`, `docker inspect`, `docker cp`), subcommand wiring
  (argparse), and the multi-step `flow` state machine. Delegates all DB
  reasoning to `db_checks`.

- **`acl/db_checks.py`** — the heart. Owns (a) container I/O primitives
  (`run_in_container`, `sonic_db_cli`, `keys`, `hgetall`, `key_exists`), (b)
  stdout parsing (`parse_hgetall_output`, `parse_redis_line`, `parse_port_list`),
  (c) intent rendering (`render_acl_json`), and (d) the pure evaluators and their
  `Check`/`ValidationReport` value objects.

- **`acl/packet_tests.py`** — optional dataplane probing via Scapy `sr1`.
  Isolated so its heavy/optional dependency never blocks the DB path.

- **`scripts/*.sh`** — thin ergonomic wrappers. `bringup.sh` is the only one with
  real logic (idempotent create/start); the others just `cd` and call the Python
  module or `docker exec sonic-db-cli`.

- **`docs/`** — `sonic_acl_state_flow.md` (conceptual pipeline) and `findings.md`
  (the one real hand-run observation log, dated 2026-05-27).

Connections: the CLI never talks to Redis directly; all Redis access is funneled
through `docker exec ... sonic-db-cli` inside `db_checks.py`. The only two writes
to the switch are `sonic-cfggen --write-to-db` (apply) and two `sonic-db-cli DEL`
calls (cleanup). Everything else is read-only KEYS/HGETALL/EXISTS.

# Part 3 — Repository Structure

Walking every file (all paths absolute):

- `/tmp/repos/sonic-acl-validation-harness/acl/__init__.py` — 1 line docstring.
  Marks `acl` as a package so `python3 -m acl.acl_harness` and `from acl.config
  import …` work.

- `/tmp/repos/sonic-acl-validation-harness/acl/config.py` (42 lines) — **owns
  configuration/storage-of-constants.** `SonicTarget` and `AclScenario` frozen
  dataclasses; `table_key`/`rule_key` derived properties; module-level `TARGET`
  and `SCENARIO` singletons. No I/O, no logic. Clean single source of truth.

- `/tmp/repos/sonic-acl-validation-harness/acl/acl_harness.py` (248 lines) —
  **owns orchestration.** `docker_available`/`container_running`/`require_container`
  (preflight), `copy_to_container` (docker cp), `_do_apply`/`_do_cleanup`
  (mutations), the five subcommand handlers (`apply_acl`, `cleanup_acl`,
  `validate`, `status`, `flow`), `_emit_flow_report` (presentation), and
  `build_parser`/`main`. Note the module-level constants
  `ASIC_ACL_ENTRY_PATTERN` and `SAI_ATTRS_OF_INTEREST` (lines 30-36) that drive
  the delta logic.

- `/tmp/repos/sonic-acl-validation-harness/acl/db_checks.py` (335 lines) — **owns
  storage queries AND validation logic** (the one place where separation of
  concerns is slightly muddied — I/O and pure evaluation coexist in one file,
  though they are cleanly separated at the function level). Contains: value
  objects `Check`/`ValidationReport`; I/O (`run_in_container`, `sonic_db_cli`,
  `key_exists`, `hgetall`, `keys`); parsers (`parse_hgetall_output`,
  `parse_redis_line`, `parse_config_value`, `parse_port_list`, `field_matches`,
  `first_present`, `strip_sai_mask`); pure evaluators
  (`evaluate_config_db_state`, `evaluate_asic_acl_entry_attrs`,
  `evaluate_cleanup_state`); observers (`app_db_observe`, `asic_db_observe`);
  delta logic (`compute_asic_entry_delta`, `find_scenario_entry`); orchestration
  helpers (`config_db_validate`, `validate_acl_state`, `validate_cleanup`); and
  intent rendering (`render_acl_json`).

- `/tmp/repos/sonic-acl-validation-harness/acl/packet_tests.py` (43 lines) —
  **owns dataplane/networking probing.** `PacketResult` dataclass and
  `run_packet_checks` with lazy Scapy import and skip-first behavior.

- `/tmp/repos/sonic-acl-validation-harness/scripts/bringup.sh` (18 lines) —
  idempotent container lifecycle; `set -euo pipefail`; env-overridable
  `CONTAINER_NAME`/`IMAGE`; `docker run -d --privileged`.

- `/tmp/repos/sonic-acl-validation-harness/scripts/apply_acl.sh` (6 lines) — `cd`
  + `python3 -m acl.acl_harness apply --rule drop_https`.

- `/tmp/repos/sonic-acl-validation-harness/scripts/cleanup_acl.sh` (6 lines) —
  `cd` + `python3 -m acl.acl_harness cleanup`.

- `/tmp/repos/sonic-acl-validation-harness/scripts/show_state.sh` (20 lines) —
  raw `docker exec sonic-db-cli` dumps of CONFIG_DB table/rule, APPL_DB DATAACL
  keys, ASIC_DB `SAI_OBJECT_TYPE_ACL*` keys. `|| true` so it never hard-fails.

- `/tmp/repos/sonic-acl-validation-harness/tests/test_acl_config.py` (157 lines,
  16 tests) — render + parser + config-eval fault cases.

- `/tmp/repos/sonic-acl-validation-harness/tests/test_asic_delta.py` (127 lines,
  13 tests) — SAI mask stripping, ASIC attr eval, delta computation, fingerprint
  matching.

- `/tmp/repos/sonic-acl-validation-harness/tests/test_acl_cleanup.py` (43 lines,
  6 tests) — key shapes, cleanup-order assertion, cleanup-eval fault cases.

- `/tmp/repos/sonic-acl-validation-harness/docs/findings.md` (105 lines) — the
  real single-run observation log (image/container, CONFIG_DB dict shapes, the
  observed `oid:0x80000000005e3` entry, SAI attr table, post-cleanup notes).

- `/tmp/repos/sonic-acl-validation-harness/docs/sonic_acl_state_flow.md` (58
  lines) — conceptual pipeline narrative.

- `/tmp/repos/sonic-acl-validation-harness/README.md` (170 lines) — scenario,
  quickstart, expected `validate`/`flow` output, fault-case index, limitations,
  role-relevance.

- `/tmp/repos/sonic-acl-validation-harness/requirements-dev.txt` (`pytest>=8.0`
  only — Scapy is intentionally not pinned; it is an optional runtime import).

- `/tmp/repos/sonic-acl-validation-harness/.gitignore`, `LICENSE` (Apache 2.0).

Notably **absent**: no `pyproject.toml`, `setup.py`, `setup.cfg`, `pytest.ini`,
`conftest.py`, `requirements.txt` (runtime), CI workflow (`.github/`),
`Dockerfile`, or `Makefile`. Tests rely on being invoked from the repo root so
that `import acl.*` resolves against CWD.

# Part 4 — Complete Execution Flow

### `python3 -m acl.acl_harness apply --rule drop_https`

1. `main(None)` → `build_parser().parse_args()` → `args.func = apply_acl`.
2. `apply_acl(args)` → `require_container()`:
   - `docker_available()` runs `subprocess.run(["docker","ps"])`; nonzero →
     `SystemExit("docker is not available or not running")`.
   - `container_running()` runs `docker inspect -f {{.State.Running}} sonic-vs-acl`
     and checks stdout `== "true"`; else `SystemExit(... run scripts/bringup.sh first)`.
3. `_do_apply()`:
   - `render_acl_json(SCENARIO)` builds the dict with `ACL_TABLE.DATAACL`
     (`policy_desc`="drop TCP destination port 443 on Ethernet4", `type`=L3,
     `ports`=["Ethernet4"], `stage`=INGRESS) and
     `ACL_RULE."DATAACL|drop_https"` (PRIORITY=100, PACKET_ACTION=DROP,
     IP_PROTOCOL=6, L4_DST_PORT=443), `json.dumps(indent=2, sort_keys=True)`.
   - writes to a `NamedTemporaryFile(delete=False)` on the host.
   - `copy_to_container(tmp, "/tmp/acl_drop_https.json")` →
     `docker cp <tmp> sonic-vs-acl:/tmp/acl_drop_https.json` (`check=True`).
   - `run_in_container(["sonic-cfggen","-j","/tmp/acl_drop_https.json","--write-to-db"])`
     → `docker exec sonic-vs-acl sonic-cfggen -j ... --write-to-db`. This is the
     one command that writes CONFIG_DB. `finally:` unlinks the temp file.
   - nonzero rc → write stderr, return rc.
4. Back in `apply_acl`: rc==0 → `print("apply: pass")`. **Note:** apply does
   *not* re-read or validate anything — it trusts `sonic-cfggen`'s exit code.
   Inside the container, orchagent/swss then (asynchronously) translates CONFIG_DB
   into APPL_DB and syncd materializes SAI objects in ASIC_DB. The harness does
   **not** wait/poll for that convergence.

### `python3 -m acl.acl_harness validate`

1. `validate(args)` (args.dst_ip/iface default `None`) → `require_container()`.
2. `validate_acl_state(SCENARIO)`:
   - `config_db_validate(SCENARIO)`:
     - `hgetall("CONFIG_DB","ACL_TABLE|DATAACL")` → `docker exec sonic-vs-acl
       sonic-db-cli CONFIG_DB HGETALL "ACL_TABLE|DATAACL"` → stdout parsed by
       `parse_hgetall_output` (tries `ast.literal_eval` for the `{'k':'v'}`
       python-dict shape SONiC's cli emits; else numbered/raw line-pair parse).
     - same for the rule key.
     - `evaluate_config_db_state` builds 9 `Check`s: ACL_TABLE present, ACL_RULE
       present, port_binding (via `field_matches` → `parse_port_list` handling
       list/comma/`@`/`ports@`), stage, type, priority (accepts PRIORITY or
       priority casing), IP_PROTOCOL, L4_DST_PORT, PACKET_ACTION.
   - `app_db_observe`: `keys("APPL_DB","*DATAACL*")` → if empty, adds a **skip**
     ("not visible in this VS image").
   - `asic_db_observe`: `keys("ASIC_DB","...ACL_TABLE*")` and `...ACL_ENTRY*`;
     if any, **pass** with `tables=N entries=M`; else skip.
3. `run_packet_checks(None, None)` → both dst_ip/iface falsy → returns two
   **skip** `PacketResult`s ("requires --dst-ip and --iface"). Each folded into
   the report via `report.add(name, None if skip else pass/fail, detail)`.
4. `report.text()` printed; return `0 if report.passed else 1`. `passed` is true
   iff every check is `pass` or `skip` (skips do not fail).

### `python3 -m acl.acl_harness flow`

1. `require_container()`.
2. `pre_keys = keys("ASIC_DB", "ASIC_STATE:SAI_OBJECT_TYPE_ACL_ENTRY*")` — the
   baseline snapshot (docker exec sonic-db-cli KEYS).
3. `_do_apply()` (identical to `apply` above); nonzero → return.
4. In a `try/finally`:
   - `config_report = config_db_validate(SCENARIO)` (2 HGETALLs + pure eval).
   - `post_keys = keys("ASIC_DB", ...ACL_ENTRY*)`.
   - `new_keys = compute_asic_entry_delta(pre_keys, post_keys)` — order-preserving
     set difference.
   - `candidates = {k: hgetall("ASIC_DB", k) for k in new_keys}` — one HGETALL per
     new entry.
   - `matched_oid = find_scenario_entry(SCENARIO, candidates)` — first candidate
     whose `evaluate_asic_acl_entry_attrs(...).passed` (priority==100,
     strip_sai_mask(L4)==443, strip_sai_mask(proto)==6,
     action==SAI_PACKET_ACTION_DROP). `matched_attrs` = that entry's dict.
   - `finally: cleanup_ok = _do_cleanup() == 0` — always runs cleanup even on
     exception (`DEL ACL_RULE|...` then `DEL ACL_TABLE|...`).
5. If `matched_oid`: `final_keys = keys(...)`; `removed = matched_oid not in
   final_keys`. Else `removed = None`.
6. `_emit_flow_report(...)` prints: `baseline ACL_ENTRY keys: N`, `apply: pass`,
   each config check line, the ASIC delta line + 4 SAI attr sub-lines (or a fail
   line), `cleanup: pass/fail`, `ASIC_DB scenario entry removed: pass/fail/skip`,
   `verdict`. Overall pass = config_report.passed AND matched_oid AND cleanup_ok
   AND removed is True. Returns 0/1.

Critical flow observation: there is **no sleep/poll** between `_do_apply()` and
the `post_keys` read. The harness reads ASIC_DB immediately after `sonic-cfggen`
returns, assuming orchagent+syncd converge synchronously. On a slow VS this is a
race that could produce an empty delta and a false `fail` (see Part 10).

# Part 5 — Networking Concepts

**ACL semantics.** The scenario is a canonical stateless ingress ACL: on
`Ethernet4`, drop TCP (IP protocol 6) segments whose L4 destination port is 443
(HTTPS), priority 100, table type `L3`, stage `INGRESS`. Encoded once in
`config.py` and rendered to SONiC's schema by `render_acl_json` — an `ACL_TABLE`
object binding ports+stage+type and an `ACL_RULE` keyed `DATAACL|drop_https` with
the match/action fields. This is faithful to real SONiC ACL YANG/CONFIG_DB
schema (table `ports` list, rule `PRIORITY`/`PACKET_ACTION`/`IP_PROTOCOL`/
`L4_DST_PORT`).

**The CONFIG_DB → APP_DB → ASIC_DB pipeline.** The code demonstrates genuine
understanding of the SONiC control plane:
- CONFIG_DB (Redis DB 4) is written via `sonic-cfggen --write-to-db` — the
  intended/declared state and, per the docs, "the source of truth for this first
  version." Validated deterministically and precisely.
- APP_DB / APPL_DB (Redis DB 0) is where orchagent (swss) republishes resolved
  state. The harness treats this as **conditional**: `app_db_observe` searches
  `*DATAACL*` and, per `findings.md`, this VS image exposed **no** DATAACL APPL_DB
  keys, so it reports `skip`. This is honest — real SONiC uses
  `ACL_TABLE_TABLE`/`ACL_RULE_TABLE` in APPL_DB, but the author correctly does
  not fabricate a pass when the image doesn't show them.
- ASIC_DB (Redis DB 1) holds the SAI object graph produced by syncd. The harness
  matches `ASIC_STATE:SAI_OBJECT_TYPE_ACL_TABLE*` and `...ACL_ENTRY*`.

**SAI object model.** This is where the project is strongest and most credible.
`evaluate_asic_acl_entry_attrs` checks the four canonical SAI ACL-entry
attributes: `SAI_ACL_ENTRY_ATTR_PRIORITY`, `SAI_ACL_ENTRY_ATTR_FIELD_L4_DST_PORT`,
`SAI_ACL_ENTRY_ATTR_FIELD_IP_PROTOCOL`, `SAI_ACL_ENTRY_ATTR_ACTION_PACKET_ACTION`.
`strip_sai_mask` correctly handles SAI's **ternary match** encoding
`value&mask:0xffff` — real SAI ACL fields are `sai_acl_field_data_t` (data+mask),
and `findings.md` documents observed `443&mask:0xffff` / `6&mask:0xff` exactly.
`expected_sai_action` maps `DROP` → `SAI_PACKET_ACTION_DROP`. The
`docs/findings.md` one-to-one CONFIG_DB→SAI mapping table is accurate and shows
real domain knowledge (mask=0xffff means exact match on full 16-bit port width;
action is an enum with no mask).

**The ASIC delta (`flow`).** The README claim that `flow` "identifies the
scenario's entry by matching SAI attributes … and finally verifies that *that
specific* entry disappears after cleanup" is **truthfully implemented**:
`compute_asic_entry_delta` (pre/post set difference) + `find_scenario_entry`
(attribute fingerprint) + membership re-check in `final_keys`. It even leaves
pre-existing ACL_TABLE objects alone, as claimed. This is a legitimately clever
way to disambiguate the scenario's entry from baseline SAI state without relying
on OID stability — the strongest single idea in the repo.

**What is real vs described.** Real and executed by hand once: CONFIG_DB write,
CONFIG_DB validation, ASIC_DB SAI object observation and delta, cleanup removal
(documented in `findings.md`, 2026-05-27). Described but effectively never
exercised: APP_DB translation (absent in this image), and **packet-level
enforcement**. `packet_tests.run_packet_checks` builds a real
`IP(dst=...)/TCP(dport=443,flags="S")` and `sr1(..., iface=..., timeout=2)`,
inferring drop from `response is None` — but it only runs when `--dst-ip` and
`--iface` are supplied, and the README/docs admit the VS topology provides no
default reachable path, so in practice it always `skip`s. Critically, the README
itself is explicit that ASIC_DB presence "is not proof of hardware-backed ASIC
enforcement" — the honesty here is a strength, not a hidden weakness.

**Precision gap:** the harness validates *that a SAI ACL entry with the right
match/action exists*, but it never verifies the entry is actually **bound** to
`Ethernet4` (no check of `SAI_OBJECT_TYPE_ACL_TABLE_GROUP` /
`SAI_ACL_ENTRY_ATTR_TABLE_ID` / port ACL-binding attributes). So "drop on
Ethernet4" is validated at CONFIG_DB level but not proven at the SAI binding
level — the SAI check proves the rule was translated, not that it was attached to
the intended port.

# Part 6 — AI Concepts

**N/A — verified.** There is no AI/ML anywhere. No model files, no `numpy`/
`torch`/`sklearn`/LLM/agent imports, no inference, no "intent parsing" via NLP.
The README states plainly: "This is a SONiC / ACL / SAI-concepts project, not an
AI project. Deterministic Python validation is the source of truth." Every
decision is a deterministic equality/membership check over parsed Redis state.
The repo is correctly and intentionally non-AI, and the "AI" score in Part 14
reflects that this is by design, not a deficiency.

# Part 7 — Software Engineering

**Folder structure / modularity.** Clean for the size: `acl/` package (config /
orchestration / db+validation / packet), `scripts/`, `tests/`, `docs/`.
Config is a proper single source of truth via frozen dataclasses. The CLI does
not reach into Redis; it goes through `db_checks`. Good.

**Abstraction — the standout.** The deliberate split of **pure evaluators**
(`evaluate_config_db_state`, `evaluate_asic_acl_entry_attrs`,
`evaluate_cleanup_state`) from **I/O** (`config_db_validate`, `validate_acl_state`,
`validate_cleanup`) is textbook and is explicitly motivated in docstrings
("Separated from Redis I/O so fault cases are unit-testable without docker"). The
`Check`/`ValidationReport` value objects give uniform pass/skip/fail semantics and
a single `.text()` renderer. This is senior-flavored design in a junior-sized
repo.

**Testing — real integration or pure mocks?** This is the crux. There are **35
tests** (16 + 13 + 6) and **zero** are integration tests. They import the pure
evaluators and feed **hand-constructed Python dicts** — e.g.
`test_asic_delta.py::_good_attrs()` returns a literal SAI-attr dict; there is no
docker, no Redis, no `unittest.mock` even (the repo contains no `mock` usage at
all — the earlier grep hits were all `node_modules` noise from the host env, not
this repo). Consequences:
  - **Praise:** the tests genuinely validate the *decision logic* — mask
    stripping, port-serialization variants (`["Ethernet4"]` / `Ethernet4,` /
    `Ethernet4@` / `ports@`), numbered-vs-raw-vs-python-dict HGETALL parsing,
    delta computation, fingerprint matching, and 9 fault cases. That is more
    rigorous fault-case coverage than most portfolio repos.
  - **Gap:** the parts most likely to break in reality — `run_in_container`,
    `sonic_db_cli`, `copy_to_container`, `_do_apply`/`_do_cleanup`, the whole
    `flow` orchestration, and the actual behavior of `sonic-cfggen`/`sonic-db-cli`
    stdout — have **no automated coverage**. `parse_hgetall_output` is unit-tested
    against *assumed* CLI shapes, but whether real `sonic-db-cli` emits exactly
    those shapes is validated only by one manual run in `findings.md`. So the
    project's headline claim ("validates ACL state end-to-end through
    CONFIG_DB→APP_DB→ASIC_DB") is **unit-verified for logic, manually-verified
    once for integration, and CI-verified never.**

**Dependency management.** Minimal and slightly under-specified. Only
`requirements-dev.txt` (`pytest>=8.0`). Scapy is an optional lazy import (good —
keeps the DB path dependency-free), but there is no runtime `requirements.txt`,
no `pyproject.toml`/`setup.py`, so `acl` is not installable/packageable; tests
depend on CWD-based import resolution (no `conftest.py` or `[tool.pytest]`
rootdir config).

**Logging / error handling.** No `logging` module usage at all — everything is
`print()` / `sys.stderr.write`. Error handling is pragmatic but shallow:
`require_container` raises `SystemExit` with helpful messages; `copy_to_container`
uses `check=True` (will raise `CalledProcessError` with an ugly traceback rather
than a clean message); most I/O functions swallow failures by returning `{}`/`[]`
on nonzero rc, which means a broken `docker exec` silently degrades to "missing"
checks rather than an explicit error. `_emit_flow_report` uses `assert` for a
control-flow invariant (`assert matched_attrs is not None`) — assertions can be
stripped under `python -O`.

**Config / Docker / reproducibility.** Container name/image are configurable via
env in `bringup.sh` and via dataclass in Python, but **the image
`docker-sonic-vs-fixed:latest` is not built by this repo** — there is no
`Dockerfile` and no documented provenance for "fixed". That is the single biggest
reproducibility hole: a fresh cloner cannot reproduce the `findings.md` run
without an image they don't have and can't rebuild. `--privileged` is required
and unexplained. No CI means the "runs green" claim rests on trust.

**Code quality / maintainability.** High for the size: `from __future__ import
annotations`, modern typing (`str | None`, `list[str]`), dataclasses, clear
docstrings explaining *why* (SONiC serialization quirks), small functions, no
dead code, no TODO/FIXME/placeholder in first-party files. `sort_keys=True` in
`render_acl_json` makes output deterministic. Readable and idiomatic.

**Extensibility.** Currently hard-capped at one scenario: argparse literally does
`choices=[SCENARIO.rule_name]` for `--rule`, and every value is a class default.
Adding a second rule/table/scenario would require real refactoring (scenario
registry, parameterized keys). The pure-evaluator design *would* make that
refactor pleasant, but it hasn't been done.

# Part 8 — Research Quality

Positioned (correctly) as **verification/testing tooling**, not a research
contribution. Judged as such:

Reviewers would **praise**: the crisp problem framing (one scenario, end-to-end,
honestly bounded); the pure/impure separation enabling reproducible logic tests;
the SAI ternary-mask handling and the CONFIG_DB→SAI attribute mapping table in
`findings.md` (this reads like a good lab notebook); the intellectual honesty
about VS-vs-hardware and about APP_DB invisibility; and the delta-fingerprint
technique for isolating the scenario's SAI entry.

Reviewers would **criticize**: n=1 empirical evidence (a single manual run on a
single undocumented image, one date); no falsifiable comparison against ground
truth beyond "the fields I wrote are the fields I read back"; no measurement of
convergence timing (the apply→observe race is unaddressed); no negative control
(e.g., does an intentionally malformed CONFIG_DB actually fail to produce a SAI
entry, observed live? — only unit-simulated); scope too narrow to generalize (no
egress, no L4 source port, no ranges, no IPv6, no multiple ports/tables, no
counters/`SAI_ACL_COUNTER`); and no verification of ACL *binding* to the port at
the SAI layer. Missing rigor: reproducible environment (Dockerfile), automated
integration harness, and multiple-image cross-validation to show the parser's
three-shape handling is actually necessary/sufficient rather than speculative.

As a testing-methodology artifact it is competent and honest; as research it is a
well-kept single-case study, not a generalizable result.

# Part 9 — Hiring Committee Review

**Would it impress NVIDIA / Cisco / Arista / Juniper / Azure&GCP Networking /
Meta Infra committees?** It would earn a genuine second look and a strong "invite
to interview" from a networking-infra team screening for **SONiC/SAI literacy** —
which is a scarce, specific skill. It would not, on its own, clear a senior/staff
bar.

**Skills demonstrated (real):**
- Concrete SONiC control-plane fluency: CONFIG_DB schema, `sonic-cfggen
  --write-to-db`, `sonic-db-cli`, orchagent/syncd/ASIC_DB layering.
- SAI object-model literacy: ACL_TABLE/ACL_ENTRY object types, ternary
  `value&mask` fields, `SAI_PACKET_ACTION_*` enums.
- Disciplined Python: dataclasses, typing, pure/impure separation, testable
  design, deterministic output.
- Engineering honesty: skip-not-fake for unobservable state; explicit
  VS-vs-hardware disclaimer.

**Gaps a committee would flag:** no real integration/CI; fragile CLI-stdout
parsing instead of a proper SONiC Python SDK (`swsssdk`/`sonic-py-common`); one
hardcoded scenario; no SAI binding verification; no Dockerfile/reproducibility;
`print`-based, no logging; `--privileged` container hygiene.

**Level assessment:** The *design instincts* (pure evaluators, value objects,
honest reporting) are **L4/mid to occasionally senior-flavored**. The *scope and
production-robustness* are **new-grad/L3**. Net: this is a **strong new-grad or
junior-to-mid (L3, reaching L4)** portfolio piece — clearly above a bootcamp
project, clearly below what would be called staff-level. It signals "this person
can ramp fast on SONiC and writes clean, testable Python," which for a networking
team is a high-value signal even at n=1 scenario. Claiming staff/senior on the
strength of this repo alone would be a red flag; presenting it as focused,
honest domain-learning work is exactly right.

# Part 10 — Weaknesses (brutally honest)

1. **No real integration test; CI absent.** 35/35 tests are pure-logic over
   hand-built dicts. The docker/`sonic-db-cli`/`sonic-cfggen`/`flow` paths are
   never automatically executed. The end-to-end claim is trust-based (one manual
   run, `findings.md`). No `.github/workflows`, no test for `main`/argparse
   wiring, no test for `_do_apply`/`_do_cleanup`/`flow`.

2. **Fragile stdout parsing of `sonic-db-cli`.** `parse_hgetall_output` heuristically
   distinguishes a python-dict `{'k':'v'}` shape (via `ast.literal_eval`), redis
   numbered lines, and raw pairs. This is inherently brittle: it depends on the
   exact CLI text format, which varies by SONiC version and can break silently.
   A value legitimately containing `&mask:` or `,`/`@` (e.g. `parse_port_list`)
   could be misparsed. `ast.literal_eval` on subprocess stdout is safer than
   `eval` but still a code-smell versus using `swsssdk`/`redis-py` to read
   structured hashes directly. `literal_eval` also silently mis-handles values
   with embedded quotes/unicode.

3. **Silent failure degradation.** `hgetall`/`keys`/`key_exists` return
   empty/`False` on nonzero rc. A broken container or renamed DB yields
   "missing"/"skip" checks rather than a loud error — a false-negative-shaped
   failure mode that can mask real breakage as an innocuous skip.

4. **Apply→observe race in `flow`.** ASIC_DB is read immediately after
   `sonic-cfggen` returns, with no wait/poll for orchagent+syncd convergence. On
   a slow/loaded VS the delta can be empty → spurious `fail`. No retry/backoff.

5. **SAI binding never verified.** The harness proves a SAI ACL_ENTRY with the
   right match/action exists, but never checks it is bound to `Ethernet4`
   (no `SAI_ACL_ENTRY_ATTR_TABLE_ID` / ACL group / port-binding attribute check).
   "Drop on Ethernet4" is only proven at CONFIG_DB level.

6. **Single hardcoded scenario.** Every value is a dataclass default; `--rule`
   is `choices=[SCENARIO.rule_name]` (i.e. only `drop_https`). No egress, no port
   ranges, no L4 src, no IPv6, no multi-port/table, no counters. Zero
   parameterization.

7. **Reproducibility hole.** `docker-sonic-vs-fixed:latest` is required but not
   built here — no `Dockerfile`, no provenance for "fixed". A cloner cannot
   reproduce `findings.md`. No pinned SONiC version. `--privileged` unexplained.

8. **Packaging/config gaps.** No `pyproject.toml`/`setup.py`/`conftest.py`/
   `pytest.ini`; tests depend on CWD import resolution; no runtime
   `requirements.txt`; Scapy unpinned.

9. **Observability.** No `logging`; `print`/`sys.stderr.write` only. `assert` used
   for a runtime invariant in `_emit_flow_report` (stripped under `-O`).
   `copy_to_container(check=True)` surfaces a raw traceback instead of a clean
   message.

10. **Security/hygiene (minor).** `--privileged` container; commands built as
    `docker exec <container> sonic-db-cli <db> <key>` where keys/patterns derive
    from config (not user input today), so injection risk is low — but if the
    scenario ever became user-supplied, the current string-based command
    construction has no validation/quoting discipline. Temp JSON written with
    `delete=False` and only best-effort unlinked in `finally` (leaks on hard
    crash, though contents are non-sensitive).

11. **`show_state.sh` uses `|| true` everywhere** — convenient, but means the
    script cannot signal failure; it always exits 0 even if the container is
    down.

# Part 11 — Reusable Components (toward a future "NetworkGym")

**Directly reusable as a validation module (little/no change):**
- `acl/db_checks.py` pure evaluators — `evaluate_config_db_state`,
  `evaluate_asic_acl_entry_attrs`, `evaluate_cleanup_state`, plus `Check`/
  `ValidationReport`. These are clean, dependency-free, well-tested value/logic
  units — an ideal "verifier" primitive for a gym reward/validation signal.
- `strip_sai_mask`, `expected_sai_action`, `compute_asic_entry_delta`,
  `find_scenario_entry` — small, general SAI-reasoning helpers. `find_scenario_entry`
  (fingerprint-match a config against candidate SAI objects) is a genuinely
  reusable pattern.
- `parse_port_list`/`field_matches`/`parse_hgetall_output` — reusable *only if*
  you keep the "shell out to `sonic-db-cli` and parse stdout" architecture; see
  below.
- The 35 tests are a reusable regression suite for the evaluators.

**Needs rewriting before reuse:**
- The I/O layer (`run_in_container`, `sonic_db_cli`, `hgetall`, `keys`,
  `key_exists`, `copy_to_container`, `_do_apply`, `_do_cleanup`). For a robust
  platform, replace `docker exec ... sonic-db-cli` + stdout parsing with a
  structured client (`swsssdk`/`sonic-py-common`/`redis-py` against the mapped
  Redis socket). Then `parse_hgetall_output` and friends become unnecessary.
- `config.py` — generalize `AclScenario` from a fixed dataclass into a scenario
  schema/registry so many scenarios can be expressed and composed.
- `acl_harness.py` orchestration — the `flow` state machine is a good template but
  needs convergence polling, retries, and structured logging before it is a
  reliable gym environment step.

**Should stay independent:**
- `packet_tests.py` — dataplane probing is topology-specific and heavyweight;
  keep it a pluggable optional backend, not core.
- The shell scripts — environment glue; a NetworkGym would provide its own
  lifecycle management.

# Part 12 — Portfolio Positioning

Recommendation: **keep it independent now, and later refactor its pure evaluators
into a small importable library / verification backend** rather than merging the
whole repo anywhere.

Reasoning:
- As a **standalone portfolio artifact** it is coherent, honest, and readable —
  its value is precisely that a reviewer can grok the whole thing in 15 minutes
  and see SONiC/SAI competence. Merging it into a larger repo would dilute that.
- If a sibling project like `sonic-intent-agent` exists (an intent→config
  system), this harness is the **natural verification backend** for it: the pure
  evaluators + delta-fingerprint are exactly the "did the intent actually
  materialize in CONFIG_DB/ASIC_DB?" oracle such an agent needs. The right move
  is to extract `db_checks`' pure functions into a thin library
  (`sonic-acl-verify`) that both this CLI and the agent import — i.e. **library,
  not submodule, not full merge.** A git submodule would couple lifecycles
  awkwardly; a full merge would bury the clean verifier inside agent code.
- Do **not** position it as a general SONiC test framework — it is one scenario.
  Position it as "a focused, honest SONiC ACL→SAI verification study + reusable
  verifier core."

# Part 13 — Interview Questions (Staff-level, repo-specific)

1. `_do_apply` reads ASIC_DB (in `flow`) immediately after `sonic-cfggen`
   returns. Why is that a correctness bug, and how would you make the
   observation converge-safe without unbounded blocking?
2. `parse_hgetall_output` first tries `ast.literal_eval` on stdout. Under what
   real SONiC `sonic-db-cli` outputs does this branch fire, and what value
   contents would make it silently return wrong data?
3. Why does `find_scenario_entry` iterate `candidates` and return the *first*
   full match? What happens if two new ACL_ENTRY OIDs both match the fingerprint,
   and is that possible for this scenario?
4. `compute_asic_entry_delta` uses `pre` as a `set`. Why is order preserved on
   `post` but not `pre`, and when would OID reuse across apply cycles defeat this
   delta approach?
5. The SAI check validates match+action but never `SAI_ACL_ENTRY_ATTR_TABLE_ID`
   or port binding. Construct a CONFIG_DB state that passes every current check
   yet does *not* drop TCP/443 on Ethernet4. 
6. Walk the exact SONiC internal path from `sonic-cfggen --write-to-db` to the
   `oid:0x8000...` ACL_ENTRY appearing in ASIC_DB. Which daemons touch which DBs?
7. `strip_sai_mask` splits on `&mask:`. For which SAI ACL field types is a mask
   *semantically required* vs optional, and how would a range match
   (`SAI_ACL_ENTRY_ATTR_FIELD_ACL_RANGE_TYPE`) break this parser?
8. Why is `IP_PROTOCOL: 6` compared as a string, and what SONiC serialization
   difference between CONFIG_DB and ASIC_DB does that gloss over?
9. `field_matches` returns True for `"Ethernet4,"` (trailing comma) but False for
   `"Ethernet0,Ethernet4"`. Explain `parse_port_list`'s logic and why a
   two-port binding must fail this exact-match check.
10. The harness marks APP_DB `skip` when no `*DATAACL*` keys exist. In a full
    SONiC, what APPL_DB table names would you expect, and why might the VS image
    legitimately not populate them?
11. `require_container` checks `docker inspect ... {{.State.Running}} == "true"`.
    Why is a running container insufficient to guarantee `sonic-db-cli` will
    succeed, and what would you additionally probe?
12. Design a real integration test for `flow` that runs in CI without a physical
    ASIC. What do you fake, what stays real, and how do you avoid the
    apply→observe race?
13. `_emit_flow_report` uses `assert matched_attrs is not None`. Why is that
    dangerous, and what should replace it?
14. The action maps `DROP → SAI_PACKET_ACTION_DROP`. How would `FORWARD`, `COPY`,
    or `TRAP` differ in SAI, and which need extra attributes beyond
    `PACKET_ACTION`?
15. Explain the ternary meaning of `443&mask:0xffff` vs `443&mask:0xfff0`. What
    L4 port range would the latter match?
16. Cleanup does `DEL ACL_RULE|...` then `DEL ACL_TABLE|...`. Why that order, and
    what breaks if reversed in real SONiC?
17. After cleanup, `findings.md` notes a residual `SAI_OBJECT_TYPE_ACL_TABLE`
    object. Is that a leak, expected default state, or a bug? How would you
    confirm?
18. The harness never checks `SAI_ACL_COUNTER`. How would you add hit-count
    verification, and what would prove the rule is actually being *evaluated* vs
    merely installed?
19. `sonic-cfggen --write-to-db` vs `config acl update full` vs `swssconfig` —
    when would each be the right injection path, and why did the author pick
    cfggen?
20. Why is priority 100 significant relative to default/implicit ACL entries, and
    how do overlapping-priority rules resolve in SAI?
21. The `flow` command re-reads `final_keys` and checks `matched_oid not in
    final_keys`. Why is membership on the OID string safe here but the
    attribute-fingerprint needed earlier?
22. How would you extend `AclScenario` to support egress + a port range + IPv6
    without breaking the existing 35 tests?
23. `run_in_container` uses `capture_output=True, text=True`. What encoding/locale
    assumptions does that bake in for `sonic-db-cli` output?
24. If `sonic-db-cli` returned RESP-formatted output instead of the python-dict
    shape, which tests would still pass and which parser branch would take over?
25. Explain why the pure-evaluator split makes 33 of 35 tests possible without
    docker, and what class of bug those tests structurally cannot catch.
26. Under `python -O`, which two behaviors in this repo change, and are either
    load-bearing?
27. How does orchagent decide whether an ACL table is `L3` vs `L3V6` vs `MIRROR`,
    and where would a mismatch surface — CONFIG_DB, APPL_DB, or ASIC_DB?
28. The temp JSON is `delete=False` + `finally: unlink(missing_ok=True)`. Give a
    failure scenario where the file leaks, and a cleaner pattern.
29. Why does `render_acl_json` use `sort_keys=True`, and how does that interact
    with reproducibility and with `sonic-cfggen`'s parsing?
30. What is the difference between `DENY` and `DROP` in SONiC ACL semantics, and
    does the SAI mapping here assume one?
31. How would you verify the ACL is applied at INGRESS specifically (not EGRESS)
    at the SAI layer? Which object/attribute encodes stage?
32. `asic_db_observe` counts tables and entries but does not correlate them. Write
    the check that proves *this* entry belongs to *this* table via the ACL group.
33. If two SONiC versions serialize `ports` differently (`["Ethernet4"]` vs
    `Ethernet4`), how does `parse_config_value`→`parse_port_list` keep both
    passing, and where is the risk of a false pass?
34. The harness shells out per query (one `docker exec` each). At 10k ACL entries,
    what breaks, and how would you batch/stream reads?
35. Explain the SAI object dependency order (switch → ACL table group → ACL table
    → ACL entry → port bind) and which the harness (in)directly observes.
36. `packet_tests` infers drop from `sr1(...) is None`. Enumerate non-ACL reasons
    a SYN gets no reply, and how you'd disambiguate a true ACL drop.
37. What veth/topology wiring would you need in this VS to make the TCP/80-permit
    vs TCP/443-drop packet test actually meaningful?
38. The image is `docker-sonic-vs-fixed`. What is typically "fixed" in a patched
    sonic-vs image for ACL/SAI experiments, and why might stock sonic-vs not show
    ASIC_DB ACL entries?
39. Design a negative control that proves the harness would *catch* a real
    misconfiguration end-to-end (not just in unit tests).
40. How would `find_scenario_entry` behave if orchagent split one rule into
    multiple SAI entries (e.g., range expansion)? Fix it.
41. Why compare `SAI_ACL_ENTRY_ATTR_FIELD_IP_PROTOCOL` as `6` and not the SONiC
    symbolic `tcp`, and where does that translation happen?
42. What consistency guarantees does Redis give across CONFIG_DB/APPL_DB/ASIC_DB
    during a config apply, and how should a validator account for eventual
    consistency?
43. If you had to run this against 50 scenarios in parallel in CI, what in the
    current design (container name, temp path `/tmp/acl_drop_https.json`, global
    singletons) forces serialization?
44. `_do_cleanup` runs in a `finally` even if apply/validate raised. Trace the
    state if `_do_apply` partially wrote CONFIG_DB then threw — is cleanup
    sufficient?
45. How would you assert the *absence* of collateral damage — that applying
    DATAACL did not disturb pre-existing ACL tables/entries — beyond the current
    delta approach?
46. Which SONiC daemon writes ASIC_DB, which reads it in a real box, and why does
    ASIC_DB presence in VS not imply forwarding-plane enforcement?
47. Propose a schema-driven rewrite where scenarios are declarative
    (YAML/YANG-ish) and the evaluators are generated — what stays from
    `db_checks`?
48. The report's `passed` treats `skip` as non-failing. Argue for and against that
    policy for a gating CI check.
49. How would you detect and fail on a partial SAI materialization (table present,
    entry missing) that the current `asic_db_observe` would report as a bland
    `tables=4 entries=1` pass?
50. If this became the verifier backend for an intent-driven agent, what
    adversarial inputs (near-miss configs) would you add to the test corpus to
    prevent the agent from gaming the reward?

# Part 14 — Overall Score

- **Architecture — 7/10.** Clean layering and a genuinely good pure/impure split;
  loses points for I/O coupled to stdout parsing and one file mixing I/O with
  evaluators.
- **Networking — 8/10.** Accurate SONiC ACL + SAI object/ternary-mask modeling and
  a clever delta-fingerprint; capped by no SAI port-binding verification and a
  single scenario.
- **AI — N/A (score 1/10 by design).** Intentionally non-AI; the low number
  reflects absence-by-design, not a flaw — should not count against the project.
- **Systems Design — 5/10.** Sensible flow orchestration but no convergence
  polling, silent-degradation failure modes, per-query docker exec, no
  scalability path.
- **Code Quality — 8/10.** Idiomatic modern Python, dataclasses, typing, honest
  docstrings, no dead code/TODOs; minor `assert`/`print`/no-logging demerits.
- **Research — 5/10.** Honest single-case study with a good notebook; n=1
  evidence, no negative controls or timing rigor.
- **Reproducibility — 4/10.** Deterministic tests, but the required
  `docker-sonic-vs-fixed` image is unbuildable from the repo and there is no CI.
- **Open Source Quality — 6/10.** Apache-2.0, clear README/docs, good tests; no
  CI/CONTRIBUTING/packaging, tests need CWD import resolution.
- **Portfolio Value — 7/10.** Scarce SONiC/SAI signal, honest framing, readable
  in minutes; narrow scope caps it.
- **Resume Value — 7/10.** Concretely demonstrates SONiC control-plane + SAI +
  testable Python — a differentiated line item for networking-infra roles.
- **Hiring Impact — 6/10.** Strong new-grad/junior-to-mid (L3, reaching L4)
  signal; not staff/senior evidence on its own, but a real interview magnet for
  SONiC teams.
