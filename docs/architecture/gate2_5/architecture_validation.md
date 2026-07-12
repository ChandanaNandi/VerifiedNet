# VerifiedNet — Gate 2.5: Architecture Validation

Status: **validation complete** (analysis only; no code, no folders beyond this document, no schemas)
Date: 2026-07-11
Inputs: Gate 0 (source/license/environment inventories), Gate 1 reuse matrix v2, Gate 2 Wave A
harvest plan + Appendices A–C, and the 30-file inspection record. No new repository inspection
was performed; every finding below cites evidence already gathered.

---

## 1. Executive readiness decision

The architecture is sound and implementable. The Gate 2 design survives adversarial review with
**zero blockers**, **seven HIGH** corrections that must shape Gate 3, and a handful of MEDIUM
items deferrable to Gate 4. The single most consequential finding: the mandated 16-contract set
**conflates data schemas with behavioral interfaces** and **omits the two manifest contracts that
Gate 4's own required artifacts demand** (RunManifest, EnvironmentManifest). Both are cheap to fix
now and expensive to fix after schemas ship.

**Verdict (also §20): `READY_FOR_GATE_3_WITH_REQUIRED_CHANGES`.**

## 2. Evidence supporting the current design

- The execution split (runner + adapter + permission separation) is directly validated by source
  evidence: NN's `_default_runner` is already an argv-only, docker-agnostic, timeout-bounded
  executor with rc-124/127 sentinels (App. A), and NN's tests inject `CommandRunner` — proving the
  testability claim.
- The fault lifecycle maps 1:1 onto observed, working STA behavior: precondition gate, bounded
  `wait_for_state(predicate, timeout, interval=0.5)` with monotonic deadline, loud inject-twice
  failure, no-op restore-twice, and the load-bearing `clear bgp` (App. C).
- The ground-truth matrix's ten facts each have a named deterministic prover; onset already
  requires *both* not-Established *and* wrong remote-AS read-back — the design anticipated the
  strongest criticism (Gate 2 §8, §10).
- The ledger/claims/manifest/logging spine is the most-tested code in the portfolio (CC
  `test_ledger.py`, `test_claims.py`, `test_manifest.py`, `test_logging.py` — Gate 1 C11/C31/
  C35/C56).
- Scope discipline held: nothing in the Gate 3 plan requires RAG, GraphRAG, SLMs, agents,
  dashboards, databases, SONiC, ACL, Batfish, EVPN, or SR Linux (Gate 2 §2 exclusions verified
  against §19/§24).

## 3. Confirmed weaknesses (evidence-backed)

| # | Weakness | Evidence | Priority |
|---|---|---|---|
| W1 | RunManifest/EnvironmentManifest have no contract home — Gate 4 requires both artifacts, but neither appears in the 16-contract list | Gate 4 brief ("run manifest, environment manifest"); contract list items 1–16 | HIGH |
| W2 | The 16 "contracts" mix data schemas with side-effecting interfaces (LabBackend has start/stop methods; IncidentRecord is pure data) — placing interfaces in `schemas/` would force runtime imports into the schema package | Gate 3 brief lists LabBackend methods; §19 package rules say schemas are DB-free/pure | HIGH |
| W3 | Gate 3 order puts CI + security guard **last** (step 9), contradicting the brief's "CI from the beginning" and Gate 2's own §24 note ("CI live from first commit") | Gate 2 §24 vs quality requirements | HIGH |
| W4 | Gate 3 step 4 schedules a live-lab integration test inside Gate 3, but Gate 2.5 instruction and gate discipline place all live-lab tests in Gate 4 | Gate 2 §24 step 4 vs Gate 4 test list | HIGH |
| W5 | No canonical JSON serialization rule exists, yet content hashes are everywhere (EvidenceRecord, manifests, approval digests) — hash stability across runs/platforms is unspecified | Gate 2 §9/§12; CC `_hash_manifest` sorts internally but VerifiedNet rule never stated | HIGH |
| W6 | Gate 1 vs Gate 2 classification inconsistency: Gate 1 has C11 (ledger) and C31 (claims) as RR/DR respectively, while Gate 2 App. B labels ledger.py and claims.py DIRECT_REUSE even though Gate 2 itself lists required modifications (torn-line tolerance; `trusted`-flag enforcement) | Gate 1 §12 table; Gate 2 §4 + frag_cc | HIGH (reconcile) |
| W7 | `inject()`/`restore()` have no explicit ledger-phase guard — inject-twice safety currently relies on preconditions being called first, which the contract does not force | Gate 2 §8 table (guard implied, not specified) | HIGH |
| W8 | Single `ping -c 1 -W 2` proves instantaneous reachability only; a transient scheduling hiccup fails fact 5 spuriously | App. C (EVL `loopback_reachable` semantics) | MEDIUM |
| W9 | Onset state poll can sample a transitional state once; no confirmation window specified | Gate 2 §8 (single-predicate poll) | MEDIUM |
| W10 | `/29` link addressing was inherited from NN, whose only rationale was parking a host gateway at `.6` — a need the two-router lab does not have | App. A (NN addressing plan) | MEDIUM |
| W11 | Identifier scheme unspecified: random UUIDs / timestamp-derived IDs inside the pipeline would break repeatability comparison | Gate 2 §11 repeatability test; no ID rule anywhere | MEDIUM |
| W12 | Orchestrator ownership (who runs lab→fault→collect→oracle→record) is unassigned; Gate 2 §19 has no package for it | Gate 2 §19 | MEDIUM (Gate 4 decision) |
| W13 | Recovery timing after `clear bgp` measures forced-reset convergence, not natural reconvergence — fine now (no timing claims), but must be annotated in the record or future latency work will misread it | App. C (~2s vs ~15s spike finding) | MEDIUM |

## 4. Rejected criticisms (considered and dismissed with evidence)

1. **"Pydantic in contracts is an implementation leak."** Rejected — the owner brief mandates
   "Prefer Pydantic v2"; the no-leak rule targets FRR/Docker/DB/model specifics, and the
   serialization boundary is JSON. No change recommended.
2. **"Eight packages is premature for a vertical slice."** Rejected — the AST security boundary
   (Gate 2 §14) *requires* package-level import separation (collectors must be scannable as
   mutation-free); merging runtime into labs would put the mutation surface inside a package
   collectors must import. No change recommended.
3. **"Derive BGP sessions from links + node ASNs instead of an explicit `sessions:` section."**
   Rejected — see §9; derivation is ambiguous for iBGP (same ASN), multi-session links, and
   session-targeted faults (STA's fault targets a *session*, not a link). No change recommended.
4. **"The process runner should understand Docker."** Rejected — NN's `_default_runner` is already
   container-agnostic (argv in, result out) and that is precisely what makes it fake-injectable
   in tests; container knowledge belongs to the adapter (Gate 2 §7). No change recommended.
5. **"Onset needs FRR log/notification evidence (BGP OPEN AS mismatch) to be sufficient."**
   Rejected as a requirement — state-set membership + wrong-AS config read-back + b-side
   unchanged read-back is already deterministic and sufficient; log evidence is optional
   enrichment, and requiring it would couple truth to log formats. No change recommended.
6. **"/31 (RFC 3021) addressing is the modern p2p choice."** Rejected — FRR supports it, but it
   buys nothing for a 2-node lab and risks tooling edge cases; /30 achieves the goal with zero
   novelty (see §9). (W10 changes /29→/30, not →/31.)
7. **"Aggregate deadline belongs in the runner."** Rejected — per-call bounds are runner-scope;
   run-level budgets are caller-scope, and CC's `Budget` (Gate 1 C15, tested) is the proven
   pattern for exactly this. Resolved accordingly in §8.
8. **"`clear bgp` invalidates recovery verification."** Rejected as stated — it changes *timing*
   semantics only, never the *state* facts (Established, routes present); Gate 4 makes no timing
   claims (latency eval is NAS/deferred, Gate 1 C44). Annotation requirement retained as W13.

## 5. Contract-by-contract validation

Legend: classification / Wave A-required fields / deferred fields / notes. All contracts:
serializable JSON, explicit `schema_version`, no FRR/Docker/DB/model leakage in core fields.

1. **LabBackend — KEEP_AS_PROPOSED (as a behavioral interface, relocated).**
   REQUIRED_FOR_GATE_3, but it is a Protocol/ABC with side effects (start/stop/reset/
   health_check/topology/execute_readonly/capture_environment_metadata) — it lives in
   `verifiednet.labs`, not `schemas/` (W2). Backend-portable: FRR/SONiC/EVPN/SRL all fit behind
   these methods (each has a bringup/health/exec analogue in the sources — Gate 1 C03–C07).
   No breaking change expected.
2. **TopologySpec — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. Data schema. Wave A fields: name,
   backend id, nodes (name, asn, loopback), links (endpoints, ifaces, ips), sessions, images.
   Deferred fields: pools/roles/host-networks (CC Clos machinery) until multi-node topologies.
   Known risk: it is a *reimplementation* (FabricSpec can't express p2p links — Gate 2 App. B);
   breaking-change risk mitigated by explicit `links:`/`sessions:` from day one.
3. **ScenarioDefinition — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3 (IncidentRecord mandates
   scenario id/template id/family). Wave A fields: id, family, template_id, version, parameters
   (wrong_asn, target session), timeouts. Deferred: parameter-space generators (Gate 5).
4. **FaultInjection — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. Data record of the mutation
   (target node/session, parameter, method, command transcript refs, timestamps). Distinct from
   the FaultScenario *interface* (faults package). No overlap: FaultInjection is what happened;
   ScenarioDefinition is what was asked.
5. **EvidenceRecord — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. Wave A fields: id (content-hash
   based — fixes CC `_emit` collision, Gate 2 §4), source metadata, raw payload ref + hash,
   normalized payload, capture timestamp, transcript link, phase tag.
6. **EvidenceBundle — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. Immutable once sealed;
   phase-grouped (baseline/onset/recovery); bundle hash.
7. **GroundTruth — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. Composed strictly of FaultInjection
   metadata + VerificationResult refs (Principles 11–12); no free-text fields.
8. **VerificationCheck — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. Claim + predicate + target +
   evidence requirements; predicate vocabulary seeded from CC `Predicate` (tested) with `ANY`
   flagged untested upstream (new tests mandatory).
9. **VerificationResult — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. Verdict + evidence refs +
   phase + check ref + timing.
10. **RecoveryResult — MERGE_WITH_ANOTHER_CONTRACT.** Evidence: Gate 2 §8 shows verify_recovery
    produces exactly the same shapes as verify_onset (EvidenceRecords + Claims/results). A
    separate contract would duplicate VerificationResult with a phase rename. Merge as:
    IncidentRecord's `recovery` section = restore metadata (method incl. clear-bgp flag — W13)
    + phase-tagged VerificationResults. *Owner approval required* since the 16 were mandated.
11. **IncidentRecord — KEEP_AS_PROPOSED.** REQUIRED_FOR_GATE_3. All 24 mandated fields; the
    schema is the platform's centerpiece and must ship with round-trip + version tests.
12. **DatasetManifest — DEFER_UNTIL_LATER_GATE (Gate 6).** Gate 4 needs *artifact hashing*
    (`sha256_file`, generic manifest builder → `common`), not dataset-level manifests. Evidence:
    Gate 4 artifact list contains run/environment manifests and incident JSONs, no datasets.
13. **DatasetSplit — DEFER_UNTIL_LATER_GATE (Gate 6).** Evidence: Gate 1 Wave B table.
14. **ModelPrediction — DEFER_UNTIL_LATER_GATE (Gates 7–8).** No model runs before Gate 7
    baselines; the post-truth LLM explanation in Gate 4 is not a scored prediction.
15. **BenchmarkRun — DEFER_UNTIL_LATER_GATE (Gate 7).**
16. **EvaluationReport — DEFER_UNTIL_LATER_GATE (Gate 7+).**
17. **RunManifest — ADD (new, REQUIRED_FOR_GATE_3).** W1. Fields: run id, git rev, lock hash,
    scenario ref, image digests, transcript hash, seeds, timestamps, acceptance status.
18. **EnvironmentManifest — ADD (new, REQUIRED_FOR_GATE_3).** W1. Fields: host OS/kernel/arch,
    runtime + versions, Python, FRR version, platform; fixes CC emit-script gaps (silent
    failures → fatal; OS/Python captured — Gate 2 §12).

Gate 3 therefore implements **12 data schemas** (TopologySpec, ScenarioDefinition, FaultInjection,
EvidenceRecord, EvidenceBundle, GroundTruth, VerificationCheck, VerificationResult, IncidentRecord,
RunManifest, EnvironmentManifest, + ExecResult as a runtime-owned serializable model) and
**4 behavioral interfaces** (LabBackend, FaultScenario, EvidenceCollector, Verifier).
DatasetExporter and ModelAdapter interfaces defer with their gates.

## 6. Package-boundary validation

| Package | Single responsibility | May import | Must never import | Gate 3? | Verdict |
|---|---|---|---|---|---|
| `schemas` | data contracts + versions | pydantic, stdlib | everything else (incl. runtime, labs) | yes | keep |
| `common` | logging, canonical JSON + hashing, errors, RunContext (ids/clock) | stdlib | schemas*, runtime, labs… | yes | keep (*see DAG note) |
| `runtime` | process runner, ExecResult, exec policies, transcript | common, stdlib | schemas? no need; labs/collectors/faults; docker SDKs | yes | keep |
| `labs` | LabBackend iface, topology rendering, frr-compose backend | schemas, common, runtime | verifiers, incidents, model libs | yes | keep |
| `collectors` | EvidenceCollector iface + FRR read-only collectors | schemas, common, runtime (read-only surface only) | mutation surface, faults, model libs | yes | keep |
| `verifiers` | claims + checks, pure verdict logic | schemas, common | runtime, labs, collectors (evidence arrives as data) | yes | keep |
| `faults` | FaultScenario iface + ASN-mismatch impl + ledger | schemas, common, runtime (mutation), labs, collectors, verifiers | incidents, model libs | yes | keep |
| `incidents` | oracle, IncidentRecord builder, manifest writers | schemas, common | runtime, labs, collectors, faults, model libs | yes | keep |

- **Circular-dependency test: none found.** The one candidate cycle (verifiers→collectors→
  verifiers) is broken by rule: verifiers consume evidence *as data* (schemas types); polling
  loops that gather-then-verify live in faults (orchestration), not in verifiers.
- **Merge check:** no merges recommended — each package carries a distinct AST-guard policy
  (Gate 2 §14), which is the non-cosmetic justification for the split.
- **Premature-split check:** `scenarios/` and `cli/` from the original target tree are correctly
  absent from Gate 3 (registry deferred to Gate 5; Gate 4 harness can be a module entry point).
  No change recommended.
- **Backend pluggability:** SONiC/EVPN/SRL backends add modules under labs/collectors/faults
  without touching schemas/common/runtime — verified against Gate 1 C03–C07 shapes.

## 7. Dependency DAG (corrected)

Evidence-based correction to the expected shape: `schemas` and `common` are **sibling roots**
(neither imports the other — schemas needs pydantic only; common needs stdlib only; the
schema-hash mechanism reimplementation lives in schemas, canonical-JSON bytes live in common and
are passed values, not imports).

```
   schemas (root)          common (root)
        ▲                     ▲
        │            ┌────────┘
        │            │
        │         runtime
        │            ▲
        ├──────┬─────┼──────────┐
        │      │     │          │
     verifiers │   labs     collectors
        ▲      │     ▲          ▲
        │      │     │          │
        └──── faults ┴──────────┘
                ▲
                │
            incidents (imports schemas + common only; consumes data)
                ▲
                │
        [Gate 4 orchestrator / future cli — imports everything]
```

(faults is the only package holding a MutationExecutor grant; incidents deliberately cannot reach
runtime at all.)

## 8. Runtime execution validation

The three-way split (runner / adapter / permission separation) is **confirmed as the correct
minimum** — every validated property maps to observed source behavior or a specific gap fix:
argv-only + no shell=True (NN runner as-is; EVL `_run` shell=True rejected on evidence),
stdout/stderr capture + rc sentinels (NN as-is), output cap (new, 64 KiB precedent from
`_cap_text`), transcripts (new, required by manifests), allow-lists (NN guards, parameterized),
DI for tests (NN `CommandRunner` pattern), cancellation (poll loops check deadline — no async
cancellation needed in Wave A), cleanup (finally-scoped teardown, ledger-tracked).

**Open-question resolutions:**

1. **Timeout: mandatory, no runner default.** The runner API requires an explicit timeout per
   call; adapters supply per-command-class defaults from policy. Evidence: NN's implicit 10s ×
   unbounded call count produced the ~160s worst case (App. A) — implicit defaults hide cost.
2. **Aggregate deadline: snapshot orchestrator, not runner.** Reuse CC `Budget` (tested) as a
   time/call budget at the collection layer. Runner stays single-call-scoped.
3. **Minimum ExecResult categories:** `OK`, `DENIED_COMMAND`, `DENIED_TARGET`, `TIMEOUT`,
   `TARGET_NOT_FOUND`, `NONZERO_EXIT`, `INTERNAL_ERROR`, plus a `truncated: bool` flag (not a
   status). Rationale: each category maps to a distinct caller decision; NN's rc-124/127
   sentinels split into TIMEOUT/TARGET_NOT_FOUND properly.
4. **Transcript failures:** for mutation commands — write-ahead: transcript append must succeed
   *before* execution, else refuse to execute (pattern: CC `guarded_mutation`, audit-write-before-
   mutate, tested in `test_rollback_audit.py`). For read-only commands — transcript failure fails
   the run at the end (evidence completeness broken) but does not block the read.
5. **Collectors with mutation executors: never.** Triple-enforced — constructor type (collectors
   accept ReadOnlyExecutor only), AST guard (collectors package bans mutation imports), runtime
   policy (executor refuses non-allow-listed verbs).
6. **Runner Docker-awareness: none.** `docker exec …` is just argv composed by the lab adapter;
   the runner can equally run `ping`. This keeps the fake-runner test strategy intact.

## 9. Topology validation

- **Addressing: change /29 → /30** (W10, MEDIUM). NN's /29 existed to park a gateway at `.6`
  (App. A) — irrelevant here; /30 is the conventional p2p choice; /31 rejected (§4.6).
- **Interface names (`eth1`), ASNs (65001/65002 private, wrong-ASN 65999 private and ≠ both):
  validated, no change recommended.**
- **Loopbacks advertised via `network <loopback>`: validated** — required for fact 10
  (route-restoration proof both ways).
- **FRR idioms** (`no bgp default ipv4-unicast`, `no bgp ebgp-requires-policy`, per-neighbor
  `activate`): validated against NN configs (App. A). No change recommended.
- **Image strategy: validated with one addition** — pin `frrouting/frr:v8.4.1` by *multi-arch
  manifest digest* and record the platform-resolved digest in EnvironmentManifest (arm64/amd64
  portability, Gate 0 §3 platform findings).
- **Compose project naming: per-run unique project name derived from run id; do NOT set
  `container_name`** (rejects NN's fixed-name pattern per Gate 0 §1) — gives parallel-run
  isolation and clean teardown scoping.
- **Resource cleanup: `compose down` + volume removal + post-cleanup assertion of zero
  project-labeled containers — validated (Gate 2 §11).**
- **Sessions question — answered: keep the explicit `sessions:` section.** Deriving sessions
  from links+ASNs is ambiguous for iBGP (equal ASNs), parallel sessions, and unnumbered links;
  and faults target *sessions* (STA's fault operates on a neighbor statement, App. C), so the
  session must be a first-class addressable object. Explicit wins for multi-session and iBGP
  futures. No change to Gate 2's design (it already proposed `sessions:`).

## 10. Fault-lifecycle validation

Falsification results:

- *Inject-twice fails loudly*: *currently unenforced* if `inject()` is called out of order — add
  explicit ledger-phase guard: `inject()` legal only from `PRECHECKED`, `restore()` legal from
  `INJECTED/ONSET_VERIFIED/RESTORING` (W7, HIGH). STA achieves this implicitly via preconditions;
  the contract must make it structural.
- *Restore-twice safe*: validated (STA no-op behavior, App. C). Keep.
- *Every post-injection failure attempts restoration*: validated (finally-scoped restore when
  phase ≥ INJECTING, Gate 2 §11) — with the addition that restore failure leaves ledger in
  `RESTORING` and the run must emit a rejected record + nonzero exit.
- *Every run tears down the lab*: validated (finally teardown + per-run project naming makes
  orphaned labs detectable).
- *Onset proves the mismatch, not just BGP-down*: validated — Gate 2 §8 already requires
  `remote-as==wrong AND not-Established`; §11 adds b-side unchanged read-back (fact 3).
- *Recovery proves BGP and routes*: validated — both directions required (fact 10).

Ambiguity resolution (requested): **fault operation** = inject/restore command execution
(faults, MutationExecutor); **fault verification** = onset/recovery checks (faults orchestrates
polling; verdict logic in verifiers); **evidence collection** = collectors, invoked at the three
collection points; **ground-truth construction** = incidents.oracle exclusively, consuming
FaultInjection + VerificationResults. No package does two of these jobs.

## 11. Ground-truth validation

All ten facts re-examined; per-fact provers stand as designed (Gate 2 §10). Challenge outcomes:

- **"BGP != Established" precision:** strengthen to state-set membership (∈ {Idle, Connect,
  Active}) observed on **two consecutive polls** (W9, MEDIUM) — eliminates single-sample
  transitional reads. Directly observed, sufficient with the config read-back conjunct.
- **Remote-ASN readability:** reliable — `show ip bgp summary json` exposes per-peer `remoteAs`
  (NN `_peers_from` parses it, App. A); cross-check against `show running-config` accepted as
  optional hardening, not required.
- **Ping sufficiency:** current single-echo is the weakest prover in the matrix — change to
  N-of-N consecutive successes (e.g., 3/3, all required; deterministic AND-semantics, the
  anti-pattern of EVL's rejected 4/15 floor) (W8, MEDIUM).
- **Route restoration both ways:** confirmed required and already designed (fact 10 covers both
  directions).
- **`clear bgp` and timing validity:** state facts unaffected; annotate restore method in the
  record (W13, MEDIUM). No timing claims exist to invalidate (Gate 1 C44 NAS).
- **Injection metadata alone proving single-sided change:** correctly NOT relied upon — fact 3
  pairs mutation metadata with router_b config read-back. Validated.

Rejection semantics: failure of facts 1–5 (preconditions/baseline) or 8–10 (recovery) → rejected
record with reason; failure of facts 6–7 (onset) → rejected record *and* immediate restore path.
**No LLM/model/RAG output anywhere in the chain — reconfirmed** (explanation generation is
post-verdict and quarantined by package import rules).

## 12. Evidence and incident ownership

| Artifact | Constructor | Notes |
|---|---|---|
| EvidenceRecord | collectors (at capture) | capture timestamp owned here |
| EvidenceBundle | fault-lifecycle orchestration (faults) seals per-phase bundles | immutable after seal |
| Verification execution | faults invokes; verdict logic in verifiers | results are data |
| GroundTruth | incidents.oracle only | from FaultInjection + verdicts |
| IncidentRecord | incidents.builder | 24 fields; accepted/rejected + reason |
| Run/Environment manifests | incidents.manifests | write-ahead transcript rule (§8.4) |
| run_id, sequence ids, clock | common.RunContext (single authority) | deterministic ids (W11) |

Mutable-shared-state risks found: (a) EvidenceBundle must reject post-seal appends (enforced in
model, tested); (b) two potential timestamp authorities (collector capture vs RunContext) —
resolved: capture timestamps are collector-local data; run-level ordering comes only from
RunContext sequence numbers. No duplicated ownership remains.

## 13. Determinism risks (confirmed only)

| Risk | Evidence | Impact | Fix when | Minimal mitigation |
|---|---|---|---|---|
| Docker startup ordering | compose starts unordered (NN/EVL labs) | baseline flakes | Gate 3 design / Gate 4 observed | health-poll gate before baseline (already designed) |
| BGP convergence variance | STA ~2s vs ~15s spike data | timeout tuning | Gate 4 | bounded polls; repeatability compares verdicts, never timings |
| Single-sample onset read | Gate 2 §8 | false onset | Gate 3 (spec) | two-consecutive-poll rule (W9) |
| Single-echo ping | EVL semantics | false precondition fail | Gate 3 (spec) | 3/3 all-success (W8) |
| Hash instability from JSON serialization | no canonical rule stated | irreproducible hashes | **Gate 3 (W5, HIGH)** | canonical JSON (sorted keys, UTF-8, fixed float repr) in common |
| Timestamp/UUID identifiers | no ID rule | repeatability comparison breaks | Gate 3 (W11) | RunContext: run_id recorded-not-hashed; content-hash or run_id+seq for inner ids |
| Compose project names vary per run | per-run naming (by design) | transcripts differ across runs | accepted | repeatability compares normalized verdicts, volatile fields excluded |
| Image platform selection | Gate 0 §3 arm64 findings | cross-host divergence | Gate 4 | record platform-resolved digest (§9) |
| Concurrent collectors | none in Wave A (sequential by design) | n/a | n/a | keep sequential; aggregate Budget bounds total time |
| Retries | none in runner; polling only | n/a | n/a | keep: no auto-retry anywhere |

No other claimed nondeterminism sources survived evidence checks (filesystem traversal, log
ordering, random ports: not present in the Wave A design — collectors sort/bound outputs, no
ports are published by the lab).

## 14. Reuse-classification changes (evidence-required only)

| Symbol | Was (Gate 2) | Now | Evidence |
|---|---|---|---|
| CC `Ledger` family | copy nearly unchanged / App. B "DIRECT_REUSE" | **copy with modifications (REFACTOR_AND_REUSE)** | Gate 2 itself mandates two changes: torn-line tolerance + Phase-enum alignment; also reconciles W6 with Gate 1 C11=RR |
| CC `claims.verify` + models | copy nearly unchanged / App. B "DIRECT_REUSE" | **copy with modifications (REFACTOR_AND_REUSE)** | `trusted`-flag enforcement is a semantic change; `ANY` predicate untested upstream (frag_cc); reconciles with new-test mandate |
| CC `DatasetManifest` model | copy nearly unchanged | **split**: `sha256_file` stays copy-nearly-unchanged (→ common); the manifest *model* is copy-with-modifications and its contract **defers to Gate 6** (§5.12) | field renames + deferral evidence |
| NN `_assert_show_command` | copy nearly unchanged | **copy with modifications** | Gate 2's own modification list (policy-object input; dead multi-word tokens dropped) contradicts the "nearly unchanged" verb |

Explicitly re-examined and left unchanged (no change recommended): CC logging (additive context
fields only), NN `CommandRunner`/`_default_runner` (wrapping, not modification), NN parsers,
STA `wait_for_state`, STA inject/restore sequences, EVL `bgp_underlay_established`/
`loopback_reachable`/`container_running`.

## 15. Testing-boundary validation

**Must exist before any real lab run (Gate 3, fake-runner only):** unit (ported CC/NN suites,
re-baselined for the trusted-flag and evidence-id changes), contract (JSON round-trip +
schema-version for all 12 schemas — new), property (IPAM uniqueness/determinism re-targeted at
TopologySpec; claim-predicate properties incl. ANY), failure-path (denied/timeout/not-found/
nonzero via fake runner), security (consolidated AST guard + its self-tests).

**Require the real lab (Gate 4):** integration (lab up/healthy/down; live parser goldens),
repeatability (full loop ×2), cleanup-after-failure (kill mid-INJECTED), platform (arm64/amd64
digest checks). **Correction (W4):** Gate 2 §24 step 4 scheduled a live integration test inside
Gate 3 — moved to Gate 4's opening step.

**Copied-test honesty check:** three ported suites would otherwise preserve source assumptions —
claim tests must be re-baselined after the `trusted` fix (else they test CC's old semantics);
fabric tests must drop CC's 2s4l constants (retained as CC fixtures per Gate 2 §18); NN parser
goldens are source-derived until re-recorded against live plain-FRR output in Gate 4 (flagged as
such in the test names/markers).

## 16. Corrected Gate 3 implementation order

1. Repo scaffold + `pyproject.toml`/lock + **CI workflow + consolidated AST security guard**
   (runs from first commit — fixes W3)
2. `schemas`: 12 data schemas + contract tests (incl. RunManifest/EnvironmentManifest — W1)
3. `common`: canonical JSON + hashing (W5), logging, errors, RunContext (W11)
4. `runtime`: runner, ExecResult taxonomy (§8.3), policies, write-ahead transcript (§8.4) +
   unit/failure tests (fake runner)
5. `labs`: TopologySpec, renderer, frr-compose backend — fake-runner tests only (W4)
6. `collectors`: FRR family + source-derived goldens (marked)
7. `verifiers`: claims (modified) + checks + polling predicates
8. `faults`: ledger (modified) + FaultScenario + ASN-mismatch impl with phase guards (W7)
9. `incidents`: oracle, IncidentRecord builder, manifest writers
   — **No live lab run anywhere in Gate 3.**

## 17. Required changes before/during Gate 3

**BLOCKER:** none.

**HIGH (must shape Gate 3):**
1. Add RunManifest + EnvironmentManifest contracts (W1).
2. Separate data schemas from behavioral interfaces; interfaces live in their owning packages (W2).
3. CI + AST guard move to step 1 of the implementation order (W3).
4. All live-lab tests move to Gate 4; Gate 3 is fake-runner only (W4).
5. Canonical JSON serialization rule for every content hash, defined once in common (W5).
6. Reconcile Gate 1/Gate 2 classification inconsistencies per §14 (W6).
7. Ledger-phase guards inside `inject()`/`restore()` (W7).

**MEDIUM (before Gate 4):** /30 addressing (W10); 3/3 ping rule (W8); two-consecutive-poll onset
confirmation (W9); deterministic ID scheme (W11); restore-method annotation incl. clear-bgp (W13);
orchestrator ownership decision (W12).

**LOW:** none recommended (per the change-priority rule, no elegance-only changes).

## 18. Items approved unchanged

Scope of the vertical slice (§ Part 1: nothing further deferrable found; nothing missing);
the runner/adapter/permissions split; the 8-package layout and import rules; explicit
`sessions:` in TopologySpec; ASN plan and FRR idioms; the fault-lifecycle method set and its
STA-derived polling semantics; the ten-fact ground-truth matrix structure (with W8/W9
strengthenings); evidence/incident ownership as assigned in §12; the rejection semantics;
all Gate 2 §18 rejections (reconfirmed, incl. the 4/15 floor); the provenance plan.

## 19. Risks accepted for Gate 4

FRR-in-SONiC → plain-FRR drift in vtysh JSON fields (goldens re-recorded live); convergence
timing variance on LinuxKit vs Linux hosts; multi-arch digest resolution differences; the
consolidated AST guard's dynamic-import blindness (documented, runtime-policy mitigated);
single-maintainer upstream repos force-push risk (content hashes recorded at harvest).

## 20. Final verdict

**`READY_FOR_GATE_3_WITH_REQUIRED_CHANGES`**

Blockers: none. The seven HIGH changes in §17 must shape Gate 3 exactly as specified in the
corrected implementation order (§16).
