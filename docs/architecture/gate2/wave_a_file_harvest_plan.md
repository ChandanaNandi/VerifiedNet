# VerifiedNet — Gate 2: Wave A File-Level Harvest Plan

Status: **audit complete** (inspection + design only; no code copied, no packages created, no source repo modified)
Date: 2026-07-11
Baseline: Gate 0 pinned commits; Gate 1 approved reuse matrix (v2); owner decisions: Apache-2.0 outbound (proposed, files not yet created), minimal two-router FRR lab.

---

## 1. Executive summary

Twenty-six files across four repos were read completely (NeuroNOC 11, ClosCall 15 including tests,
STA 3, EVL 1) and analyzed against the 28-point checklist; full per-file tables are in Appendices
A–C. The Wave A verdict: the first vertical slice can be assembled almost entirely from verified
source patterns, with three genuinely new constructions — the 16 Gate 3 contracts, the
ground-truth oracle assembly, and the IncidentRecord builder.

Key findings that shape Gate 3:

1. **NeuroNOC's runner is the right execution core and is nearly liftable**: argv-list
   `subprocess.run` (never shell), 10s timeout, rc-124/127 sentinels, injectable
   `CommandRunner` type, and a token-ban + `show `-prefix allow-list. Gaps to fix: no aggregate
   deadline (worst case ~160s per snapshot), no runner-level output cap (caps live in parsers,
   64 KiB), read-only-only (Wave A needs a separately-permissioned mutation path for
   inject/restore), no transcript recording.
2. **STA's `bgp_asn_mismatch.py` is a direct lifecycle blueprint** — preconditions, inject,
   `wait_for_state(predicate, timeout, interval=0.5)` bounded polling, restore, with the
   load-bearing detail that `clear bgp <peer>` after restore cuts reconvergence ~15s→~2s. Its
   vtysh idioms translate to plain FRR nearly verbatim. Inject-twice fails loudly; restore-twice
   is a no-op — exactly the idempotency contract Wave A needs.
3. **ClosCall supplies the tested bookkeeping/verification/provenance spine**: `Ledger` (DIRECT),
   `claims.verify` (DIRECT, with the `trusted` flag enforcement fix and the untested `ANY`
   predicate flagged), `Budget`/`ToolContext` (DIRECT), `build_manifest`/`sha256_file` (DIRECT),
   `JsonFormatter` (DIRECT). `FabricSpec`'s address *math* is reusable but its *grammar* cannot
   express a 2-router point-to-point topology (no `links:` section; hardcoded 2-spine formula;
   `extra=forbid` makes Clos fields mandatory) — the TopologySpec contract is a reimplementation
   informed by it.
4. **Explicit rejections confirmed by line-level evidence**: EVL `_run` (shell=True, f-string
   interpolation, no timeout), EVL `host_can_ping` ≥4/15 floor (passes a 73%-loss data plane —
   would mask partial faults in the truth path), NN lab.sh's manual convergence waiting, CC
   emit-script silent git/docker failures.
5. **The AST security scanner exists in triplicate in NeuroNOC** and must be consolidated into one
   parameterized guard (8-lib execution ban + telemetry's extended 13-lib set as policy input).

## 2. Scope and exclusions

In scope: the 21 Wave A capability areas and only the files listed in the gate instruction.
Excluded (deferred to Wave B per approved Gate 1 matrix): SONiC DB collectors (beyond the
BGP-evidence patterns of `collect_bgp_summary`), ASIC/ACL, Batfish, dataset splitting, approval
binding, telemetry, RAG/GraphRAG/SLM/model adapters/agents. EVL EVPN-specific checks
(`evpn_imet_from_peer`, `vxlan_iface_exists`, `bridge_fdb_has_her_to`) were read but deferred to
the EVPN backend; no UI/DB/ML/RAG/agent files were inspected.

## 3. Complete file inventory

| # | Repo@commit | Path | Class | Full analysis |
|---|---|---|---|---|
| 1 | NN@5f24447 | `backend/app/lab/collector.py` | REFACTOR_AND_REUSE | App. A |
| 2 | NN@5f24447 | `backend/tests/test_lab_collector.py` | REFACTOR_AND_REUSE (21B e2e test RETAIN) | App. A |
| 3 | NN@5f24447 | `infra/lab/docker-compose.lab.yml` | DESIGN_REFERENCE_ONLY | App. A |
| 4 | NN@5f24447 | `infra/lab/scripts/lab.sh` | REFACTOR_AND_REUSE | App. A |
| 5 | NN@5f24447 | `infra/lab/configs/core-1/frr.conf` (+edge-1) | DESIGN_REFERENCE_ONLY | App. A |
| 6 | NN@5f24447 | `infra/lab/configs/{edge-2,branch-1}/frr.conf` | REJECT (redundant templates) | App. A |
| 7 | NN@5f24447 | `infra/lab/configs/*/daemons` | DIRECT_REUSE | App. A |
| 8 | NN@5f24447 | `backend/app/core/config.py` (collector-relevant parts) | RETAIN_ONLY_IN_ORIGINAL_REPO | App. A |
| 9 | NN@5f24447 | `backend/tests/test_validation.py` | REFACTOR_AND_REUSE | App. A |
| 10 | NN@5f24447 | `backend/tests/test_remediation.py` | REFACTOR_AND_REUSE | App. A |
| 11 | NN@5f24447 | `backend/tests/test_telemetry.py` | REFACTOR_AND_REUSE | App. A |
| 12 | CC@d192bf3 | `src/closcall/domain/fabric.py` | REFACTOR_AND_REUSE | App. B |
| 13 | CC@d192bf3 | `tests/unit/test_fabric_ipam.py` | REFACTOR (invariants) / RETAIN (2s4l constants) | App. B |
| 14 | CC@d192bf3 | `tests/unit/test_fabric_validate.py` | DESIGN_REFERENCE_ONLY | App. B |
| 15 | CC@d192bf3 | `src/closcall/chaos/ledger.py` | DIRECT_REUSE | App. B |
| 16 | CC@d192bf3 | `tests/unit/test_ledger.py` | DIRECT_REUSE | App. B |
| 17 | CC@d192bf3 | `src/closcall/evidence/claims.py` | DIRECT_REUSE | App. B |
| 18 | CC@d192bf3 | `tests/unit/test_claims.py` | DIRECT_REUSE | App. B |
| 19 | CC@d192bf3 | `src/closcall/evidence/tools.py` (Wave A subset) | REFACTOR_AND_REUSE | App. B |
| 20 | CC@d192bf3 | `tests/unit/test_tools.py` | copy with modifications | App. B |
| 21 | CC@d192bf3 | `src/closcall/datasets/manifest.py` | DIRECT_REUSE | App. B |
| 22 | CC@d192bf3 | `tests/unit/test_manifest.py` | DIRECT_REUSE | App. B |
| 23 | CC@d192bf3 | `src/closcall/observability/logging.py` | DIRECT_REUSE | App. B |
| 24 | CC@d192bf3 | `tests/unit/test_logging.py` | DIRECT_REUSE | App. B |
| 25 | CC@d192bf3 | `scripts/emit_manifest.py`, `scripts/emit_manifest_v3.py` | DESIGN_REFERENCE_ONLY | App. B |
| 26 | CC@d192bf3 | `src/closcall/datasets/schemas.py` (schema_hash mechanism) | reimplement mechanism | App. B |
| 27 | STA@eb4c818 | `faults/bgp_asn_mismatch.py` | REFACTOR_AND_REUSE | App. C |
| 28 | STA@eb4c818 | `collectors/sonic_state.py` (split verdicts) | RR / RETAIN / DREF by symbol | App. C |
| 29 | STA@eb4c818 | `main.py` (lifecycle parts) | DESIGN_REFERENCE_ONLY | App. C |
| 30 | EVL@5b5a479 | `validate/checks.py` (generic patterns only) | split: 3 RR / 2 REJECT / 3 deferred | App. C |

## 4. Symbol-level harvest table

Consolidated Wave A decisions (full per-repo tables in Appendices; verbs per the harvest rule).

| Symbol | Source | Verb | Key modifications | Wave A role |
|---|---|---|---|---|
| `CommandRunner`, `_default_runner` | NN collector.py | copy nearly unchanged | add output cap + transcript hook; keep argv-only, 10s timeout, rc sentinels | execution core |
| `_assert_show_command`, `_FORBIDDEN_VTYSH_TOKENS` | NN collector.py | copy nearly unchanged | policy object input; drop dead multi-word tokens | read-cmd allow-list |
| `_assert_known_router` | NN collector.py | copy with modifications | allow-set from TopologySpec, not module constant | container allow-list |
| `_vtysh_json`, `_vtysh_text` | NN collector.py | copy with modifications | container-name prefix from topology; deadline param | FRR query layer |
| `_peers_from`, `_scrape_router`, `_scrape_route_table`, `_cap_text` | NN collector.py | copy with modifications | detach NN schemas → EvidenceRecord; keep 64 KiB caps | BGP/route/interface evidence |
| `collect_lab_snapshot` | NN collector.py | reimplement behavior from specification | split scrape from persistence; add aggregate deadline | snapshot orchestration |
| `_bgp_neighbor_mode` (heal gotcha: `no neighbor X shutdown`) | NN lab.sh | copy nearly unchanged | as FRR idiom documentation + mutation adapter detail | restore correctness |
| `lab.sh` up/down/inject/restore verbs | NN | use only as architectural reference | replace with LabBackend impl + convergence polling (lab.sh has zero polling) | lifecycle shape |
| `daemons` file (bgpd=yes etc.) | NN configs | copy nearly unchanged | none | FRR container config |
| `frr.conf` idioms (`no bgp default ipv4-unicast`, `no bgp ebgp-requires-policy`, per-neighbor `activate`, `network <loopback>`) | NN configs | use only as architectural reference | re-rendered from TopologySpec for 2 routers | healthy config |
| `_scan_files_for_execution_imports` (×3) | NN tests | copy with modifications | consolidate 3 copies → 1 parameterized guard; policy = banned-libs list + protected packages | security boundary |
| `wait_for_state(predicate, timeout, interval)` | STA bgp_asn_mismatch.py | copy with modifications | typed predicate → VerificationCheck; raise-on-timeout at caller preserved | onset/recovery polling |
| `_check_container_running`, `_peer_reachable` | STA | reimplement behavior from specification | FRR/compose equivalents via runner | preconditions |
| `_apply_inject` / `_apply_restore` vtysh sequences | STA | copy with modifications | same vtysh grammar against FRR container; ASN/peer from scenario config; keep `clear bgp <peer>` post-restore | fault mutation |
| `read_peer_state`, `_read_peer_raw` | STA | copy with modifications | parse FRR `show ip bgp summary json` via NN parser instead of raw string handling | fault-state reads |
| `Scenario`, `take_snapshot`, `print_snapshot` | STA main.py | use only as architectural reference | lifecycle order harvested; snapshot becomes hashed EvidenceBundle | run flow |
| `Ledger`, `LedgerRecord`, `Phase`, `now_record` | CC ledger.py | copy nearly unchanged | add torn-line tolerance on read; align Phase names to FaultScenario states | undo/cleanup ledger |
| `Claim`, `Predicate`, `Verdict`, `Snapshot`, `verify`, `committable` | CC claims.py | copy nearly unchanged | enforce `trusted` flag inside `verify`; add tests for `ANY` predicate (untested upstream) | ground-truth checks |
| `Budget`, `BudgetExhausted`, `ToolContext`, `Record`, `EvidenceSource` | CC tools.py | copy nearly unchanged | rename to contract vocabulary | evidence framework |
| `_emit` envelope | CC tools.py | copy with modifications | fix evidence_id collision (`source:subject:metric:at` not unique) → add content hash | evidence provenance |
| `get_interface_state`, `get_bgp_state`, `get_log_events` | CC tools.py | copy with modifications | back with FRR collector source | evidence tools |
| `DatasetManifest`, `build_manifest`, `sha256_file` | CC manifest.py | copy nearly unchanged | field-name alignment to contracts | manifests |
| `JsonFormatter`, `configure_logging` | CC logging.py | copy nearly unchanged | add run_id/incident_id context fields | structured logging |
| `allocate` address math (index formulas) | CC fabric.py | copy with modifications | generalized to explicit link list | deterministic IPAM |
| `FabricSpec` grammar | CC fabric.py | reimplement behavior from specification | TopologySpec with `links:` section, role-agnostic nodes, optional fields | topology contract |
| `test_fabric_ipam.py` invariants (uniqueness, determinism) | CC | copy with modifications | re-targeted at TopologySpec | property tests |
| emit_manifest v1/v3 capture set | CC scripts | use only as architectural reference | new RunManifest/EnvironmentManifest; add OS/Python/per-stage seeds; fail loudly on git/docker errors (silent today) | run/env manifests |
| `schema_hash` mechanism | CC schemas.py | reimplement behavior from specification | same idea, contract-owned | schema versioning |
| `collect_bgp_summary` | STA collectors | copy with modifications | via shared runner; FRR target | BGP evidence (secondary) |
| `bgp_underlay_established` (vtysh JSON `peers[ip].state == "Established"`) | EVL checks.py | copy with modifications | run through bounded runner; field access hardened | BGP health/onset check |
| `loopback_reachable` (`ping -c 1 -W 2 [-I src]`) | EVL checks.py | copy with modifications | through runner; deterministic exit-code semantics kept | reachability evidence |
| `container_running` | EVL checks.py | copy with modifications | through runner | precondition |
| `_run` (shell=True, f-string, no timeout) | EVL checks.py | **reject** | — | — |
| `host_can_ping` (≥4/15 floor) | EVL checks.py | **reject** | — | rejected truth criterion |
| Redis DB collectors, `CONTAINER` constants | STA collectors | retain only in original repo | — | Wave B |
| `edge-2`,`branch-1` frr.conf | NN | reject (redundant) | — | — |

## 5. Source dependency graph

```
                         Wave A dependency flow (sources → VerifiedNet constructs)

 NN collector.py ──runner/allow-list──► Bounded Executor ◄──rejects── EVL _run (shell=True)
        │                                    │
        ├─vtysh parsers──► FRR Evidence Collectors ◄──secondary── STA collect_bgp_summary
        │                                    │                  ◄──checks── EVL bgp_underlay_established,
        │                                    │                              loopback_reachable
 NN lab.sh + frr.conf ──idioms──► Two-Router Lab Backend ◄──grammar reimpl── CC fabric.py (math copied)
        │                                    │
 STA bgp_asn_mismatch.py ──lifecycle──► FaultScenario impl ──phases──► CC Ledger (copied)
        │                                    │
 STA wait_for_state ──polling──► Onset/Recovery Verifiers ──claims──► CC claims.verify (copied)
                                             │
                              Evidence framework (CC tools.py: Budget/ToolContext/EvidenceSource)
                                             │
                              EvidenceBundle → GroundTruth (oracle: NEW) → IncidentRecord (NEW)
                                             │
                CC manifest.py (copied) + emit_manifest patterns (reference) → Run/Env Manifests
                                             │
                CC logging.py (copied) ──── observability across all stages
                NN AST scans (consolidated) ─ security boundary over all mutation-capable packages
```

Notable independence: STA's fault imports stdlib only and calls no collector functions (its own
`_read_peer_raw`); the fault, collectors, and ledger are cleanly separable — the contracts join them.

## 6. Minimal two-router lab design (design only — not implemented)

Derived from NN's lab (image `frrouting/frr:v8.4.1`, to be **digest-pinned** at Gate 3 build time)
and NN config idioms. Values below are **example scenario/topology configuration**, never Python
constants.

```yaml
# Example TopologySpec input (illustrative values, final schema in Gate 3)
schema_version: 1
topology:
  name: verifiednet-frr-2r
  backend: frr-compose
nodes:
  - name: router_a
    asn: 65001
    loopback: 10.255.0.1/32     # advertised — needed for route-restoration verification
  - name: router_b
    asn: 65002
    loopback: 10.255.0.2/32     # advertised
links:
  - a: {node: router_a, iface: eth1, ip: 172.30.0.1/29}
    b: {node: router_b, iface: eth1, ip: 172.30.0.2/29}
sessions:
  - type: ebgp
    a: {node: router_a, peer_ip: 172.30.0.2, remote_as: 65002}
    b: {node: router_b, peer_ip: 172.30.0.1, remote_as: 65001}
images:
  frr: "frrouting/frr:v8.4.1@sha256:<pinned-at-gate-3>"
```

Healthy FRR config (rendered, per NN idioms): `router bgp <asn>`; `no bgp default ipv4-unicast`;
`no bgp ebgp-requires-policy`; `neighbor <peer_ip> remote-as <remote_as>`; `address-family
ipv4 unicast` with per-neighbor `activate` and `network <loopback>`; daemons file `bgpd=yes`.

- **Wrong-ASN mutation (router_a only):** `vtysh -c "configure terminal" -c "router bgp 65001"
  -c "neighbor 172.30.0.2 remote-as 65999"` (65999 = scenario parameter, must differ from
  router_b's real local ASN and be a valid private ASN).
- **Restore mutation:** same sequence with `remote-as 65002`, followed by
  `clear bgp 172.30.0.2` (STA-proven: cuts reconvergence ~15s→~2s).
- **Health checks (preconditions):** both containers running; both interfaces oper-up; link ping
  `ping -c 1 -W 2 172.30.0.2` deterministic; `show ip bgp summary json` →
  `ipv4Unicast.peers["172.30.0.2"].state == "Established"` on both; each router's loopback
  present in the other's `show ip route json`.
- **Onset checks:** router_a's configured remote-as reads 65999 (`show run` / bgp summary JSON);
  session **not** Established (state in {Idle, Active, Connect} — polled, bounded);
  interfaces still oper-up; link ping still succeeds (isolates control-plane fault).
- **Recovery checks:** remote-as reads 65002; session Established (bounded poll, timeout 60s,
  interval 0.5s); router_b loopback route present again in router_a's table (and vice versa).
- **Evidence sources:** vtysh JSON (BGP summary, route table), `ip -j link`/interface state via
  runner, container inventory, ping transcript, FRR log tail.
- **Environment metadata captured:** host OS/kernel/arch, container runtime + version, image
  digests, FRR version string, compose project name, VerifiedNet git rev, lock hash, Python
  version, UTC timestamps (fixes CC emit gaps: OS/Python captured; failures fatal, not silent).
- **Cleanup:** `down` destroys containers + networks; ledger records every phase; cleanup runs in
  `finally`; repeat-run safe (fresh compose project); post-cleanup check asserts no lab
  containers remain.

## 7. Runtime command-execution recommendation

**Recommendation for Gate 3: split into (1) a low-level process runner + (2) a lab command
adapter.** Justification: the runner's concerns (argv-only, timeout, output cap, rc sentinels,
transcript, structured errors) are backend-independent and must be testable with a fake runner —
exactly how NN's tests inject `CommandRunner`. The adapter's concerns (which container, which
command grammar, read-only vs mutation permission, allow-list policy) depend on the lab backend
and TopologySpec. A single "general runtime service" would force lab policy into core; a purely
"lab-owned service" would duplicate the runner per backend. Required properties (all designed,
none implemented): injectable runner; explicit argv; allow-list policy object (command tokens +
target containers from topology); per-call timeout with default; max-output-bytes; stdout/stderr
capture; exit code; structured `ExecResult` error taxonomy (denied/timeout/not-found/nonzero);
no silent exception swallowing; **separate ReadOnlyExecutor and MutationExecutor grants** (the
fault path receives mutation rights, collectors never do — enforced by the consolidated AST guard
plus runtime policy); every call appended to a run transcript with timestamps and env/runtime
metadata for the manifest.

## 8. Fault lifecycle mapping (BGP remote-AS mismatch → FaultScenario)

| Method | Inputs | Commands (via MutationExecutor except where noted) | Evidence | Timeout | Failure behavior | Ledger transition | Cleanup req | Idempotency |
|---|---|---|---|---|---|---|---|---|
| `validate_preconditions()` | ScenarioDefinition, TopologySpec | (read-only) container ls; `show ip bgp summary json` ×2; `ping -c1 -W2`; iface state | precondition EvidenceRecords | 30s total | raise PreconditionFailed → run rejected, nothing injected | `PENDING→PRECHECKED` | none | pure reads, repeatable |
| `inject()` | wrong_asn param | vtysh configure sequence on router_a | post-command config read-back | 10s cmd | raise InjectFailed → ledger stays `INJECTING` → cleanup path | `PRECHECKED→INJECTING→INJECTED` | restore required from here on | second inject → loud failure (precondition detects non-healthy state) — matches STA |
| `verify_onset()` | expected wrong ASN, peer ip | (read-only) poll bgp summary JSON until not-Established AND remote-as==wrong | onset EvidenceRecords + Claims | 30s, 0.5s interval | timeout → OnsetNotVerified → auto-restore → run rejected record | `INJECTED→ONSET_VERIFIED` | restore still required | poll is pure |
| `restore()` | correct ASN | vtysh revert sequence + `clear bgp <peer>` | config read-back | 10s cmd | raise RestoreFailed → ledger `RESTORING` persists → operator-visible, cleanup test asserts | `ONSET_VERIFIED→RESTORING→RESTORED` | this IS the cleanup | second restore → no-op (matches STA) |
| `verify_recovery()` | expected ASN, routes | (read-only) poll Established; poll loopback route present both sides | recovery EvidenceRecords + Claims | 60s, 0.5s interval | timeout → RecoveryNotVerified → run rejected record; lab torn down anyway in `finally` | `RESTORED→RECOVERY_VERIFIED` | teardown in finally | poll is pure |

Source fidelity: lifecycle order, polling mechanics (monotonic deadline; poller returns last
state, caller raises), and both idempotency behaviors are STA's, re-expressed; **no SONiC command
is copied blindly** — all mutation grammar is FRR vtysh validated against NN's configs.

## 9. Evidence collection plan

Collectors (all through ReadOnlyExecutor, all bounded, all stamped with source metadata):
BGP summary (NN `_peers_from` shape), route table (NN `_scrape_route_table`, 64 KiB cap),
interface state (`ip -j link` parse), reachability (single deterministic ping, exit-code
semantics from EVL `loopback_reachable`), FRR log tail (bounded lines), container inventory.
Raw evidence: every collector's raw stdout preserved verbatim as content-hashed EvidenceRecords
inside an EvidenceBundle (fixes STA's unhashed dict snapshots); normalization is a separate pure
step producing typed records (CC tools.py envelope with the evidence-id collision fixed).
Collection points: pre-injection (baseline), post-onset, post-recovery — mirroring STA's
BEFORE/AFTER snapshots plus the post-restore snapshot STA lacks (gap closed).

## 10. Ground-truth proof matrix

Every fact proven by deterministic collector/verifier output only. **No LLM or model output
appears anywhere in this chain** (LLM use is permitted only after these verdicts, for language
variants/explanation, per the brief).

| # | Fact | Proven by | Evidence |
|---|---|---|---|
| 1 | Healthy BGP Established before injection | precondition check: bgp summary JSON both routers | baseline EvidenceRecords |
| 2 | router_a originally expected router_b's real ASN | baseline config read (`show run` bgp stanza / summary remote-as field) | baseline config evidence |
| 3 | Only router_a's remote-AS changed | injected-mutation metadata (scenario param) + router_b config read-back unchanged | mutation record + b-side config evidence |
| 4 | Interfaces stayed operational | interface-state collector at all 3 collection points | iface EvidenceRecords |
| 5 | IP reachability stayed healthy | deterministic link ping at all 3 points | ping transcripts |
| 6 | Configured remote-AS ≠ router_b's real local ASN | claim: onset config read (65999) vs router_b `local AS` field | onset evidence + claim verdict |
| 7 | BGP not Established after mutation | onset poll result (state ∈ {Idle,Active,Connect}) | onset EvidenceRecords |
| 8 | Correct ASN restored | restore read-back (remote-as==65002) | restore evidence |
| 9 | BGP returned to Established | recovery poll (Established) | recovery EvidenceRecords |
| 10 | Expected route exchange returned | route-table check: each loopback in peer's table | recovery route evidence |

Facts 1–10 each become a `Claim` evaluated by `claims.verify` against the relevant `Snapshot`;
GroundTruth = injected-fault metadata + the full verdict set; any non-PASS verdict on facts
1–5 (preconditions) or 8–10 (recovery) yields the **deliberately rejected record** path.

## 11. Undo/cleanup plan

CC `Ledger` (copied nearly unchanged, torn-line tolerance added) records every phase transition
from §8. Cleanup invariants: restore attempted in `finally` whenever phase ≥ INJECTING; lab
teardown in `finally` always; cleanup-after-failure test kills the run mid-INJECTED and asserts
(a) restore ran, (b) no containers remain, (c) ledger shows the interrupted phase; repeatability
test runs the full loop twice asserting identical verdict sets and no state bleed.

## 12. Incident/provenance/manifests plan

IncidentRecord (new builder, Gate 3 contract) carries all 24 mandated fields; raw + normalized
evidence embedded by content hash. RunManifest: VerifiedNet git rev, lock-hash, scenario id +
params, image digests, command transcript hash, seeds, timestamps. EnvironmentManifest: host
OS/kernel/arch, runtime versions, Python, FRR version. Patterns from CC emit_manifest v1/v3
(reference only) with its three gaps fixed: OS/Python captured, per-stage seeds recorded,
git/docker subprocess failures fatal. `DatasetManifest`/`sha256_file` copied for artifact hashing.
Accepted vs rejected records both persist with rejection_reason populated on the reject path.

## 13. Structured logging plan

CC `JsonFormatter`/`configure_logging` copied nearly unchanged; added contextual fields: run_id,
scenario_id, phase, incident_id. Every executor call, phase transition, verdict, and manifest
write emits one structured event; the log stream is itself an artifact referenced by the
RunManifest.

## 14. Security boundary plan

One parameterized AST guard (consolidating NN's three copies): walks configured packages, bans
configured import sets. Wave A policy: collectors/verifiers/evidence packages may never import
mutation-capable libs (subprocess allowed only in the runner module); fault/lab packages may not
import model/LLM libs (enforcing the ground-truth boundary structurally); nothing imports
paramiko/netmiko/ansible_runner anywhere. Known limitation carried forward honestly: AST scanning
is blind to dynamic imports — documented, mitigated by runtime allow-lists in the executor.

## 15. Test migration plan

| Tier | Content | Source basis |
|---|---|---|
| unit | ported: test_claims (＋new ANY-predicate cases), test_ledger, test_manifest, test_logging, tools tests (modified), fabric-IPAM invariants re-targeted at TopologySpec; new: parser goldens from NN test_lab_collector fixtures, exec policy tests | CC/NN suites |
| contract | new: JSON round-trip + schema-version tests for every Gate 3 contract | none (NAS) |
| integration | lab up→healthy-verify→down against real compose; collector-vs-live-FRR goldens | NN test_lab_collector 21B pattern (its e2e test itself RETAINed) |
| failure | denied command, timeout, dead container, onset-timeout auto-restore, restore-failure ledger state | new (patterns from STA error paths) |
| repeatability | full loop ×2 → identical verdicts | new (Gate 4 requirement) |
| cleanup-after-failure | kill mid-INJECTED → restore + teardown asserted | new (Gate 4 requirement) |
| security | consolidated AST guard self-tests + policy tests | NN ×3 scans |

## 16. Exact files/symbols proposed for reuse (near-direct)

`CommandRunner`/`_default_runner`, `_assert_show_command`+token list (NN); `Ledger`/`LedgerRecord`/
`Phase`/`now_record`, `Claim`/`Predicate`/`Verdict`/`Snapshot`/`verify`/`committable`,
`Budget`/`BudgetExhausted`/`ToolContext`/`Record`/`EvidenceSource`, `DatasetManifest`/
`build_manifest`/`sha256_file`, `JsonFormatter`/`configure_logging` (CC); `daemons` file (NN);
`_bgp_neighbor_mode` heal idiom (NN lab.sh).

## 17. Exact files/symbols proposed for rewrite (copy-with-modifications or reimplement)

Copy w/ mods: `_assert_known_router`, `_vtysh_json`/`_vtysh_text`, NN parsers (`_peers_from`,
`_scrape_router`, `_scrape_route_table`, `_cap_text`), `_emit` (id collision fix),
`get_interface_state`/`get_bgp_state`/`get_log_events`, `allocate` math, `wait_for_state`,
STA inject/restore vtysh sequences (FRR-targeted), `read_peer_state` (JSON-parser-backed),
`collect_bgp_summary`, EVL `bgp_underlay_established`/`loopback_reachable`/`container_running`,
NN AST scanner (consolidated), CC test_tools/test_fabric invariants.
Reimplement from spec: `FabricSpec` grammar → TopologySpec (links section, role-agnostic),
`collect_lab_snapshot`, STA preconditions, `schema_hash` mechanism, emit-manifest capture set,
Scenario/take_snapshot lifecycle → contract-shaped runner, the oracle + IncidentRecord builder.

## 18. Exact files/symbols rejected

EVL `_run` (shell=True, f-string interpolation, no timeout — line-level evidence in App. C);
EVL `host_can_ping` ≥4/15 floor (passes 73%-loss data plane; masks partial faults — owner-mandated
rejection recorded); NN `edge-2`/`branch-1` frr.conf (redundant templates); NN `pull_policy: never`
(breaks fresh hosts) and SYS_ADMIN capability (excessive) as compose patterns; NN lab.sh
sleep-free-but-poll-free convergence handling (manual waiting); STA Redis DB collectors +
`CONTAINER` constants (Wave B / retain); CC emit scripts' silent git/docker failure mode; CC 2s4l
test constants (retain as CC fixtures); dead multi-word forbidden tokens (NN allow-list).

## 19. Proposed Gate 3 package boundaries (candidates, for Gate 3 decision)

`verifiednet.schemas` (contracts, DB-free) · `verifiednet.common` (logging, hashing, errors) ·
`verifiednet.runtime` (process runner + ExecResult + transcript; policy objects) ·
`verifiednet.labs` (LabBackend contract + frr-compose backend + topology rendering) ·
`verifiednet.faults` (FaultScenario contract + bgp_remote_as_mismatch) ·
`verifiednet.collectors` (EvidenceCollector contract + frr collectors) ·
`verifiednet.verifiers` (claims + checks) · `verifiednet.incidents` (oracle + record builder +
manifests). Split rationale in §7; final boundaries are a Gate 3 decision.

## 20. Provenance actions

Every harvested symbol gets a provenance header (source repo, commit, path, symbol, modifications)
+ NOTICE entry once the approved Apache-2.0 LICENSE/NOTICE are created (Gate 3, owner-approved).
CC-sourced items additionally blocked from **public release** until closcall publishes its license
(owner reuse proceeds now per Gate 0 correction). AVH/Apache attribution not triggered in Wave A
(no AVH symbols in scope).

## 21. Risks

(1) FRR-in-SONiC vs plain FRR v8.4.1 behavioral drift for the vtysh sequences — mitigated by
integration tests against the pinned image, not assumed. (2) No aggregate deadline exists in any
source collector — worst-case snapshot ~160s; new deadline logic is untested territory.
(3) `frrouting/frr:v8.4.1` digest pinning may surface arch differences (arm64 vs amd64).
(4) Reconvergence timing (~2s with `clear bgp`) is STA-lab-measured, not FRR-compose-measured.
(5) The evidence-id collision fix changes CC semantics — ported claim tests must be re-baselined.
(6) AST guard's dynamic-import blindness. (7) Single-maintainer source repos: any upstream force-push
invalidates pinned-commit provenance (mitigate: record commit + content hashes at harvest).

## 22. Open questions

(1) Compose project naming/namespacing for parallel runs (affects repeatability test design).
(2) Whether onset verification should also assert the *reason* field (FRR exposes "Connect" vs
"Idle (Admin)") or state-set membership suffices — Gate 3 contract detail. (3) Exact ExecResult
error taxonomy enum values. (4) Whether TopologySpec v1 includes `sessions:` (my design above) or
derives sessions from links + node ASNs. (5) Runner default timeout: keep NN's 10s or make
policy-mandatory with no default.

## 23. Claims not yet proven

(a) That the two-router lab converges reliably under the pinned FRR image on both arm64/amd64 —
no run has been executed. (b) That STA's polling parameters transfer to FRR-compose timing.
(c) That NN's parsers accept plain-FRR JSON byte-for-byte (FRR-in-SONiC vs 8.4.1 field drift
possible). (d) That the consolidated AST guard catches everything the three originals did
(guard self-tests required). (e) Any performance/latency number — none measured, none claimed.
(f) Runnability of any harvested symbol inside VerifiedNet remains unproven until Gate 3 tests
execute.

## 24. Gate 3 implementation order

1. `schemas` contracts (16) + JSON round-trip/contract tests — everything depends on them.
2. `common` (logging, hashing, errors) — copied CC symbols + tests.
3. `runtime` (process runner, ExecResult, policies, transcript) + unit/failure tests.
4. `labs` TopologySpec + frr-compose backend (2-router) + integration test (up/healthy/down).
5. `collectors` FRR evidence family + goldens.
6. `verifiers` (claims + checks + polling) + ported/new tests.
7. `faults` bgp_remote_as_mismatch behind FaultScenario + ledger wiring.
8. `incidents` (oracle, record builder, manifests) — closing the Gate 4 loop.
9. Security AST guard + CI workflow (runs from first commit; gates every later step).

---

# Appendix A — NeuroNOC per-file analyses (verbatim inspection fragment)

# Gate 2 file-level harvest fragment — neuronoc-network-ops-assistant

Repo: `/tmp/repos/neuronoc-network-ops-assistant` @ commit `5f2444742afbfd557d24d1e30fedd337f565f432` (verified via `git rev-parse HEAD`). All inspection local; no network access.

---

## 1. backend/app/lab/collector.py

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | backend/app/lab/collector.py (1265 lines) |
| 4 file purpose | One-shot, read-only FRR lab collector: scrapes BGP summary, interfaces, route table, running-config from 4 Docker FRR routers via `docker exec ... vtysh -c "show ..."`, persists into Incident/IncidentEvent/IncidentEvidence tables. Hard contract in module docstring (L9-16): one-shot, read-only, per-router failure isolation. |
| 5 public symbols | `LAB_MARKER` (L37, `"[lab-collector]"`), `LAB_INCIDENT_TYPE` (L38), `LAB_FULL_SNAPSHOT_INCIDENT_TYPE` (L41), `LAB_ROUTERS`/`LAB_ROUTERS_SET` (L43-44), `LAB_LOOPBACKS` (L79-84), `EXPECTED_BGP_LOOPBACKS_FOR` (L85-92), `CommandResult`/`CommandRunner` (L145-146), `RouterScrape` (L216), `LabBgpCollectionSummary` (L266), `InterfaceObservation` (L279), `ConfigScrape` (L343), `RouteTableScrape` (L390), `LabSnapshotSummary` (L517), `collect_lab_bgp_snapshot` (L546), `collect_lab_snapshot` (L721), `MAX_ITERATIONS=100`/`MAX_INTERVAL_SECONDS=3600` (L1137-1138), `main` (L1231) |
| 6 private symbols relevant to Wave A | `_FORBIDDEN_VTYSH_TOKENS` (L50-64), `_assert_known_router` (L95), `_VTYSH_TOKEN_SEP_RE` (L109), `_assert_show_command` (L112), `_default_runner` (L149), `_container_for` (L165), `_vtysh_json` (L169), `_vtysh_text` (L193), `_scrape_router` (L223), `_safe_int` (L233), `_peers_from` (L240), `_router_id_and_as` (L256), `_normalize_interface` (L297), `_scrape_interfaces` (L321), `_scrape_running_config` (L352), `_route_entries` (L406), `_route_protocol` (L429), `_route_has_protocol` (L445), `_cap_text` (L449), `_scrape_route_table` (L460), `_interface_is_down` (L701), `_interface_has_errors` (L715), `_build_parser` (L1141), `_run_watch` (L1195) |
| 7 internal imports | `app.db.models` (Incident, IncidentEvent, IncidentEvidence), `app.db.session` (SessionLocal) — L34-35. No import of app.core.config directly; DATABASE_URL reaches it transitively via session.py. |
| 8 external dependencies | stdlib: argparse, json, re, subprocess, sys, time, dataclasses, typing, uuid, collections.abc. Third-party: pydantic (BaseModel, Field), sqlalchemy.orm.Session. |
| 9 global state | Module-level constants only (allow-lists, caps, loopback map). No mutable globals. Engine/SessionLocal are module-level in the imported session module. Tests monkeypatch `_default_runner` and `SessionLocal` at module scope. |
| 10 side effects | DB writes (Incident + events + evidence, one `db.commit()` per collection at L684/L1110); subprocess `docker exec`; stdout prints in CLI paths; `time.sleep` in `_run_watch`. No file writes, no network beyond local docker socket. |
| 11 subprocess behavior | **Injectable runner**: `CommandRunner = Callable[[list[str]], tuple[str,str,int]]` (L145-146). `_default_runner` (L149-162) calls `subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)` — **argv list, never shell=True**, so no shell injection surface. Exit-code handling: returns `(stdout, stderr, returncode)`; `FileNotFoundError` (docker CLI missing) → rc 127; `TimeoutExpired` → rc 124 with `"timeout after {n}s"` message. Callers treat rc!=0 as failure and format `"vtysh exit {rc}: {last-line-of-stderr-or-stdout}"` (L184-186). **No output cap at the runner level** — caps are applied downstream per artifact type only. |
| 12 command allow-list behavior | Two-layer: (a) `_assert_known_router` (L95-106): membership in frozenset {edge-1, edge-2, core-1, branch-1}; raise ValueError otherwise — prevents `docker exec` against arbitrary containers. (b) `_assert_show_command` (L112-139): lowercases, collapses every run of `[^a-z0-9]+` to one space (regex L109), pads with spaces; requires prefix `" show "`; then rejects if any of the 11 forbidden tokens {clear, reset, debug, "no debug", configure, "conf t", write, copy, reload, delete, enable} appears as `" {token} "` substring. Punctuation (`;`, `|`, `&`) becomes a token boundary so `show running-config|clear ip bgp` is caught. `show debugging` allowed (whole-word). Both asserted inside `_vtysh_json`/`_vtysh_text` (L179-180, L202-203) so no call path skips them. **Bypass notes**: (i) multi-token entries "no debug"/"conf t" are dead weight — the single tokens "debug"/"configure" already match, but a hyphenated single token would be split by normalization ("running-config" → "running config"), so a forbidden token containing a hyphen would never match; current list has none. (ii) The guard is convention-enforced at the two `_vtysh_*` helpers; any new code calling `runner()` directly bypasses it (nothing in-module does). (iii) A malicious injected `runner` bypasses everything by design (test seam). (iv) The allow-list rejects only the vtysh string, not the argv assembly — but router and command are the only variables, both guarded. |
| 13 timeouts | subprocess timeout=10s per vtysh call (L155). No overall collection deadline: worst case 4 routers × 4 commands × 10s = 160s for `collect_lab_snapshot`. `--watch` sleeps `interval-seconds` (1..3600) between ≤100 iterations. |
| 14 error handling | Per-scrape (router, command) failures return `(None, err)` and become dedicated `*_collection_error` events; never abort collection. JSON decode errors → `"vtysh returned non-JSON: ..."` (L188-190). `_run_watch` catches broad `Exception` per iteration, emits NDJSON error row, continues (L1216-1225). Severity escalation: low → medium (any peer not Established / iface errors / missing loopback) → high (any scrape failure / iface down) (L1057-1061). |
| 15 environment assumptions | docker CLI on PATH; containers named `neuronoc-lab-<router>`; FRR vtysh inside; FRR 8.x JSON shapes (`ipv4Unicast.peers`, with legacy top-level `peers` fallback L240-253); Postgres reachable via SessionLocal; Python ≥3.10 (`X | None` unions, `removeprefix` in tests). |
| 16 hardcoded names | Router names edge-1/edge-2/core-1/branch-1 (L43); container prefix `neuronoc-lab-` (L166); event types `lab_bgp_peer_established`, `lab_bgp_peer_not_established`, `lab_bgp_prefix_snapshot`, `lab_bgp_collection_error`, `lab_interface_status`, `lab_interface_collection_error`, `lab_route_table_snapshot`, `lab_route_missing`, `lab_route_collection_error`, `lab_config_collection_error`; evidence types `running_config_snapshot`, `route_table_snapshot`; marker `[lab-collector]`; `_origin: "lab-collector"` payload tag. |
| 17 hardcoded addresses | Loopbacks: edge-1 10.0.0.11/32, edge-2 10.0.0.12/32, core-1 10.0.0.21/32, branch-1 10.0.0.31/32 (L79-84). No link IPs in this file (those live in compose/frr.conf/tests). |
| 18 output formats | **Parsers**: `_peers_from` → `dict[peer_ip → peer_info dict]`; `_router_id_and_as` → `(router_id: str\|None, as: int\|None)`; `_scrape_interfaces` → `list[InterfaceObservation]` (router, ifname, admin/oper/line_protocol strings defaulting "unknown", 4 optional int counters); `_scrape_running_config` → `ConfigScrape` (text capped 64 KiB byte-sliced with `errors="replace"` decode, truncated flag, raw_byte_count); `_scrape_route_table` → `RouteTableScrape` (parsed dict, pretty-printed sorted JSON `content` capped 64 KiB via `_cap_text`, bgp/connected/total counts, expected/present/missing loopback lists). Public returns: `LabBgpCollectionSummary` / `LabSnapshotSummary` pydantic models; CLI prints `model_dump(mode="json")` — indent=2 for one-shot, NDJSON for --watch. Bounded-output discipline: `_RUNNING_CONFIG_MAX_BYTES = _ROUTE_TABLE_MAX_BYTES = 64*1024` (L69, L75); BGP/interface payloads not size-capped (bounded in practice by lab size). |
| 19 existing tests | backend/tests/test_lab_collector.py — 40+ tests, fully mocked runner: happy path, non-established, unreachable router, malformed JSON, guard functions, truncation, route-missing, severity matrix, API 201s, CLI mutex/bounds, watch-loop semantics, end-to-end Phase 5→6→7 chain. |
| 20 missing tests | `_default_runner` itself untested (timeout→124, FileNotFoundError→127 branches); no test for total-collection wall time; no test for non-dict `parsed` in `_scrape_interfaces` empty-list branch via API; no unicode-boundary truncation test (only ASCII); `routers=` kwarg validation for `collect_lab_bgp_snapshot` (only `collect_lab_snapshot` pre-validates routers at L754 — the BGP-only path relies on the per-call assert inside `_vtysh_json`); no concurrency/reentrancy test. |
| 21 reuse classification | REFACTOR_AND_REUSE |
| 22 exact reusable symbols | `CommandRunner`/`CommandResult`/`_default_runner`, `_assert_show_command` + `_FORBIDDEN_VTYSH_TOKENS` + `_VTYSH_TOKEN_SEP_RE`, `_assert_known_router` (parameterized), `_vtysh_json`, `_vtysh_text`, `_cap_text`, `_safe_int`, `_peers_from`, `_router_id_and_as`, `InterfaceObservation`, `_normalize_interface`, `_scrape_interfaces`, `ConfigScrape`, `_scrape_running_config`, `RouteTableScrape`, `_route_entries`, `_route_protocol`, `_scrape_route_table`, `_interface_is_down`, `_interface_has_errors` |
| 23 symbols to rewrite | `collect_lab_bgp_snapshot`, `collect_lab_snapshot` (fused DB persistence + NeuroNOC Incident schema — split scrape from persist for VerifiedNet); `LAB_ROUTERS`/`LAB_LOOPBACKS`/`EXPECTED_BGP_LOOPBACKS_FOR` (re-derive for 2-router topology, ideally from injected topology config not module constants); `_container_for` (prefix must become configurable); CLI `main`/`_build_parser`/`_run_watch` (reimplement against VerifiedNet CLI conventions) |
| 24 symbols to reject | Event/evidence type string constants tied to NeuroNOC phases (`lab_*` names may be renamed); `LAB_MARKER` NeuroNOC branding; Phase-numbered docstrings |
| 25 required adapter/interface | Keep `CommandRunner` as the seam. Introduce a `LabTopology` value object (router names, container prefix, loopbacks, expected-prefix map) injected into scrapers; a `SnapshotSink` protocol replacing direct Incident/IncidentEvent/IncidentEvidence writes. |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/lab/collector.py` (scrape + guards) and `verifiednet/lab/topology.py` (topology constants); persistence adapter in `verifiednet/store/`. |
| 27 provenance action | Record file+commit in PROVENANCE; per-symbol attribution for copied guard/scraper functions; note MIT/LICENSE terms of source repo. |
| 28 risks | No aggregate timeout (160s worst case); `runner` injection point bypasses all guards if exposed upstream; FRR JSON shape drift between versions (normalizers cover 8.x + one legacy shape only); `routers_seen` reconciliation logic (L1049-1054) is subtle — references `observations`/`route_scrape`/`cfg` from the same loop iteration and counts a router "seen" if ANY non-BGP scrape succeeded; DB commit granularity (single commit — partial-failure leaves nothing, which is fine, but a commit error loses the whole snapshot); `_peers_from` silently returns {} on unknown shape (healthy-looking zero-peer snapshot on schema drift → severity low false negative). |

---

## 2. backend/tests/test_lab_collector.py

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | backend/tests/test_lab_collector.py (1323 lines) |
| 4 file purpose | Full test suite for the lab collector; every test mocks the docker/vtysh runner (no live lab). Covers BGP-only collector, guards, umbrella snapshot, route tables, API, CLI, watch loop, and a Phase 21B end-to-end chain test. |
| 5 public symbols | Test functions (~45) plus reusable fixture-builders: `_summary_for` (L28, canonical FRR `show ip bgp summary json` shape w/ per-router routerId+AS map), `_peer` (L49), `_router_from_cmd` (L58), `_make_runner` (L64), `_all_healthy_runner` (L84), `_interface_json` (L632), `_route_json` (L650), `_healthy_routes` (L661), `_snapshot_runner` (L665, command-dispatching fake keyed on `cmd[5]`), `_all_healthy_snapshot_runner` (L711) |
| 6 private symbols relevant to Wave A | `_stub_session`/`_stub_sleep`/`_stub_collect_returning` (L338-375) — monkeypatch harness for CLI watch tests; `_ok_summary`/`_summary_with_errors` (L378-402) |
| 7 internal imports | app.db.models (Incident, IncidentEvent, IncidentEvidence), app.lab.collector (module + many symbols), plus in-test imports of app.agents.runner, app.llm.ollama, app.rca.explainer, app.remediation.planner, app.anomaly.engine (L1183-1187, L1303) |
| 8 external dependencies | pytest, fastapi.testclient, sqlalchemy; fixtures `db_session`/`client` from conftest |
| 9 global state | None; monkeypatch per-test. Note: `_default_runner` and `SessionLocal` monkeypatched at collector-module scope. |
| 10 side effects | DB writes through session fixture (savepoint-rolled); no subprocess, no docker. |
| 11 subprocess behavior | None — fakes conform to `CommandRunner` signature `(cmd: list[str]) -> (str, str, int)`; extracts router from `cmd[2]` (`neuronoc-lab-` prefix strip) and vtysh command from `cmd[5]`, pinning the exact argv layout `["docker","exec",<ct>,"vtysh","-c",<cmd>]`. |
| 12 command allow-list behavior | Directly tests both guards: valid routers accepted (L555-557); `rogue-router`, `../malicious`, `edge-1; rm -rf`, `""` rejected (L560-563); show-prefix enforcement incl. case-insensitive `SHOW IP ROUTE` (L566-573); forbidden-token rejection incl. punctuation-adjacent forms `show running-config|clear ip bgp *`, `show foo;reload`, `show foo&&clear ...` (L595-610); allow-pin that `show debugging` stays legal (L613-626). |
| 13 timeouts | None needed; sleep stubbed; watch pin: N iterations, N-1 sleeps of exactly interval (L437-453). |
| 14 error handling | Exercises rc!=0, non-JSON stdout, per-scrape failure isolation, watch-loop exception resilience (RuntimeError mid-loop → NDJSON error row, loop continues, L485-513). |
| 15 environment assumptions | conftest provides transactional `db_session` + FastAPI `client`; no docker/Ollama needed (Ollama stubbed via `OllamaUnavailableError` in 21B test L1189-1194). |
| 16 hardcoded names | Router quartet; container prefix `neuronoc-lab-`; event/evidence type strings; API paths `/api/lab/collect/bgp`, `/api/lab/collect/snapshot`. |
| 17 hardcoded addresses | Test AS/routerId map: edge-1 65011/10.0.0.11, edge-2 65012/10.0.0.12, core-1 65000/10.0.0.21, branch-1 65031/10.0.0.31 (L31-43); peer IPs 172.30.1.1/.2, 172.30.2.1/.2, 172.30.3.1/.2 mirroring the lab /29 links; topology-pin test (L1067-1077) locks LAB_LOOPBACKS↔EXPECTED_BGP_LOOPBACKS_FOR consistency. |
| 18 output formats | Asserts exact summary counts (e.g. 4 routers, 6 peers, 10 events = 6 peer + 4 snapshot; evidence_created == 8 = 4 config + 4 route), payload key sets, NDJSON watch output, 64 KiB truncation with byte_count preserved (L911-944). |
| 19 existing tests | This IS the test file. |
| 20 missing tests | Same gaps as collector (no `_default_runner` test); no test that `collect_lab_bgp_snapshot(routers=["bogus"])` raises (only snapshot path pre-validates); fake runner never simulates slow/hung command. |
| 21 reuse classification | REFACTOR_AND_REUSE (fixture builders + guard tests port with renamed topology; end-to-end 21B test is RETAIN_ONLY_IN_ORIGINAL_REPO — depends on NeuroNOC Phases 4-7 stack) |
| 22 exact reusable symbols | `_summary_for`, `_peer`, `_router_from_cmd`, `_make_runner`, `_snapshot_runner`, `_interface_json`, `_route_json`, guard test functions (test_assert_known_router_*, test_assert_show_command_* incl. punctuation-adjacent cases), truncation test, severity-matrix tests, watch-loop tests |
| 23 symbols to rewrite | All topology literals (4-router → 2-router VerifiedNet lab); API-path tests to VerifiedNet routes; `_all_healthy_runner` peer maps |
| 24 symbols to reject | `test_phase21b_lab_snapshot_feeds_full_workflow_end_to_end` (L1150-1323) and the anomaly/RCA/remediation imports it drags in |
| 25 required adapter/interface | Test fixtures should consume the same injected `LabTopology` the production code uses so the 2-router derivation changes exactly one fixture. |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/tests/test_lab_collector.py` (+ shared `tests/fakes/vtysh_runner.py` for the dispatching fake) |
| 27 provenance action | Attribute fixture builders and guard-test vectors to source file+commit; adversarial vectors (`show foo&&clear...`) are load-bearing security tests — copy verbatim. |
| 28 risks | Tests pin argv indexes (`cmd[2]`, `cmd[5]`) — brittle if runner argv changes; count assertions (events_created==10, evidence_created==8) will all shift with a 2-router lab — recompute, don't pattern-copy. |

---

## 3. infra/lab/docker-compose.lab.yml

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | infra/lab/docker-compose.lab.yml (146 lines) |
| 4 file purpose | Standalone Compose stack: 4 FRR routers in an eBGP hub-and-spoke (core-1 hub), independent of the main NeuroNOC stack (own networks, no shared volumes). |
| 5 public symbols | Services edge-1, edge-2, core-1, branch-1; networks neuronoc_lab_edge1_core1, neuronoc_lab_edge2_core1, neuronoc_lab_branch1_core1. |
| 6 private symbols relevant to Wave A | n/a (YAML) |
| 7 internal imports | Mounts `./configs/<router>/daemons` and `frr.conf` read-only into `/etc/frr/` (e.g. L26-28). |
| 8 external dependencies | **Image: `frrouting/frr:v8.4.1` with `pull_policy: never`** (L15-16 etc.) — image must be pre-pulled/local; Docker Compose v2 syntax. |
| 9 global state | Named containers `neuronoc-lab-<router>`; fixed hostnames; `restart: unless-stopped`. |
| 10 side effects | Creates 3 bridged networks with fixed subnets; grants `cap_add: NET_ADMIN, SYS_ADMIN, NET_RAW`; sysctl `net.ipv4.ip_forward=1` per container. |
| 11 subprocess behavior | Healthchecks run `vtysh -c 'show ip bgp summary json'` in-container, grep-count `"state":"Established"` (L33-39): edges/branch require ≥1, core-1 requires ≥3 (L91-94). interval 5s, timeout 5s, retries 30, start_period 25s → up to ~175s to healthy. |
| 12 command allow-list behavior | n/a (healthcheck is read-only show). |
| 13 timeouts | Healthcheck timing above; no other. |
| 14 error handling | `restart: unless-stopped` is the only recovery mechanism. |
| 15 environment assumptions | Local image present (pull_policy: never breaks on fresh hosts unless pre-pulled); Docker with cap_add support; subnets 172.30.{1,2,3}.0/29 free on host. |
| 16 hardcoded names | Container names `neuronoc-lab-*` (must match collector's `_container_for` and lab.sh `_container`); network names; hostnames matching Phase 3 simulator device specs (comment L9-11). |
| 17 hardcoded addresses | Per-link /29s with gateway parked at .6 so routers get .1/.2 (comment L7-8): edge1↔core1 172.30.1.0/29 (edge-1=172.30.1.1, core-1=172.30.1.2, gw .6); edge2↔core1 172.30.2.0/29 (edge-2=172.30.2.1, core-1=172.30.2.2, gw .6); branch1↔core1 172.30.3.0/29 (branch-1=172.30.3.1, core-1=172.30.3.2, gw .6). core-1 is triple-homed (L83-89). |
| 18 output formats | n/a. |
| 19 existing tests | infra/lab/scripts/test_lab.sh exists (not in Gate 2 scope); healthchecks act as convergence assertions. |
| 20 missing tests | No CI validation of compose file; no image-availability check message for pull_policy:never failure mode. |
| 21 reuse classification | DESIGN_REFERENCE_ONLY (VerifiedNet derives a minimal 2-router lab: keep the idioms — pinned FRR tag, ro config mounts, JSON-grep healthcheck, /29-with-parked-gateway, per-link networks — drop 2 routers) |
| 22 exact reusable symbols | Healthcheck CMD-SHELL pattern; cap_add/sysctls block; ipam /29+gateway .6 idiom; volume-mount pattern; `pull_policy: never` decision (revisit) |
| 23 symbols to rewrite | Everything topology-specific: 2 services (e.g. r1 AS-A, r2 AS-B), 1 network, container prefix `verifiednet-lab-`, healthcheck thresholds ≥1 each. |
| 24 symbols to reject | edge-2 and branch-1 services; 2 of 3 networks; NeuroNOC naming. |
| 25 required adapter/interface | None; but the container-name prefix must be a single shared constant with the collector and lab script. |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/infra/lab/docker-compose.lab.yml` |
| 27 provenance action | Note derivation (topology reduced 4→2) in PROVENANCE; keep FRR image tag citation to source. |
| 28 risks | `pull_policy: never` silently fails without the image cached; FRR v8.4.1 is old (2022) — parsers were written against its JSON, upgrading the tag risks shape drift (collector normalizers only cover 8.x + one legacy shape); SYS_ADMIN is a broad capability (likely only NET_ADMIN needed for FRR — verify before copying). |

---

## 4. infra/lab/scripts/lab.sh

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | infra/lab/scripts/lab.sh (253 lines, bash, `set -euo pipefail`) |
| 4 file purpose | Compose wrapper + Phase 21D fault-injection demo helper. Lab-only by construction: every mutation goes through `docker exec neuronoc-lab-*`. |
| 5 public symbols | Verbs: `up` (L203-205: `docker compose -f $COMPOSE_FILE up -d "$@"` — detached, **no wait/poll/sleep; relies on compose healthchecks only**), `down` (L206-208: `compose down`, containers+networks, no volumes), `ps` (compose ps), `logs [router]` (`--tail=200 -f` for one validated router, `--tail=100 -f` for all, L212-219), `cli <router>` (interactive `docker exec -it ... vtysh`, L220-224), `bgp <router\|all>` (non-JSON `show ip bgp summary`; `all` loops routers with `\|\| true` so one dead router doesn't stop the report, L225-237), `inject <fault> ...`, `heal <fault> ...` (L238-243), help via ""/-h/--help/help; unknown → usage + exit 2. |
| 6 private symbols relevant to Wave A | `_canonical_bgp_peer` (L28-36: case lookup edge-1→172.30.1.2, edge-2→172.30.2.2, core-1→172.30.1.1, branch-1→172.30.3.2 — chosen so faults are partial, collector still reaches all nodes); `_local_as` (L38-45: 65011/65012/65000/65031); `FAULT_TYPES=(bgp-down iface-down)` (L49); `_valid_router` (L76-84); `_valid_fault` (L86-94); `_container` (L96-98: `neuronoc-lab-$1`); `_bgp_neighbor_mode` (L107-134); `_iface_mode` (L136-155); `_inject`/`_heal` (L157-197). |
| 7 internal imports | Resolves `COMPOSE_FILE` relative to its own dir (L14-16). |
| 8 external dependencies | bash ≥3.2 (deliberately no `declare -A` for stock macOS, comment L26-27), docker + compose v2, awk. |
| 9 global state | None persistent; mutates lab container state (BGP shutdown / ip link). |
| 10 side effects | `inject bgp-down <router>`: `docker exec <ct> vtysh -c "configure terminal" -c "router bgp <AS>" -c "neighbor <peer> shutdown" -c "end"` (L129-133). `heal bgp-down`: same with `no neighbor <peer> shutdown` — the `no` wraps the whole command; comment documents FRR syntax trap (L100-106). Idempotent (FRR treats repeat as no-op). `inject/heal iface-down <router> <iface>`: pre-checks `ip link show <iface>` and prints available interfaces on typo (L145-151), then `ip link set <iface> down|up` (L154). |
| 11 subprocess behavior | All docker exec argv-quoted; `>/dev/null` on config exec output. |
| 12 command allow-list behavior | Router names validated against ROUTERS array (exit 1 + list on mismatch); fault types against FAULT_TYPES (exit 2). NOTE: this script intentionally DOES issue `configure terminal` — it is the fault injector, complementary to the read-only collector; the guard boundary is the fixed peer/AS lookup tables (no user-supplied vtysh text). Interface name IS user-supplied but only reaches `ip link show/set` as a single argv token. |
| 13 timeouts | None. No sleeps and **no polling anywhere**; help text warns "BGP convergence after `heal` takes ~30s (hold timer)" (L73) — verification is manual via `lab.sh bgp all`. |
| 14 error handling | `set -euo pipefail`; readable stderr messages; missing-args exits 2; unknown router/fault fails fast before any docker call; iface pre-check avoids half-touched state. |
| 15 environment assumptions | docker on PATH; lab already up for inject/heal; container names match compose. |
| 16 hardcoded names | ROUTERS quartet; container prefix; fault names bgp-down/iface-down (iface-errors deferred per README, L69). |
| 17 hardcoded addresses | Canonical peer IPs (per-router single peer, above); AS numbers 65000/65011/65012/65031. |
| 18 output formats | Human-readable `[lab] <router> AS<asn>: <cmd>` echo lines; vtysh table output for `bgp`. |
| 19 existing tests | None automated for the script itself (test_lab.sh exercises the lab, not the verbs); collector tests don't touch it. |
| 20 missing tests | No shellcheck/bats coverage; no test that heal syntax (`no neighbor X shutdown`) is correct — it's documentation-pinned only; no verification loop after inject/heal. |
| 21 reuse classification | REFACTOR_AND_REUSE |
| 22 exact reusable symbols | Verb dispatch skeleton; `_bgp_neighbor_mode` (incl. the `no neighbor X shutdown` heal idiom — a real FRR gotcha); `_iface_mode` with its pre-check; `_valid_router`/`_valid_fault` pattern; bash-3.2-compatible case-lookup idiom. |
| 23 symbols to rewrite | ROUTERS, `_canonical_bgp_peer`, `_local_as` tables for the 2-router topology; container prefix; usage text. |
| 24 symbols to reject | None structurally; drop unused router entries. |
| 25 required adapter/interface | Single source of truth for router→(container, AS, canonical-peer) shared with compose + collector (could be generated from one topology file). |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/infra/lab/scripts/lab.sh` |
| 27 provenance action | Attribute the heal-syntax comment block (L100-106) — it encodes hard-won FRR behavior. |
| 28 risks | No convergence polling after up/heal — callers may scrape too early (collector will just report not-Established, which is by design but can confuse demos); `logs` with no router follows forever (`-f`); inject verbs are mutation-capable and share the container namespace with the read-only collector — VerifiedNet must keep them in the script (human-invoked), never importable from Python. |

---

## 5a. infra/lab/configs/core-1/frr.conf

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | infra/lab/configs/core-1/frr.conf (28 lines) |
| 4 file purpose | FRR config for hub router core-1: AS 65000, three eBGP spokes. |
| 5 public symbols | Config stanzas (see 18). |
| 6 private symbols relevant to Wave A | n/a |
| 7 internal imports | none |
| 8 external dependencies | FRR v8.4.1 syntax |
| 9 global state | n/a |
| 10 side effects | n/a |
| 11 subprocess behavior | n/a |
| 12 command allow-list behavior | n/a |
| 13 timeouts | None configured — default FRR BGP timers (keepalive 60/hold 180; the ~30s convergence note in lab.sh reflects observed behavior, not a configured timer). |
| 14 error handling | `no bgp ebgp-requires-policy` (L12) disables RFC 8212 policy requirement — without it FRR 8.x would refuse to advertise/accept eBGP routes with no route-map. Essential idiom. |
| 15 environment assumptions | Interfaces attached by compose at fixed IPs; loopback `lo` usable for /32. |
| 16 hardcoded names | hostname core-1; neighbor descriptions edge-1/edge-2/branch-1. |
| 17 hardcoded addresses | lo 10.0.0.21/32; router-id 10.0.0.21; neighbors 172.30.1.1 (remote-as 65011), 172.30.2.1 (65012), 172.30.3.1 (65031). |
| 18 output formats | Exact config idiom (capture verbatim for VerifiedNet derivation): header `frr defaults traditional` / `hostname core-1` / `log stdout` / `service integrated-vtysh-config`; `interface lo` + ` ip address 10.0.0.21/32`; `router bgp 65000` block containing ` bgp router-id 10.0.0.21`, ` no bgp default ipv4-unicast`, ` no bgp ebgp-requires-policy`, one ` neighbor <ip> remote-as <asn>` + ` neighbor <ip> description <name>` pair per peer; nested ` address-family ipv4 unicast` with ` network 10.0.0.21/32` and ` neighbor <ip> activate` per peer, closed by ` exit-address-family`; trailing `line vty`. Because `no bgp default ipv4-unicast` is set, every neighbor MUST be explicitly activated in the address-family or no routes flow. |
| 19 existing tests | Compose healthcheck (≥3 Established) + collector topology pins (LAB_LOOPBACKS). |
| 20 missing tests | No config-lint step; no assertion that frr.conf loopbacks match collector's LAB_LOOPBACKS (drift risk). |
| 21 reuse classification | DESIGN_REFERENCE_ONLY (template for VerifiedNet's r1 config with 1 neighbor instead of 3) |
| 22 exact reusable symbols | The full stanza skeleton incl. `no bgp ebgp-requires-policy` + `no bgp default ipv4-unicast` + explicit `activate` pattern; loopback-as-router-id + `network <loopback>` advertisement idiom. |
| 23 symbols to rewrite | ASN, router-id, neighbor lines, hostname. |
| 24 symbols to reject | Two of three neighbor blocks. |
| 25 required adapter/interface | Generate from the shared topology definition if feasible. |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/infra/lab/configs/r1/frr.conf` |
| 27 provenance action | Note idiom source; trivial-config content otherwise. |
| 28 risks | Omitting `neighbor activate` or `no bgp ebgp-requires-policy` in the derived config yields sessions that establish but exchange zero prefixes — collector would report healthy peers with missing loopbacks (severity medium, confusing). |

## 5b. infra/lab/configs/edge-1/frr.conf

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | infra/lab/configs/edge-1/frr.conf (22 lines) |
| 4 file purpose | Spoke router edge-1: AS 65011, single eBGP session to core-1. |
| 5-14 | Same structure as core-1 (see 5a rows 5-14); single-neighbor variant. |
| 15 environment assumptions | eth interface at 172.30.1.1 provided by compose. |
| 16 hardcoded names | hostname edge-1; neighbor description core-1. |
| 17 hardcoded addresses | lo 10.0.0.11/32; router-id 10.0.0.11; `router bgp 65011`; `neighbor 172.30.1.2 remote-as 65000`; address-family: `network 10.0.0.11/32`, `neighbor 172.30.1.2 activate`. |
| 18 output formats | Identical idiom to 5a with exactly one neighbor — this two-line pattern (`neighbor <ip> remote-as <as>` + address-family `activate`) is the minimal eBGP session definition VerifiedNet needs per side. |
| 19 existing tests | Healthcheck ≥1 Established. |
| 20 missing tests | As 5a. |
| 21 reuse classification | DESIGN_REFERENCE_ONLY — this file is the closest template for BOTH routers of a minimal 2-router VerifiedNet lab (each side is a single-neighbor config exactly like this). |
| 22-27 | As 5a, single-neighbor variant; destination `verifiednet/infra/lab/configs/r{1,2}/frr.conf`. |
| 28 risks | As 5a. |

## 5c. infra/lab/configs/edge-2/frr.conf

| point | finding |
|---|---|
| 1-3 | Same repo/commit; infra/lab/configs/edge-2/frr.conf (22 lines). |
| 4 file purpose | Spoke edge-2: AS 65012, eBGP to core-1. |
| 5-16 | Structurally identical to edge-1 (5b); hostname edge-2. |
| 17 hardcoded addresses | lo 10.0.0.12/32; router-id 10.0.0.12; `router bgp 65012`; `neighbor 172.30.2.2 remote-as 65000`; network 10.0.0.12/32; activate 172.30.2.2. |
| 18-20 | As 5b. |
| 21 reuse classification | REJECT (redundant with edge-1 as a template; not needed in 2-router lab) |
| 22-27 | n/a beyond confirming the idiom repeats identically. |
| 28 risks | none beyond 5a. |

## 5d. infra/lab/configs/branch-1/frr.conf

| point | finding |
|---|---|
| 1-3 | Same repo/commit; infra/lab/configs/branch-1/frr.conf (22 lines). |
| 4 file purpose | Spoke branch-1: AS 65031, eBGP to core-1. |
| 5-16 | Structurally identical to edge-1; hostname branch-1. |
| 17 hardcoded addresses | lo 10.0.0.31/32; router-id 10.0.0.31; `router bgp 65031`; `neighbor 172.30.3.2 remote-as 65000`; network 10.0.0.31/32; activate 172.30.3.2. |
| 18-20 | As 5b. |
| 21 reuse classification | REJECT (redundant template) |
| 22-27 | n/a. |
| 28 risks | none beyond 5a. |

## 5e. infra/lab/configs/{core-1,edge-1,edge-2,branch-1}/daemons (4 byte-identical files)

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | infra/lab/configs/<router>/daemons — all four files identical (18 lines each), verified by direct read. |
| 4 file purpose | FRR daemon enable-list consumed by the container entrypoint. |
| 5 public symbols | Exact contents: `bgpd=yes`, then `ospfd=no ospf6d=no ripd=no ripngd=no isisd=no pimd=no ldpd=no nhrpd=no eigrpd=no babeld=no sharpd=no pbrd=no bfdd=no fabricd=no vrrpd=no`, then `zebra=yes staticd=yes`. Only bgpd, zebra, staticd run. |
| 6-14 | n/a (flat key=value file; no timeouts/error handling). |
| 15 environment assumptions | FRR docker entrypoint reads /etc/frr/daemons; format matches v8.4.1's expected keys. |
| 16 hardcoded names | Daemon names as per FRR 8.4. |
| 17 hardcoded addresses | none. |
| 18 output formats | key=value, one per line. |
| 19 existing tests | Implicit: if bgpd didn't start, healthchecks fail. |
| 20 missing tests | none needed. |
| 21 reuse classification | DIRECT_REUSE (copy verbatim per VerifiedNet router) |
| 22 exact reusable symbols | Entire file. |
| 23 symbols to rewrite | none. |
| 24 symbols to reject | none. |
| 25 required adapter/interface | none. |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/infra/lab/configs/r{1,2}/daemons` |
| 27 provenance action | Trivial content; note verbatim copy. |
| 28 risks | Newer FRR images may expect additional keys (e.g. pathd) — harmless if absent but verify against the chosen image tag. |

---

## 6a. backend/app/db/session.py (imported by collector: `SessionLocal`)

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | backend/app/db/session.py (24 lines, read fully) |
| 4 file purpose | SQLAlchemy engine + session factory + FastAPI dependency. |
| 5 public symbols | `engine` (L8: `create_engine(settings.DATABASE_URL, future=True, pool_pre_ping=True)`), `SessionLocal` (L10-16: sessionmaker autoflush=False, autocommit=False, expire_on_commit=False), `get_db` generator (L19-24). |
| 6 private symbols relevant to Wave A | none. |
| 7 internal imports | `app.core.config.settings` (L6). |
| 8 external dependencies | sqlalchemy. |
| 9 global state | **Module-import side effect: engine created at import time** using settings — importing collector.py transitively constructs a DB engine. |
| 10 side effects | Engine creation (lazy connect, but pool_pre_ping pings on checkout). |
| 11-13 | No subprocess; no explicit timeouts (relies on driver defaults). |
| 14 error handling | `get_db` closes session in finally. |
| 15 environment assumptions | Postgres reachable at DATABASE_URL. |
| 16 hardcoded names | none here (URL in config). |
| 17 hardcoded addresses | none here. |
| 18 output formats | n/a. |
| 19 existing tests | conftest overrides SessionLocal in tests. |
| 20 missing tests | none material. |
| 21 reuse classification | DESIGN_REFERENCE_ONLY (standard boilerplate; VerifiedNet has/will have its own session module) |
| 22-25 | Reusable idiom: expire_on_commit=False + pool_pre_ping. Adapter: collector should receive `Session`/session-factory by injection (it already takes `db: Session` for the core functions; only CLI paths touch SessionLocal). |
| 26 proposed VerifiedNet destination (candidate) | n/a (use VerifiedNet's own store layer). |
| 27 provenance action | none needed beyond file-level note. |
| 28 risks | Import-time engine creation means collector import fails/hangs environments without config — VerifiedNet should defer engine construction. |

## 6b. backend/app/core/config.py (transitively imported; parts collector uses)

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | backend/app/core/config.py (27 lines, read fully) |
| 4 file purpose | pydantic-settings `Settings` with .env loading from PROJECT_ROOT (L5: `parents[3]`), `extra="ignore"`. |
| 5 public symbols | `Settings`, `settings` singleton (L27), `PROJECT_ROOT`. Collector-relevant field: `DATABASE_URL` only (default `postgresql+psycopg://neuronoc:neuronoc_dev_password@localhost:5433/neuronoc`, L18-20). Other fields (APP_NAME, API_HOST 127.0.0.1, API_PORT 8000, OLLAMA_BASE_URL http://localhost:11434, OLLAMA_MODEL qwen2.5:7b-instruct, RAG_*) unused by collector. |
| 6-14 | n/a; instantiation at import (L27) is the only side effect; no timeouts. |
| 15 environment assumptions | Optional .env at repo root; env vars override defaults. |
| 16 hardcoded names | Default DB user/password/db name `neuronoc`. |
| 17 hardcoded addresses | localhost:5433 (DB), localhost:11434 (Ollama), 127.0.0.1:8000 (API). |
| 18 output formats | n/a. |
| 19 existing tests | Indirect via app tests. |
| 20 missing tests | No test for env-file precedence. |
| 21 reuse classification | RETAIN_ONLY_IN_ORIGINAL_REPO (VerifiedNet has its own settings; note the `parents[3]` root-resolution + extra="ignore" idiom only) |
| 22-26 | Nothing to copy; the collector's only transitive need is a DATABASE_URL-equivalent in VerifiedNet's settings. |
| 27 provenance action | none. |
| 28 risks | Dev password committed in defaults (acceptable for local dev; do not replicate pattern in VerifiedNet without flagging). |

---

## 7a. backend/tests/test_validation.py

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | backend/tests/test_validation.py (332 lines) |
| 4 file purpose | Phase 16A validation-preview tests + AST safety scan ensuring the validation package never imports remote-execution libraries. |
| 5 public symbols | Tests for `extract_fenced_json`, `build_validation_preview` happy/error paths (404/400 for missing rec, non-plan rec, missing fenced JSON, schema-invalid plan); `_FORBIDDEN_EXECUTION_LIBS` (L278-289); `_root_module` (L292); `_scan_files_for_execution_imports` (L296-313); `test_validation_package_blocks_execution_library_imports` (L316-332). |
| 6 private symbols relevant to Wave A | `_stub_rca` autouse fixture (L40-48); `_seed_and_persist` (L50). |
| 7 internal imports | app.db.models.Recommendation, app.remediation.planner, app.simulator.seed.apply_scenario, app.validation.preview. |
| 8 external dependencies | pytest, fastapi.testclient, sqlalchemy; stdlib ast/json/pathlib. |
| 9 global state | none. |
| 10 side effects | DB via fixtures; the AST scan reads source files under `app/`. |
| 11 subprocess behavior | none (that's the point: it bans `subprocess`). |
| 12 command allow-list behavior (AST scan mechanics) | **Walk**: `app_root = Path(__file__).resolve().parents[1] / "app"`; files = `(app_root/"validation").rglob("*.py")` + explicit `app/api/validation.py` (L323-325); asserts non-empty file list so a moved package can't silently skip the scan (L326). **Ban**: parses each file with `ast.parse`, walks all nodes; flags `ast.Import` where `alias.name.split(".",1)[0]` ∈ banned set, and `ast.ImportFrom` where `node.module` root ∈ set (relative imports with `node.module=None` skipped). Only real import nodes count — strings/docstrings/comments ignored by design (L321-322). **Banned set (exact 8)**: subprocess, ansible_runner, netmiko, napalm, paramiko, pexpect, fabric, scrapli. **Protected packages**: `app/validation/**` + `app/api/validation.py`. |
| 13 timeouts | n/a. |
| 14 error handling | Failure message lists `file:line: import X` offenders. |
| 15 environment assumptions | Test file located one dir below backend/ (parents[1] layout coupling). |
| 16 hardcoded names | Package paths; REMEDIATION_PLAN_TYPE. |
| 17 hardcoded addresses | none. |
| 18 output formats | offender strings `name.py:lineno: import x` / `from x import ...`. |
| 19 existing tests | Is a test file; the scanner itself has no self-test (e.g. no fixture proving it catches a planted offender). |
| 20 missing tests | Scanner blind spots untested: `importlib.import_module("subprocess")`, `__import__`, aliased indirection, `exec()` — the AST scan only sees literal import statements. No test for relative-import edge (`from . import subprocess`-style shadowing). |
| 21 reuse classification | REFACTOR_AND_REUSE (extract scanner into one shared parameterized helper; the tri-file copy-paste is itself a smell) |
| 22 exact reusable symbols | `_root_module`, `_scan_files_for_execution_imports`, `_FORBIDDEN_EXECUTION_LIBS`, the "assert files non-empty" guard, the package-walk pattern. |
| 23 symbols to rewrite | Parameterize as `scan(packages: list[Path], extra_files: list[Path], banned: frozenset[str])` in a shared test util; VerifiedNet instantiates per protected package (e.g. its validation/remediation/telemetry equivalents) with per-package banned sets. |
| 24 symbols to reject | The preview-endpoint tests (NeuroNOC-schema-specific) unless VerifiedNet ports the validation preview feature. |
| 25 required adapter/interface | `tests/util/import_guard.py` with `find_forbidden_imports(files, banned) -> list[str]`; per-package test functions declare (package, banned-set) pairs. |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/tests/util/import_guard.py` + thin per-package tests. |
| 27 provenance action | Attribute banned-lib sets and scan technique; the exact sets are policy artifacts worth citing. |
| 28 risks | Path-layout coupling (`parents[1]`); scanner cannot catch dynamic imports (documented limitation, keep the caveat in docstrings); if VerifiedNet renames packages the "assert files" guard is the only thing preventing a silent no-op scan — keep it. |

## 7b. backend/tests/test_remediation.py

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | backend/tests/test_remediation.py (661 lines) |
| 4 file purpose | Phase 7 remediation planner tests: plan-only contract (requires_approval=True, rollback steps, DRAFT-ONLY ansible with `when: false`), template selection, persistence, approval workflow with Phase 23 auth/RBAC, CLI, plus the original AST execution-import scan. |
| 5 public symbols | Per-template tests (L51-133: BGP plan has rollback + approval tag; ACL mentions diff+approval; interface plan's FIRST command must be observational — forbidden first tokens "conf t"/"configure terminal"/"shutdown"/"no shutdown" L91; route plan pre/post checks include `show ip route`; unknown type → generic_investigation with no config commands); `pick_template` fallback (L164-172); API tests; approval tests (pending default L228-236; approve/reject record identity from bearer token not body L280-329; idempotent re-approve overwrites L332-360; 401 unauth L404-434; 403 non-admin L437-478; 422 legacy identity fields via extra='forbid' L481-514); CLI persist/no-persist (L543-596); AST scan block (L604-661). |
| 6 private symbols relevant to Wave A | `_stub_rca` autouse (L31-41); `_seed_operator_with_password`/`_login_as`/`_admin_headers` (L239-277). |
| 7 internal imports | app.anomaly.engine, app.db.models, app.remediation.{planner,templates}, app.simulator.seed, app.auth.hashing (in-test). |
| 8 external dependencies | pytest, fastapi.testclient, sqlalchemy; ast/json/pathlib. |
| 9 global state | none. |
| 10 side effects | DB via fixtures; source-file reads for AST scan. |
| 11 subprocess behavior | none. |
| 12 command allow-list behavior (AST scan mechanics) | Identical scanner to 7a (L618-641), same **banned set of 8**: subprocess, ansible_runner, netmiko, napalm, paramiko, pexpect, fabric, scrapli (L604-615, comment: "Kept ONLY in the test file by design"). **Protected packages**: `app/remediation/**` + `app/api/remediation.py` (L652-654). Same Import/ImportFrom root-module logic; docstrings/comments ignored (L650-651). Separately, plan-content tests enforce a command-level allow discipline: first proposed command must not be config-changing, ansible must carry `when: false`. |
| 13 timeouts | n/a. |
| 14 error handling | offender-list assertion messages. |
| 15 environment assumptions | `parents[1]` layout; conftest fixtures; simulator scenarios seeded (bgp_neighbor_down, acl_blocking_traffic, interface_errors_spike, route_missing). |
| 16 hardcoded names | recommendation_type "remediation_plan"; plan_type strings; roles admin/operator; scenario names. |
| 17 hardcoded addresses | none. |
| 18 output formats | Plan JSON via CLI; fenced-JSON details blob (` ```json `). |
| 19 existing tests | Is the test file. |
| 20 missing tests | Same scanner blind spots as 7a; no negative self-test of scanner; approval-state machine lacks reject→approve transition test. |
| 21 reuse classification | REFACTOR_AND_REUSE for the scanner + plan-only contract tests (first-command-observational, `when: false`, requires_approval pins); RETAIN_ONLY_IN_ORIGINAL_REPO for the auth/RBAC and template-specific tests unless VerifiedNet ports those features. |
| 22 exact reusable symbols | `_FORBIDDEN_EXECUTION_LIBS`, `_scan_files_for_execution_imports`, `_root_module`, `test_remediation_package_blocks_execution_library_imports` (as template), forbidden-first-token vector `("conf t","configure terminal","shutdown","no shutdown")` (L91), `when: false` + `DRAFT ONLY` assertions (L61-62). |
| 23 symbols to rewrite | Consolidate the scanner (shared util, see 7a); re-target protected package paths to VerifiedNet remediation-equivalent. |
| 24 symbols to reject | Phase 23 auth tests, operator seeding helpers, approval-API tests (feature-coupled). |
| 25 required adapter/interface | Same shared import-guard util as 7a. |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/tests/test_remediation_safety.py` (scanner + plan-only pins). |
| 27 provenance action | Cite the plan-only contract tests as the origin of VerifiedNet's "plan never executes" invariant. |
| 28 risks | Triplicated scanner already drifted once (telemetry variant grew the set) — port ONE shared implementation or VerifiedNet inherits the drift; scanner misses dynamic imports. |

## 7c. backend/tests/test_telemetry.py

| point | finding |
|---|---|
| 1 source repository | neuronoc-network-ops-assistant |
| 2 source commit | 5f2444742afbfd557d24d1e30fedd337f565f432 |
| 3 exact source path | backend/tests/test_telemetry.py (665 lines) |
| 4 file purpose | Phase 18A/18B telemetry tests: TelemetryEvent bounds/normalization, adapters-as-Protocols, no-persistence contract (row-count before/after), correlation preview mapping, and the WIDENED AST scan banning network + execution + async libs. |
| 5 public symbols | Event validation tests (enum rejection, empty required strings, extra='forbid' L120-127, missing observed_at); bounds tests using RAW_PAYLOAD_MAX_KEYS / RAW_PAYLOAD_MAX_VALUE_LEN / LABELS_MAX_ENTRIES (L139-154); `normalize_manual_event` pass-through; runtime_checkable Protocol isinstance tests (L180-211); API no-DB-write assertions via 6-table count snapshot (L253-288, L549-591); correlation tests (event_type match confidence 0.9 vs message-keyword 0.6 L427-446; severity map info/notice→low, warning→medium, error→high, critical→critical L405-424; unknown→telemetry_observation with would_create_incident=False L384-402; `persisted: Literal[False]` construction-time pin L483-490; correlation-key narrowing by peer/if/prefix); scanner block (L597-665). |
| 6 private symbols relevant to Wave A | `_valid_payload` (L52-66), `_bgp_event` (L294), `_counts` closures. |
| 7 internal imports | app.db.models (6 tables), app.telemetry (adapters, events, preview). |
| 8 external dependencies | pytest, fastapi.testclient, pydantic, sqlalchemy; ast/pathlib/datetime. |
| 9 global state | none. |
| 10 side effects | DB reads only (count assertions); source reads for scan. |
| 11 subprocess behavior | none. |
| 12 command allow-list behavior (AST scan mechanics) | Same scanner shape as 7a/7b but **widened banned set (exact 13)**: subprocess, ansible_runner, netmiko, napalm, paramiko, pexpect, fabric, scrapli, **pysnmp, easysnmp, netsnmp, socket, asyncio** (L605-621). Rationale comment L597-604: execution libs + SNMP libs + raw sockets + asyncio ("background-loop temptation"; revisitable). **Protected packages**: `app/telemetry/**` + `app/api/telemetry.py` (L656-658). Same root-module Import/ImportFrom logic; `assert files` non-empty guard (L659). Parameterization recipe for VerifiedNet: (package-path, banned-set) pairs — read-only analysis packages get the 8-lib execution set; ingest-adjacent packages get the 13-lib network+async set. |
| 13 timeouts | n/a. |
| 14 error handling | ValidationError expectations; offender-list message. |
| 15 environment assumptions | conftest client/db_session; `parents[1]` layout. |
| 16 hardcoded names | source "snmp:core-1", hostname core-1 (reuses lab device names); event/incident type strings; API paths /api/telemetry/{validate,correlate/preview}. |
| 17 hardcoded addresses | mgmt_ip 10.0.0.1; neighbor 10.0.0.21 (matches core-1 loopback); OID 1.3.6.1.2.1.2.2.1.8.1 (ifOperStatus). |
| 18 output formats | Echoed normalized event JSON; preview JSON with confidence floats (0.9/0.6/0.3) and nested `telemetry` payload key (L449-462). |
| 19 existing tests | Is the test file. |
| 20 missing tests | Scanner self-test absent (as 7a/7b); no test banning `os.system`/`ctypes` escape hatches; socket ban not verified against indirect use via http clients (requests would import socket transitively — scan only checks the package's OWN import statements, which is the intended scope but worth documenting). |
| 21 reuse classification | REFACTOR_AND_REUSE (scanner + bounds/no-persistence patterns); correlation-preview tests DESIGN_REFERENCE_ONLY unless the feature ports. |
| 22 exact reusable symbols | `_FORBIDDEN_LIBS` (13-lib set), `_scan_files_for_forbidden_imports`, the before/after row-count no-persistence pattern (`_counts`), bounds-test pattern driven by exported cap constants, `Literal[False]` persisted pin technique, runtime_checkable-Protocol adapter test pattern. |
| 23 symbols to rewrite | Consolidate scanner into the shared util with per-package banned sets; retarget table list in `_counts` to VerifiedNet schema. |
| 24 symbols to reject | NeuroNOC-specific correlation mappings (incident-type names) if VerifiedNet's taxonomy differs. |
| 25 required adapter/interface | Shared `import_guard` util (single source of both banned sets, exported as `EXECUTION_LIBS` and `NETWORK_AND_ASYNC_LIBS = EXECUTION_LIBS | {...}`). |
| 26 proposed VerifiedNet destination (candidate) | `verifiednet/tests/util/import_guard.py` + `verifiednet/tests/test_telemetry_safety.py`. |
| 27 provenance action | Cite the widened-set rationale comment (L597-604) — it documents deliberate policy (asyncio ban revisitable). |
| 28 risks | asyncio ban may conflict with a FastAPI-native VerifiedNet design — carry the ban consciously, per-package, not globally; three divergent copies of the scanner is the standing drift hazard. |

---

## Symbol-level harvest table — neuronoc-network-ops-assistant

| symbol | file | harvest verb | required modifications (explicit list) | Wave A role |
|---|---|---|---|---|
| `CommandRunner` / `CommandResult` types | backend/app/lab/collector.py | copy symbol nearly unchanged | none (rename module path only) | Injectable execution seam for all lab scraping + test fakes |
| `_default_runner` | backend/app/lab/collector.py | copy symbol with modifications | (1) make timeout a named constant/param (keep 10s default); (2) optionally add stdout byte cap at runner level; (3) keep 124/127 sentinel codes | Real subprocess adapter (argv-only, never shell) |
| `_assert_show_command` + `_FORBIDDEN_VTYSH_TOKENS` + `_VTYSH_TOKEN_SEP_RE` | backend/app/lab/collector.py | copy symbol nearly unchanged | (1) drop dead multi-word entries "no debug"/"conf t" or document why kept; (2) document hyphen-token limitation | Read-only vtysh command guard (core Wave A safety invariant) |
| `_assert_known_router` | backend/app/lab/collector.py | copy symbol with modifications | (1) take allow-set from injected LabTopology, not module constant; (2) VerifiedNet 2-router set | Container allow-list guard |
| `_vtysh_json` / `_vtysh_text` | backend/app/lab/collector.py | copy symbol with modifications | (1) container prefix from topology (`_container_for` inlined/configurable); (2) keep both guards as first statements | Guarded docker-exec-vtysh primitives |
| `_peers_from` / `_router_id_and_as` / `_safe_int` | backend/app/lab/collector.py | copy symbol nearly unchanged | none; keep dual-shape (ipv4Unicast.peers + top-level peers) fallback | FRR BGP-summary JSON parsing |
| `InterfaceObservation` + `_normalize_interface` + `_scrape_interfaces` | backend/app/lab/collector.py | copy symbol nearly unchanged | none (field defaults 'unknown'/None preserved) | Interface state normalization |
| `ConfigScrape` + `_scrape_running_config` + `_cap_text` | backend/app/lab/collector.py | copy symbol nearly unchanged | (1) cap constant configurable (keep 64 KiB default); (2) unify running-config path to use `_cap_text` (it currently duplicates the byte-slice logic) | Bounded running-config evidence |
| `RouteTableScrape` + `_route_entries` + `_route_protocol` + `_scrape_route_table` | backend/app/lab/collector.py | copy symbol with modifications | (1) expected-loopback map from injected topology; (2) keep prefix-contains-"/" conservatism; (3) keep 64 KiB cap | Route-table snapshot + missing-loopback detection |
| `_interface_is_down` / `_interface_has_errors` | backend/app/lab/collector.py | copy symbol nearly unchanged | none | Severity signal predicates |
| `collect_lab_snapshot` / `collect_lab_bgp_snapshot` | backend/app/lab/collector.py | reimplement behavior from specification | Split scrape orchestration from persistence (SnapshotSink protocol); replace Incident/Event/Evidence writes with VerifiedNet store; keep severity ladder (low/medium/high rules) and per-scrape error isolation as the spec; add pre-validation of `routers` kwarg on BGP path; consider aggregate deadline | Umbrella one-shot collector |
| CLI (`main`, `_build_parser`, `_run_watch`, MAX_ITERATIONS/MAX_INTERVAL_SECONDS) | backend/app/lab/collector.py | reimplement behavior from specification | Keep: mutex mode group, --watch requires --iterations, bounds 1..100 / 1..3600, NDJSON per iteration, per-iteration exception swallowing | Bounded dev CLI (anti-daemon guardrail) |
| `LAB_LOOPBACKS` / `EXPECTED_BGP_LOOPBACKS_FOR` derivation | backend/app/lab/collector.py | reimplement behavior from specification | Recompute for 2-router plan; keep the "each router expects all other loopbacks via bgp" derivation comprehension | Topology expectations |
| `_make_runner` / `_snapshot_runner` / `_summary_for` / `_peer` / `_interface_json` / `_route_json` | backend/tests/test_lab_collector.py | copy symbol with modifications | (1) 2-router AS/IP maps; (2) decouple from argv indexes cmd[2]/cmd[5] if runner argv changes; (3) container prefix rename | Canonical FRR-JSON test fakes |
| Guard test vectors (known-router rejects; show-command punctuation-adjacent forbidden tokens; `show debugging` allow-pin) | backend/tests/test_lab_collector.py | copy symbol nearly unchanged | rename router literals only — keep adversarial strings verbatim | Security regression suite for the guards |
| Truncation + severity-matrix + watch-loop tests | backend/tests/test_lab_collector.py | copy symbol with modifications | recompute event/evidence counts for 2 routers | Behavior pins |
| test_phase21b end-to-end chain test | backend/tests/test_lab_collector.py | retain only as test fixture | n/a — stays in original repo (depends on NeuroNOC Phases 4-7) | none (reference for future integration test shape) |
| Compose service block idiom (frr v8.4.1 pinned image, ro config mounts, cap_add, sysctl, JSON-grep healthcheck) | infra/lab/docker-compose.lab.yml | use only as architectural reference | Derive 2-service/1-network file; verify SYS_ADMIN necessity; decide pull_policy; adjust healthcheck Established-count thresholds | Lab runtime definition |
| /29-with-parked-gateway ipam idiom (gateway .6, routers .1/.2) | infra/lab/docker-compose.lab.yml | copy symbol nearly unchanged | one subnet (e.g. 172.30.1.0/29) instead of three | Deterministic link addressing |
| `lab.sh` verb dispatcher + `_valid_router`/`_valid_fault` | infra/lab/scripts/lab.sh | copy symbol with modifications | 2-router tables; container prefix; usage text | Operator ergonomics |
| `_bgp_neighbor_mode` (incl. `no neighbor X shutdown` heal idiom) | infra/lab/scripts/lab.sh | copy symbol nearly unchanged | peer/AS lookup tables only | Idempotent BGP fault inject/heal |
| `_iface_mode` (pre-check + ip link set) | infra/lab/scripts/lab.sh | copy symbol nearly unchanged | container prefix only | Interface fault inject/heal |
| frr.conf single-neighbor template (edge-1 shape: defaults traditional, lo /32, router bgp, no bgp default ipv4-unicast, no bgp ebgp-requires-policy, neighbor remote-as/description, address-family network+activate) | infra/lab/configs/edge-1/frr.conf | use only as architectural reference | New ASNs/IPs/hostnames per VerifiedNet plan; both routers use the single-neighbor shape | Router configs for 2-router lab |
| daemons file (bgpd/zebra/staticd yes, all else no) | infra/lab/configs/*/daemons | copy symbol nearly unchanged | none | FRR daemon enablement |
| `_scan_files_for_execution_imports` + `_root_module` + `_FORBIDDEN_EXECUTION_LIBS` (8-lib set) | backend/tests/test_remediation.py (duplicated in test_validation.py) | copy symbol with modifications | (1) consolidate three copies into ONE shared util `find_forbidden_imports(files, banned)`; (2) parameterize (package, banned-set) pairs; (3) keep the `assert files` non-empty guard; (4) document dynamic-import blind spot | AST import-guard for plan-only packages |
| `_FORBIDDEN_LIBS` widened 13-lib set (+pysnmp, easysnmp, netsnmp, socket, asyncio) | backend/tests/test_telemetry.py | copy symbol with modifications | express as `EXECUTION_LIBS \| NETWORK_LIBS \| {"asyncio"}`; make asyncio ban per-package and consciously revisitable | Import-guard for ingest-adjacent packages |
| No-persistence row-count pattern (`_counts` before/after) | backend/tests/test_telemetry.py | copy symbol with modifications | retarget table list to VerifiedNet schema | Read-only endpoint contract pin |
| Plan-only content pins (`requires_approval=True`, `when: false` + `DRAFT ONLY`, first-command-not-config vector) | backend/tests/test_remediation.py | copy symbol with modifications | adapt to VerifiedNet plan schema; keep exact forbidden-first-token tuple | Remediation safety invariants |
| `persisted: Literal[False]` type-pin technique | backend/tests/test_telemetry.py | copy symbol nearly unchanged | apply to VerifiedNet preview models | Construction-time immutability of preview-only contract |


# Appendix B — ClosCall per-file analyses (verbatim inspection fragment)

# Gate 2 file-level harvest fragment — repo `closcall`

Repo: /tmp/repos/closcall @ d192bf3cb86d96e6011f80d1d6915862397abab7. All inspection local; no network. NOTE (Gate 0): closcall has NO LICENSE file in the repo root (verified) — every DIRECT_REUSE/REFACTOR item below is conditional on a public-release/provenance decision.

---

## 1. src/closcall/domain/fabric.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | src/closcall/domain/fabric.py (249 lines) |
| 4 | file purpose | Fabric source-of-truth model + deterministic IPAM allocator: parses `lab/fabric.yaml` into `FabricSpec`, derives fully-resolved topology (loopbacks, mgmt, /31 p2p endpoints, host subnets, interface names) as a pure function (`allocate`, L131). Offline rendering input; never touches a device. |
| 5 | public symbols | `FabricSpec` (L68), `ResolvedNode` (L82), `ResolvedEndpoint` (L91), `ResolvedLink` (L98), `ResolvedHostNetwork` (L108), `ResolvedTopology` (L116), `load_fabric` (L125), `allocate` (L131), `Role` type alias (L19). `__all__` at L249 exports only FabricSpec/ResolvedTopology/allocate/load_fabric. |
| 6 | private symbols (Wave A relevant) | `_Topology` (L25: name, mtu, link_capacity_bps), `_Pools` (L32: p2p_supernet, loopback_supernet, management_supernet, host_subnet_template), `_Interfaces` (L40: leaf_uplink_to_spine1/2, leaf_downlink_to_host, spine_port_prefix, host_port), `_SwitchSpec` (L49: name, asn), `_HostSpec` (L55: name, leaf), `_Nodes` (L61: spines, leaves, hosts). All pydantic `extra="forbid"`. |
| 7 | internal imports | None (leaf module of the domain layer). |
| 8 | external dependencies | `yaml` (PyYAML, safe_load only, L127), `pydantic` BaseModel; stdlib `ipaddress`, `pathlib`, `typing`. |
| 9 | global state | None. `Role = Literal["spine","leaf","host"]` is a constant. |
| 10 | side effects | `load_fabric` reads a file (L127). `allocate` is pure. No writes, no logging. |
| 11 | subprocess behavior | None. |
| 12 | command allow-list behavior | N/A. |
| 13 | timeouts | None. |
| 14 | error handling | Delegated entirely to pydantic validation (extra=forbid, typed fields) and `ipaddress` ValueError on bad prefixes. No try/except; semantic checks (dup ASN, pool exhaustion) live in sibling `domain/validate.py::validate_fabric`. `allocate` can silently produce out-of-pool addresses if pools are too small (validate.py catches this separately). `uplinks` dict (L190) hard-fails with KeyError if >2 spines. |
| 15 | environment assumptions | A `lab/fabric.yaml`-shaped file exists; IPv4-only assumed (int arithmetic on network base works for v6 but templates/comments are v4); /31 P2P validated only against SR Linux 25.3.3 (docstring L6-7). |
| 16 | hardcoded names | Roles "spine"/"leaf"/"host" (L19); link kinds "fabric"/"access" (L101); link key format `f"{leaf}-{spine}"` (L199) and `f"{leaf}-{host}"` (L229). Interface names come from the spec but the STRUCTURE hardcodes: exactly 2 spine uplink slots (`uplinks = {1:..., 2:...}`, L190), one host downlink per leaf, spine port = `spine_port_prefix + leaf_index` (L206). |
| 17 | hardcoded addresses | None in code; math constants are hardcoded: gateway = subnet+1 (L220), host_ip = subnet+10 (L221), mgmt/loopback ordinal starts at 1 skipping .0 (L153), leaf even / spine odd in each /31 (L195-196), link index k = 2*(N-1)+(S-1) (L194). |
| 18 | output formats | Pydantic models; `ResolvedTopology.model_dump_json()` is byte-deterministic (relied on by test L89-93). Addresses as strings: loopback "x.x.x.x/32", p2p "x.x.x.x/31", access "x.x.x.x/24", mgmt bare host address. |
| 19 | existing tests | tests/unit/test_fabric_ipam.py (8 tests: counts, §7.2 worked example, index formula, loopback uniqueness, host subnet template, ASNs, /31 pairing+uniqueness, determinism); tests/unit/test_fabric_validate.py (10 tests via validate.py). |
| 20 | missing tests | No test for >2 spines (KeyError path), 0 hosts, leaf without host (L216-218 `continue` branch is only implicitly covered by full fixture), IPv6 pools, pool overflow behavior of `allocate` itself (only validate.py-level), non-contiguous host_subnet_template, spec loaded from string/dict rather than file. |
| 21 | reuse classification | REFACTOR_AND_REUSE (data model + allocation discipline are excellent; topology grammar is Clos-specific). |
| 22 | exact reusable symbols | `ResolvedNode`, `ResolvedEndpoint`, `ResolvedLink`, `ResolvedTopology`, `ResolvedHostNetwork`, `load_fabric` pattern, the deterministic-/31 allocation core (k-th /31, even/odd endpoint) as a helper. |
| 23 | symbols to rewrite | `FabricSpec` and all `_*` source-schema classes (spine/leaf/host grammar → generic node+link-list grammar); `allocate` (keep address math, replace the leaf×spine double loop with an explicit link list); `_Interfaces` (role-pair interface map → per-link interface fields). |
| 24 | symbols to reject | `_HostSpec`/host_networks .1/.10 convention as-is (gateway/host offsets should be spec fields, not constants); the `uplinks` 2-spine dict (L190). |
| 25 | required adapter/interface | New source schema: `nodes: [{name, role?, asn?}]` + `links: [{a: node/iface, b: node/iface}]`; allocator consumes ordered link list, assigns k-th /31 by list position; role becomes optional metadata. Keep ResolvedTopology as the stable downstream contract. |
| 26 | proposed VerifiedNet destination | verifiednet/domain/topology.py (resolved model) + verifiednet/domain/spec.py (new source grammar) + verifiednet/domain/ipam.py (allocation math). |
| 27 | provenance action | closcall has NO published license — obtain owner sign-off / public-release approval before copying any code verbatim; otherwise reimplement from this spec. Record commit d192bf3 in the provenance ledger either way. |
| 28 | risks | Clos assumptions are load-bearing in three places (uplinks dict, spine_port_prefix indexing, host-per-leaf loop) — partial harvest that keeps `allocate` will silently mis-address non-Clos topologies. /31 acceptance verified only on SR Linux 25.3.3. Silent pool overflow if validate step is skipped. |

**Explicit answers (fabric.py):**
- **FabricSpec carries:** `schema_version:int`, `topology{name,mtu,link_capacity_bps}`, `pools{p2p_supernet,loopback_supernet,management_supernet,host_subnet_template}`, `interfaces{leaf_uplink_to_spine1,leaf_uplink_to_spine2,leaf_downlink_to_host,spine_port_prefix,host_port}`, `nodes{spines:[{name,asn}],leaves:[{name,asn}],hosts:[{name,leaf}]}`.
- **ResolvedTopology carries:** `name`, `management_supernet`, `nodes:[ResolvedNode{name,role,asn|None,loopback("x/32"|None),management}]`, `links:[ResolvedLink{key,kind∈{fabric,access},a/b:ResolvedEndpoint{node,interface,address|None},capacity_bps,mtu}]`, `host_networks:[{leaf,subnet,gateway,host_ip}]`.
- **Deterministic address math:** leaf N (1-based) × spine S (1-based) → link index `k = 2*(N-1)+(S-1)` (L194); leaf addr = `p2p_base + 2k` (even), spine = `p2p_base + 2k + 1` (odd) as /31 (L195-196). The `2*` factor IS the spine count — hardcoded for 2 spines. Loopback = `lo_base + ordinal` /32, mgmt = `mgmt_base + ordinal`, ordinal 1.. over spines→leaves→hosts (hosts get mgmt only) (L153-186). Host subnet = `host_subnet_template.format(leaf_index=n)`, gateway = net+1, host = net+10 (L219-221). Spine-side interface = `spine_port_prefix + str(leaf_index)` (L206).
- **Clos/SR-Linux-specific vs generalizable:** Specific — three fixed roles, exactly-2-spines formula, role-pair interface naming, one-host-per-leaf access links, "ethernet-1/N" convention, /31-on-P2P verified only for SR Linux. Generalizable — ResolvedTopology/Node/Link/Endpoint shape, k-th /31 slicing, ordinal loopback/mgmt allocation, purity/determinism contract, extra=forbid schema hygiene.
- **What a 2-node point-to-point topology CANNOT express today:** (a) a router-to-router link where neither side is "spine"/"leaf" in a 2-spine matrix — the link set is generated only as leaves×spines, so 2 routers means either 1 spine+1 leaf, and then k=2*(0)+(0)=0 works but the `uplinks` dict and `leaf_uplink_to_spine2` field are still REQUIRED spec fields (extra=forbid, no optional) for a spine that doesn't exist; (b) arbitrary/explicit links (no `links:` section exists — links are implied by roles); (c) zero hosts is expressible but `host_subnet_template`/`host_port`/`leaf_downlink_to_host` remain mandatory fields; (d) per-link interface names (only role-pair template names); (e) >2 spines (KeyError L190); (f) same-role links (leaf-leaf, spine-spine), multi-links between the same pair, ASN on a "host", IPv6. **Minimum needed:** explicit link list, optional interfaces/pools fields, role-agnostic node list.

---

## 2. tests/unit/test_fabric_ipam.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | tests/unit/test_fabric_ipam.py (93 lines) |
| 4 | file purpose | Unit tests for the IPAM allocator against the real `lab/fabric.yaml` fixture (Bible §7.2 address math). |
| 5 | public symbols | 8 test functions: `test_loads_and_allocates`, `test_canon_worked_example_leaf1_spine1`, `test_canon_link_index_formula`, `test_loopbacks_are_unique_slash32_on_switches_only`, `test_host_subnets_match_template`, `test_asns_match_canon`, `test_all_p2p_addresses_unique_and_paired`, `test_allocation_is_deterministic`. |
| 6 | private symbols | `_topo()` helper (L11); `FABRIC` path constant (L8) resolving to repo `lab/fabric.yaml`. |
| 7 | internal imports | `closcall.domain.fabric` (ResolvedTopology, allocate, load_fabric). |
| 8 | external dependencies | stdlib `ipaddress`, `pathlib`; pytest runner. |
| 9 | global state | `FABRIC` module constant (repo-relative fixture path — couples tests to repo layout). |
| 10 | side effects | Reads lab/fabric.yaml from disk each test. |
| 11 | subprocess behavior | None. |
| 12 | command allow-list behavior | N/A. |
| 13 | timeouts | None. |
| 14 | error handling | None needed (happy-path assertions only). |
| 15 | environment assumptions | Repo checkout with lab/fabric.yaml two dirs up from tests/unit. |
| 16 | hardcoded names | "closcall-2s4l", "leaf1-spine1", "leaf2-spine2", "ethernet-1/1", "ethernet-1/2", node names leaf1..leaf4/spine1/spine2. |
| 17 | hardcoded addresses | 10.0.0.0/31, 10.0.0.1/31, 10.0.0.6/31, 10.0.0.7/31, 172.16.1.0/24, 172.16.1.1, 172.16.1.10, 172.16.4.0/24; ASNs 65101/65102/65001/65004. |
| 18 | output formats | N/A (assertions). Notably asserts byte-identical `model_dump_json()` determinism (L89-93). |
| 19 | existing tests | This is the test file; invariant-style tests (L73-86 pairing/uniqueness, L89 determinism) generalize; example-style tests (L25-42, L55-70) are 2s4l-specific. |
| 20 | missing tests | No negative tests here (in validate file); no property-based tests over pool sizes/topology shapes; no test of a minimal 1-spine or 2-node spec. |
| 21 | reuse classification | REFACTOR_AND_REUSE (invariant tests) / RETAIN_ONLY_IN_ORIGINAL_REPO (canon worked-example values). |
| 22 | exact reusable symbols | `test_all_p2p_addresses_unique_and_paired`, `test_loopbacks_are_unique_slash32_on_switches_only`, `test_allocation_is_deterministic` (as invariants over any spec). |
| 23 | symbols to rewrite | `_topo`/`FABRIC` — replace repo-file fixture with an inline dict/pytest fixture; re-derive worked examples for the new topology grammar. |
| 24 | symbols to reject | Hardcoded 2s4l expectations (counts=10 nodes/8+4 links, specific ASNs/addresses). |
| 25 | required adapter/interface | Inline spec fixture builder for the new grammar. |
| 26 | proposed VerifiedNet destination | verifiednet tests/unit/test_ipam.py. |
| 27 | provenance action | No license — reimplement invariant tests from spec (trivially clean-room); record commit in ledger. |
| 28 | risks | Tests read the shared repo fixture; porting verbatim would silently pass against wrong math if fixture constants are copied along with formula bugs. |

---

## 3. tests/unit/test_fabric_validate.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | tests/unit/test_fabric_validate.py (85 lines) |
| 4 | file purpose | Malformed-fixture rejection tests for `domain/validate.py::validate_fabric` (B01, Gate 2 exit): mutate the real fabric.yaml dict and assert specific error strings. |
| 5 | public symbols | 10 tests: canonical-valid, duplicate ASN (L24), ASN out of private range (L31), dangling host leaf (L38), bad prefix /33 (L45), p2p pool too small (L52), empty interface (L59), duplicate node name (L66), unknown field rejected at parse (pydantic extra=forbid, L73), fixture-mutation isolation sanity (L80). |
| 6 | private symbols | `_raw()` (L16) — reload fabric.yaml as dict; `FABRIC` path constant. |
| 7 | internal imports | `closcall.domain.fabric.FabricSpec`, `closcall.domain.validate.validate_fabric` (validate_fabric returns `list[str]` of human-readable errors, empty == valid; covers semantic checks pydantic can't: dup names/ASNs, private ASN range, dangling host→leaf, pool sizing, empty interface names — see validate.py L18+). |
| 8 | external dependencies | pytest, yaml; stdlib copy, pathlib. |
| 9 | global state | `FABRIC` path constant. |
| 10 | side effects | Reads lab/fabric.yaml per test. |
| 11-13 | subprocess/allow-list/timeouts | None / N/A / None. |
| 14 | error handling | Asserts on error-string substrings ("duplicate ASN", "private", "unknown leaf", "not a valid network", "/31s but", "interface mapping", "duplicate node name") — string-coupled to validate.py messages. |
| 15 | environment assumptions | Repo checkout with lab/fabric.yaml. |
| 16 | hardcoded names | "leaf99", index-based mutations of leaves/spines/hosts lists. |
| 17 | hardcoded addresses | "10.0.0.0/33", "10.0.0.0/30". |
| 18 | output formats | N/A. |
| 19 | existing tests | This file; good negative coverage of the validator. |
| 20 | missing tests | No test for loopback/mgmt pool exhaustion, overlapping pools, host subnet collisions across leaves, bad host_subnet_template placeholder; no direct unit tests of `_report_dupes`. |
| 21 | reuse classification | DESIGN_REFERENCE_ONLY (the check LIST is the harvest; strings and fixture mutations are repo-specific). |
| 22 | exact reusable symbols | The validation-check inventory: dup node names, dup ASNs, private-ASN range, dangling endpoint refs, prefix validity, pool capacity vs required /31 count, non-empty interface map. |
| 23 | symbols to rewrite | All tests, against the new spec grammar and new error taxonomy (prefer structured error codes over substrings). |
| 24 | symbols to reject | Substring-matching assertions; repo-file-mutation fixture style. |
| 25 | required adapter/interface | New validator returning typed errors (code + message) for the link-list grammar. |
| 26 | proposed VerifiedNet destination | verifiednet tests/unit/test_spec_validate.py (+ verifiednet/domain/validate.py reimplementation). |
| 27 | provenance action | No license — clean-room reimplementation from the check inventory; log commit. |
| 28 | risks | Error-string coupling makes ports brittle; pool-capacity check formula (needed /31s) must be re-derived for arbitrary link lists (currently 2×leaves). |

---

## 4. src/closcall/chaos/ledger.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | src/closcall/chaos/ledger.py (104 lines) |
| 4 | file purpose | Durable write-ahead chaos ledger (§8.3): a `planned` record with the EXACT cleanup payload is fsync'd BEFORE any impairment; subsequent phase transitions appended; startup reconciler replays and cleans up anything left unreconciled or quarantines the lab. |
| 5 | public symbols | `Phase` (StrEnum, L26), `UNRECONCILED` (frozen phase set, L38), `LedgerRecord` (frozen dataclass, L41), `Ledger` (L54: `append`, `records`, `outstanding`), `now_record` factory (L84). |
| 6 | private symbols | None. |
| 7 | internal imports | None (leaf module; the reconciler/faults live in chaos/faults.py, not inspected). |
| 8 | external dependencies | stdlib only: json, os, time, dataclasses, enum.StrEnum (Python ≥3.11), pathlib. |
| 9 | global state | None (UNRECONCILED is an immutable constant). |
| 10 | side effects | `Ledger.__init__` mkdirs the parent (L57); `append` writes + flush + `os.fsync` (L60-63); `records` reads the whole file. |
| 11 | subprocess behavior | None here (cleanup payloads like `tc qdisc del ...` are DATA carried in `cleanup`, executed elsewhere). |
| 12 | command allow-list behavior | None — `cleanup: dict[str,str]` is unconstrained; whatever executes it must enforce its own allow-list. |
| 13 | timeouts | None. |
| 14 | error handling | Minimal: no file locking, no corruption tolerance (a truncated/garbled JSONL line raises json.JSONDecodeError in `records`, L71), no schema versioning of records, unknown phase string raises ValueError (L72). `LedgerRecord(**d)` breaks if fields are added/removed across versions. |
| 15 | environment assumptions | Local POSIX filesystem where fsync gives durability; single-writer (no lock); Python 3.11+ (StrEnum). File-backed by design "for now" — canonical home is the `evaluation.fault_injections` Postgres table at Gate 7 (docstring L8-10). |
| 16 | hardcoded names | Phase strings planned/injecting/active/clearing/cleared/settled/failed/quarantined; target dict convention `{node, interface}` (comment L46). |
| 17 | hardcoded addresses | None. |
| 18 | output formats | JSONL, one `asdict(LedgerRecord)` object per line; phase serialized as its string value. |
| 19 | existing tests | tests/unit/test_ledger.py (4 tests). |
| 20 | missing tests | No corrupted-line/partial-write test, no concurrent-writer test, no FAILED/QUARANTINED/SETTLED phase in any test, no test that `records()` on missing file returns [] (only implicitly), no round-trip test of `detail` payloads, no reconciler integration test at this layer. |
| 21 | reuse classification | DIRECT_REUSE (with the license caveat) — small, stdlib-only, generic write-ahead pattern. |
| 22 | exact reusable symbols | `Phase`, `UNRECONCILED`, `LedgerRecord`, `Ledger`, `now_record` — all five. |
| 23 | symbols to rewrite | `Ledger.records` (add tolerant/versioned decode); optionally rename `simulated` default semantics for VerifiedNet. |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | Storage interface if Postgres backend is wanted later (`append/records/outstanding` is already the natural protocol); executor-side allow-list for `cleanup` payloads. |
| 26 | proposed VerifiedNet destination | verifiednet/chaos/ledger.py. |
| 27 | provenance action | No license — this is small enough to reimplement from this spec in <1h if release approval is not obtained; otherwise copy with attribution + commit pin. |
| 28 | risks | fsync-per-append is slow at scale (fine for chaos cadence); no crash-safety for torn writes (a partial last line poisons ALL reads including `outstanding()` — reconciliation would fail exactly when needed); single-writer assumption undocumented in code. |

**Explicit answers (ledger.py):** Phase enum = PLANNED, INJECTING, ACTIVE, CLEARING, CLEARED, SETTLED, FAILED, QUARANTINED (L26-34). LedgerRecord fields = injection_id, fault_class, phase, target:{node,interface}, cleanup (exact undo payload dict), event_time (UTC epoch), monotonic (for durations), simulated (default True, §2.12), detail dict (L41-51). Append = open-append, write JSON line, flush, fsync (durable WAL, L59-63); read = full-file re-parse to LedgerRecord list (L65-74). Undo/cleanup tracking: cleanup payload written durably in the PLANNED record before apply; `outstanding()` folds to latest-phase-per-injection_id and returns those in {PLANNED, INJECTING, ACTIVE, CLEARING} (L76-81) — those owe cleanup via their stored payload or trigger quarantine. Backing: fsync'd JSONL FILE (not DB, not memory); Postgres migration explicitly deferred to Gate 7.

---

## 5. tests/unit/test_ledger.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | tests/unit/test_ledger.py (53 lines) |
| 4 | file purpose | Happy-path tests of ledger append/read and `outstanding()` reconciliation folding. |
| 5 | public symbols | `test_append_and_read`, `test_outstanding_flags_unreconciled`, `test_cleared_then_nothing_outstanding`, `test_latest_phase_per_id_wins`. |
| 6 | private symbols | `_rec()` helper (L8) writing an "impaired_link" record with `tc qdisc del dev e1-1 root` cleanup. |
| 7 | internal imports | closcall.chaos.ledger (Ledger, Phase, now_record). |
| 8 | external dependencies | pytest tmp_path fixture only. |
| 9-13 | global/side/subprocess/allow-list/timeouts | None / tmp files only / none / N/A / none. |
| 14 | error handling | Not tested at all. |
| 15 | environment assumptions | tmp_path filesystem. |
| 16 | hardcoded names | "impaired_link", "leaf1"/"ethernet-1/1", "e1-1", injection ids "a"/"b". |
| 17 | hardcoded addresses | None. |
| 18 | output formats | N/A. |
| 19 | existing tests | Covers: round-trip incl. simulated=True default (L26) and cleanup payload persistence; outstanding for id left ACTIVE while CLEARED id excluded (L29-39); full phase sequence → nothing outstanding; latest-phase-wins fold (L49-53). |
| 20 | missing tests | NO failure paths: no corrupted/truncated JSONL, no FAILED/QUARANTINED/SETTLED phases, no missing-file `records()`, no fsync/durability simulation, no multi-process append, no detail round-trip, no unknown-phase decode error. |
| 21 | reuse classification | DIRECT_REUSE (with added failure-path tests). |
| 22 | exact reusable symbols | All four tests + `_rec` helper (rename fixture constants). |
| 23 | symbols to rewrite | Add: corrupted-line, terminal-phase, empty-file tests. |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | None. |
| 26 | proposed VerifiedNet destination | verifiednet tests/unit/test_ledger.py. |
| 27 | provenance action | No license — trivial to reimplement; log commit. |
| 28 | risks | Coverage gap gives false confidence in exactly the reconciliation-under-failure scenario the ledger exists for. |

---

## 6. src/closcall/evidence/claims.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | src/closcall/evidence/claims.py (129 lines) |
| 4 | file purpose | §12.2 typed claims + deterministic verifier: a claim is a typed proposition executed against an immutable evidence `Snapshot` with no model in the loop; adversarially strict (mismatch → INSUFFICIENT, never spuriously supported; SUSTAINED defeats cherry-picking). Gate on prose: only verified claims commit. |
| 5 | public symbols | `Verdict` (L20), `Predicate` (L26), `Evidence` (L31), `Snapshot` (L44, with `by_id`), `Claim` (L55), `verify` (L89), `committable` (L116). |
| 6 | private symbols | `_NUMERIC_OPS` (L71: >, >=, <, <=, ==), `_satisfies` (L80: per-sample eval; returns None for ill-typed ordering-on-non-numeric). |
| 7 | internal imports | None. |
| 8 | external dependencies | stdlib only (dataclasses, enum.StrEnum). |
| 9 | global state | None (`_NUMERIC_OPS` constant). |
| 10 | side effects | None — fully pure. |
| 11-13 | subprocess/allow-list/timeouts | None / operator set is effectively an allow-list (unknown operator → INSUFFICIENT, L91-92) / none. |
| 14 | error handling | No exceptions by design: every degenerate case (unknown operator, no matched evidence, ill-typed comparison) collapses to `Verdict.INSUFFICIENT` (L92, L105, L109). |
| 15 | environment assumptions | Epoch-seconds float times; evidence_id lookup is linear scan per id (fine for small snapshots). |
| 16 | hardcoded names | Operator strings; example subjects/units in comments only. |
| 17 | hardcoded addresses | None. |
| 18 | output formats | `Verdict` StrEnum values "supported"/"contradicted"/"insufficient". |
| 19 | existing tests | tests/unit/test_claims.py (10 tests, all six §12.2 adversarial cases). |
| 20 | missing tests | `Predicate.ANY` never tested (only SUSTAINED); polarity=False SUPPORTED path untested (only its contradicted side); unknown-operator branch untested; `Snapshot.by_id` miss untested directly; `trusted=False` evidence is NOT consulted by verify — no test pins that (deliberate?) behavior; mixed trusted/untrusted snapshots untested; string `==` equality supported-path untested. |
| 21 | reuse classification | DIRECT_REUSE (license caveat) — domain-agnostic, pure, stdlib-only. |
| 22 | exact reusable symbols | All: Verdict, Predicate, Evidence, Snapshot, Claim, verify, committable, _satisfies, _NUMERIC_OPS. |
| 23 | symbols to rewrite | Possibly `verify` to (a) index snapshot by id (O(n·m) now, L95-103), (b) decide explicitly whether untrusted evidence may support a claim (currently it CAN — `trusted` is carried but ignored by verify). |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | None for Wave A; evidence producers must emit Evidence with matching subject/metric/unit vocabularies. |
| 26 | proposed VerifiedNet destination | verifiednet/evidence/claims.py. |
| 27 | provenance action | No license — small enough for spec-level reimplementation if approval fails; otherwise copy with commit pin d192bf3. |
| 28 | risks | `trusted` flag not enforced in verify — an untrusted log line can currently SUPPORT a claim (defense delegated to plan/executor per tools.py docstring, but verifier-level enforcement would be stronger); float `==` comparisons are exact (fragile for computed rates); interval bounds inclusive on both ends (document). |

**Explicit answers (claims.py):** Predicate = {SUSTAINED: operator holds for EVERY in-window matched sample; ANY: at least one} (L26-29). Verdict = {SUPPORTED, CONTRADICTED, INSUFFICIENT} (L20-23). Evidence fields = evidence_id, subject, metric_or_event, value (float|str), unit, at, trusted=True (False for logs/runbooks) (L31-41). Snapshot = as_of + tuple[Evidence,...], `by_id` linear lookup (L44-52). Claim fields = claim_id, predicate_type, subject, metric_or_event, operator (">",">=","<","<=","=="), comparison (float|str), unit, interval (lo,hi) inclusive, polarity (False = assert negation), evidence_ids tuple (L55-68). verify() semantics: unknown operator → INSUFFICIENT (L91); matched = referenced evidence with EXACT subject+metric+unit match and lo≤at≤hi (L95-103); no matches → INSUFFICIENT (L104); any ill-typed sample (ordering op on non-numeric) → INSUFFICIENT for the whole claim (L107-109); else holds = all(SUSTAINED)/any(ANY), truth = holds XOR-with-polarity, → SUPPORTED/CONTRADICTED (L111-113). committable(v) = `v is Verdict.SUPPORTED` only (L116-118) — contradicted AND insufficient both abstain.

---

## 7. tests/unit/test_claims.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | tests/unit/test_claims.py (98 lines) |
| 4 | file purpose | Adversarial fixtures for the verifier: "no bad claim may reach supported". Pure/offline. |
| 5 | public symbols | 10 tests: genuine sustained supported; cherry-picked sample → CONTRADICTED (L57); wrong polarity → CONTRADICTED; wrong unit / wrong interface / out-of-time / nearby-irrelevant-metric → INSUFFICIENT (L69-86); ill-typed comparison → INSUFFICIENT (L89); only SUPPORTED committable (L95). |
| 6 | private symbols | `_ev`, `_claim` (override-kwargs builders), `_snap`; constants SUBJECT="leaf1:ethernet-1/1", METRIC="in_error_rate", UNIT="packets_per_s". |
| 7 | internal imports | closcall.evidence.claims (full public surface). |
| 8 | external dependencies | None beyond pytest runner. |
| 9-13 | global/side/subprocess/allow-list/timeouts | Constants only / none / none / N/A / none. |
| 14 | error handling | N/A. |
| 15 | environment assumptions | None — fully synthetic. |
| 16 | hardcoded names | leaf1:ethernet-1/1, in_error_rate, in_discard_rate, packets_per_s, bytes, state (vocabulary only). |
| 17 | hardcoded addresses | None. |
| 18 | output formats | N/A. |
| 19 | existing tests | Does test_claims cover all predicates? **NO — only SUSTAINED.** `Predicate.ANY` has zero coverage. Also uncovered: polarity=False→SUPPORTED, string `==` supported path, unknown operator, empty evidence_ids, evidence_ids referencing absent ids (only subject-mismatch variants), trusted=False evidence in verify. |
| 20 | missing tests | As above, plus boundary at==lo/hi inclusivity pinning, mixed matched/unmatched evidence sets. |
| 21 | reuse classification | DIRECT_REUSE (extend with the missing cases). |
| 22 | exact reusable symbols | All 10 tests + the `_ev/_claim/_snap` builder pattern. |
| 23 | symbols to rewrite | Add ANY-predicate, polarity-supported, boundary, unknown-operator, untrusted-evidence tests. |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | None. |
| 26 | proposed VerifiedNet destination | verifiednet tests/unit/test_claims.py. |
| 27 | provenance action | No license — reimplement or copy per Gate 0 ruling; log commit. |
| 28 | risks | ANY-predicate gap means half the predicate enum ships unverified. |

---

## 8. src/closcall/evidence/tools.py (scoped inspection per mandate)

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | src/closcall/evidence/tools.py (167 lines; full file read, get_metric_window/get_ranked_links noted but de-emphasized per scope) |
| 4 | file purpose | §12.3 evidence tools: nine read-only, scoped, bounded typed tools the Collect stage uses to build snapshots. One shared envelope (`_emit`) enforces as-of bound, result limit, budget, trace, and trust tagging. `EvidenceSource` deliberately has NO ground-truth/evaluation method. |
| 5 | public symbols | `BudgetExhausted` (L21), `Budget` (L25), `ToolContext` (L41), `METRIC_TEMPLATES` (L51), `Record` (L59), `EvidenceSource` Protocol (L71), nine `get_*/search_*` functions (L109-148): get_interface_state, get_bgp_state, get_metric_window, get_log_events, get_topology_neighbors, get_ranked_links, get_incident_summary, search_runbooks, get_similar_resolved_incidents. |
| 6 | private symbols | `_UNTRUSTED_SOURCES = {"log","runbook"}` (L87); `_emit` envelope (L90-106). |
| 7 | internal imports | `closcall.evidence.claims.Evidence` (L18) — tools emit Evidence directly. |
| 8 | external dependencies | stdlib only (dataclasses, typing.Protocol). |
| 9 | global state | None mutable; METRIC_TEMPLATES/_UNTRUSTED_SOURCES constants. Budget/ToolContext instances carry per-diagnosis mutable state (calls, rows, trace). |
| 10 | side effects | None external; mutates ctx.budget and ctx.trace. |
| 11 | subprocess behavior | None. |
| 12 | command allow-list behavior | `get_metric_window` accepts ONLY allow-listed template ids from METRIC_TEMPLATES (oper_state_window, in_error_rate_window, in_discard_rate_window, octet_rate_window); free-form metric query → ValueError (L120-121). |
| 13 | timeouts | None (bounding is by call/row budget, not wall clock). |
| 14 | error handling | BudgetExhausted (RuntimeError) raised AFTER incrementing (post-charge check, L32-38 — the over-budget call is counted, and its rows were already fetched from the source); ValueError for non-allow-listed template. No other exceptions. |
| 15 | environment assumptions | An EvidenceSource impl exists over core/runtime data only; epoch-second floats. |
| 16 | hardcoded names | Source tags "telemetry"/"bgp"/"log"/"runbook"/"topology"/"ranking"/"summary" (L68); metric template vocabulary; evidence_id format `f"{source}:{subject}:{metric}:{at}"` (L97 — collision-prone if two same-second samples). |
| 17 | hardcoded addresses | None. |
| 18 | output formats | list[Evidence]; trace lines `f"{tool}: {n} evidence (as_of=...)"` (L94). |
| 19 | existing tests | tests/unit/test_tools.py (8 tests: as-of drop, limit cap, call-budget raise, row-budget raise, log/runbook untrusted, template allow-list incl. injection string, wrong-incident scope, structural no-ground-truth-accessor guard on the Protocol). |
| 20 | missing tests | get_bgp_state/get_topology_neighbors/get_ranked_links/get_incident_summary/get_similar_resolved_incidents never exercised (FakeSource stubs them to []); trace content untested; evidence_id collision untested; the metric_window `lo = as_of - ctx.limit` oddity (L123 — reuses the ROW limit as a TIME window width, a unit conflation bug/smell) untested and unnoticed. |
| 21 | reuse classification | REFACTOR_AND_REUSE (envelope + Budget/ToolContext are Wave-A gold; the nine-tool roster and source-tag vocabulary are closcall-shaped). |
| 22 | exact reusable symbols | Budget, BudgetExhausted, ToolContext, Record, `_emit` envelope, get_interface_state, get_bgp_state, get_log_events, METRIC_TEMPLATES pattern, EvidenceSource protocol shape. |
| 23 | symbols to rewrite | evidence_id stamping (add a per-call sequence/uuid to avoid collisions); `get_metric_window` window computation (separate time-window param from row limit); trim EvidenceSource to the Wave-A subset (interface_state, bgp_state, log_events, metric_window) with the rest optional. |
| 24 | symbols to reject | None outright; get_ranked_links/get_similar_resolved_incidents/get_incident_summary deferred past first evidence flow (exist at L135-148, details out of scope per mandate). |
| 25 | required adapter/interface | A VerifiedNet EvidenceSource implementation over its own telemetry store; keep the Protocol as the seam. |
| 26 | proposed VerifiedNet destination | verifiednet/evidence/tools.py. |
| 27 | provenance action | No license — reimplement from this spec or copy after release approval; pin commit. |
| 28 | risks | Post-charge budget check means the over-limit query already hit the source (budget limits damage, doesn't prevent the fetch); evidence_id collisions can alias distinct samples inside claims (`by_id` returns first match); `as_of - ctx.limit` unit conflation (rows vs seconds) at L123. |

**Explicit answers (tools.py):** Budget mechanics — dataclass{max_calls, max_rows, calls, rows}; `charge(n_rows)` increments calls by 1 and rows by n, THEN raises BudgetExhausted if calls>max_calls or rows>max_rows (L32-38); charged on kept (post-filter, post-cap) rows only (L93). ToolContext fields — incident_id (scope filter), as_of (upper time bound, no future reads), limit (per-call result cap), budget, trace:list[str] (L41-47). EvidenceSource protocol methods — interface_state(incident_id, subject), bgp_state(incident_id, subject), metric_window(incident_id, subject, metric, lo, hi), log_events(incident_id), topology_neighbors(subject), ranked_links(incident_id), incident_summary(incident_id), runbooks(query), similar_incidents(incident_id) (L74-84); no ground-truth accessor by design. Record shape — frozen{subject, metric_or_event, value:float|str, unit, at, source∈{telemetry,bgp,log,runbook,topology,ranking,summary}} (L59-68). Provenance stamping — `_emit` filters r.at≤as_of, slices [:limit], charges budget, appends trace line, and mints Evidence with evidence_id=f"{source}:{subject}:{metric_or_event}:{at}" and trusted = source∉{log,runbook} (L90-106).

---

## 9. src/closcall/datasets/manifest.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | src/closcall/datasets/manifest.py (107 lines) |
| 4 | file purpose | §9.4 dataset manifest: pins everything needed to reproduce/audit a dataset. `build_manifest` is a pure assembler over already-computed hashes; `manifest_hash` = sha256 over all fields so any drift changes it. |
| 5 | public symbols | `DatasetManifest` (frozen dataclass, L31), `sha256_file` (L52), `build_manifest` (L57), `REQUIRED_NONEMPTY` (L18). |
| 6 | private symbols | None. |
| 7 | internal imports | None. |
| 8 | external dependencies | stdlib only (hashlib, json, dataclasses, pathlib). |
| 9 | global state | None (REQUIRED_NONEMPTY constant tuple). |
| 10 | side effects | `sha256_file` reads a file; `build_manifest` pure. |
| 11-13 | subprocess/allow-list/timeouts | None / N/A / none. |
| 14 | error handling | ValueError on empty required field (L97-99: dataset_kind, split_protocol, split_manifest_hash, topology_hash, config_hash, feature_schema_hash, code_revision, dependency_lock_hash, creation_command) and on empty source_run_ids (L100-101). Note graph_schema_hash, image_digests, seeds, master_seed, exclusions, content_hashes are NOT in REQUIRED_NONEMPTY. |
| 15 | environment assumptions | None. |
| 16 | hardcoded names | Field name comments bind config_hash to lab/fabric.yaml and dependency_lock_hash to uv.lock (comments only, L38/L42). |
| 17 | hardcoded addresses | None. |
| 18 | output formats | Hash = sha256 of `json.dumps(body, sort_keys=True, default=list)` with manifest_hash excluded (L95-102); manifest itself serialized by callers via asdict. |
| 19 | existing tests | tests/unit/test_manifest.py (6 tests). |
| 20 | missing tests | Not-required-but-empty fields (e.g. image_digests={}) accepted silently — untested/undocumented; dict key-order insensitivity of the hash; master_seed=0 falsiness edge (0 would NOT trip REQUIRED_NONEMPTY since not listed, but seeds={} passes too). |
| 21 | reuse classification | DIRECT_REUSE (license caveat) — domain-neutral provenance primitive. |
| 22 | exact reusable symbols | DatasetManifest, build_manifest, sha256_file, REQUIRED_NONEMPTY. |
| 23 | symbols to rewrite | Possibly extend REQUIRED_NONEMPTY (image_digests/seeds) for VerifiedNet policy; consider streaming sha256_file for large files (currently read_bytes whole-file, L54). |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | None — callers supply all inputs. |
| 26 | proposed VerifiedNet destination | verifiednet/datasets/manifest.py (or verifiednet/provenance/manifest.py). |
| 27 | provenance action | No license — reimplement (trivial) or copy after approval; pin commit. |
| 28 | risks | `default=list` in the hash json.dumps silently serializes unexpected types — a type drift could change hashes without an error; whole-file read for sha256_file on multi-GB parquet. |

**Explicit answers (manifest.py):** DatasetManifest fields = dataset_kind, source_run_ids, split_protocol, split_manifest_hash, topology_hash, config_hash (lab/fabric.yaml hash), feature_schema_hash, graph_schema_hash, code_revision, dependency_lock_hash (uv.lock hash), image_digests:dict, master_seed:int, seeds:dict[str,int], exclusions:list (quarantined ids), creation_command, content_hashes:dict[uri→sha256], manifest_hash (computed) (L31-49). build_manifest inputs = all 16 fields keyword-only; validates REQUIRED_NONEMPTY + non-empty source_run_ids; hashes sorted-keys JSON of everything except manifest_hash; returns rebuilt frozen instance (L57-104). sha256_file = read-only whole-file sha256 hexdigest (L52-54), used by callers for config_hash, dependency_lock_hash, and content_hashes entries.

---

## 10. tests/unit/test_manifest.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | tests/unit/test_manifest.py (79 lines) |
| 4 | file purpose | Manifest builder tests: field completeness, hash determinism, drift-sensitivity, required-field enforcement, sha256_file round-trip. Pure/offline. |
| 5 | public symbols | 6 tests: all-required-fields present + 64-char hash; deterministic hash; any-field-change changes hash (master_seed drift, L58); missing required field raises; empty source_run_ids raises; sha256_file round-trip vs hashlib (L74). |
| 6 | private symbols | `_kwargs()` synthetic-input builder (L13). |
| 7 | internal imports | closcall.datasets.manifest. |
| 8 | external dependencies | pytest (tmp_path). |
| 9-13 | global/side/subprocess/allow-list/timeouts | None / one tmp file / none / N/A / none. |
| 14 | error handling | pytest.raises with message-substring matches. |
| 15 | environment assumptions | None. |
| 16 | hardcoded names | "gate9-corpus", "location-inductive", "make corpus", synthetic 64-char hashes. |
| 17 | hardcoded addresses | None. |
| 18 | output formats | N/A. |
| 19 | existing tests | Good core coverage of the builder contract. |
| 20 | missing tests | Drift-sensitivity tested for ONE field only (master_seed) — not parametrized over all 16; no test that manifest_hash excludes itself; no non-required-empty-field behavior pin; no large-file/streaming sha256 test. |
| 21 | reuse classification | DIRECT_REUSE. |
| 22 | exact reusable symbols | All 6 tests + `_kwargs` builder. |
| 23 | symbols to rewrite | Parametrize drift test across every field. |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | None. |
| 26 | proposed VerifiedNet destination | verifiednet tests/unit/test_manifest.py. |
| 27 | provenance action | No license — reimplement or copy per Gate 0 ruling; log commit. |
| 28 | risks | Single-field drift test could miss a field accidentally dropped from the hash body. |

---

## 11. src/closcall/observability/logging.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | src/closcall/observability/logging.py (48 lines) |
| 4 | file purpose | Structured JSON logging to stdout, stdlib-only ("simplify, never add"); one JSON object per line, UTC timezone-aware timestamps. |
| 5 | public symbols | `JsonFormatter` (L22), `configure_logging(level="INFO")` (L42). |
| 6 | private symbols | `_STANDARD_ATTRS` (L15) — attrs of a probe LogRecord ∪ {message, asctime, taskName}; anything else on a record is treated as `extra=` and emitted. |
| 7 | internal imports | None. |
| 8 | external dependencies | stdlib only (datetime, json, logging, sys). |
| 9 | global state | Mutates the ROOT logger: `root.handlers[:] = [handler]` replaces ALL handlers (L47) — idempotent but destructive to any pre-existing handlers; `_STANDARD_ATTRS` computed at import. |
| 10 | side effects | configure_logging installs handler + sets root level; formatter writes to stdout via StreamHandler. |
| 11-13 | subprocess/allow-list/timeouts | None / N/A / none. |
| 14 | error handling | `json.dumps(..., default=str)` (L39) makes any extra value serializable; exceptions rendered via formatException into "exception" key (L37-38). Non-JSON-safe extras silently stringified. |
| 15 | environment assumptions | stdout is the log sink (container-native); Python ≥3.8 for datetime.UTC → actually 3.11+ (`datetime.UTC`). |
| 16 | hardcoded names | Payload keys timestamp/level/logger/message/exception. |
| 17 | hardcoded addresses | None. |
| 18 | output formats | One JSON line: {timestamp: ISO-8601 UTC "+00:00", level, logger, message, ...extras, exception?} (L26-39). |
| 19 | existing tests | tests/unit/test_logging.py (5 tests). |
| 20 | missing tests | Non-serializable extra (default=str path); extra colliding with payload keys (e.g. extra={"level":...} — logging forbids some but not "timestamp"); taskName/asctime exclusion behavior across Python versions; handler replacement destroying third-party handlers. |
| 21 | reuse classification | DIRECT_REUSE (license caveat). |
| 22 | exact reusable symbols | JsonFormatter, configure_logging, _STANDARD_ATTRS trick. |
| 23 | symbols to rewrite | Optionally make configure_logging non-destructive (add-if-absent) or accept a stream param for testability. |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | None. |
| 26 | proposed VerifiedNet destination | verifiednet/observability/logging.py. |
| 27 | provenance action | No license — 48-line stdlib file, trivially reimplementable clean-room; log commit. |
| 28 | risks | Root-handler wipe (L47) can swallow pytest/uvicorn handlers; `_STANDARD_ATTRS` probe is fragile against future LogRecord attrs (already patches taskName manually). |

**Explicit answers (logging.py):** JsonFormatter emits: `timestamp` (ISO-8601 UTC, tz-aware, from record.created), `level` (levelname), `logger` (name), `message` (getMessage), every non-standard record attribute (i.e. anything passed via `extra=`) as a top-level key, and `exception` (formatted traceback) when exc_info is set; serialized with `json.dumps(..., default=str)` (L25-39). configure_logging(level): builds a stdout StreamHandler with JsonFormatter, REPLACES all root handlers with just it, sets root level; calling twice yields exactly one handler (idempotent, tested at test_logging.py L43-48).

---

## 12. tests/unit/test_logging.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | tests/unit/test_logging.py (58 lines) |
| 4 | file purpose | Unit tests for JSON logging via pytest capsys. |
| 5 | public symbols | 5 tests: single JSON line with level/logger/message + "+00:00" UTC timestamp (L17); extra fields included (L30); level filtering (WARNING drops INFO, L37); reconfigure idempotent — no duplicate lines (L43); exception captured containing "ValueError: boom" (L51). |
| 6 | private symbols | `_last_json_line(capsys)` helper (L11). |
| 7 | internal imports | closcall.observability.logging.configure_logging. |
| 8 | external dependencies | pytest capsys. |
| 9 | global state | Tests mutate the global root logger (order-sensitive across the suite in principle). |
| 10-13 | side/subprocess/allow-list/timeouts | stdout capture only / none / N/A / none. |
| 14 | error handling | N/A. |
| 15 | environment assumptions | capsys captures the handler's stdout (handler created after capsys patch — works because configure_logging runs inside each test). |
| 16-17 | hardcoded names/addresses | "closcall.test" logger name, "incident_key"/"INC-1" / none. |
| 18 | output formats | N/A. |
| 19 | existing tests | Covers the formatter contract and idempotency well for its size. |
| 20 | missing tests | JsonFormatter tested only indirectly; no non-serializable-extra test; no multi-logger/propagation test; no test that pre-existing handlers are replaced (documented-destructive behavior unpinned). |
| 21 | reuse classification | DIRECT_REUSE. |
| 22 | exact reusable symbols | All 5 tests + `_last_json_line`. |
| 23 | symbols to rewrite | Rename logger namespace; add serialization-edge tests. |
| 24 | symbols to reject | None. |
| 25 | required adapter/interface | None. |
| 26 | proposed VerifiedNet destination | verifiednet tests/unit/test_logging.py. |
| 27 | provenance action | No license — trivial reimplementation; log commit. |
| 28 | risks | Global-logger mutation can interact with other test modules' logging expectations. |

---

## 13. scripts/emit_manifest.py (v1/v2 anchor)

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | scripts/emit_manifest.py (210 lines) |
| 4 | file purpose | Emits the IMMUTABLE §9.4 manifest for the Gate 9 v2 corpus (`gate8-full-corpus-v2`): gathers real provenance from git, docker, DB, and disk; writes `artifacts/manifests/gate9-dataset.json`; idempotently upserts the split-manifest header to `evaluation.split_manifests`. Per v3's docstring this file is "never touched" — byte-stability anchors v2 provenance. |
| 5 | public symbols | `git_revision` (L47), `image_digests` (L53), `windows_rollup` (L66), `gather` (async, L77), `run` (async, L149); constants CAMPAIGN_KEY="gate8-full-corpus-v2" (L38), CONTAINERS (L39). |
| 6 | private symbols | None. |
| 7 | internal imports | closcall.datasets.graph (build_topology_graph, graph_schema_hash), datasets.manifest (build_manifest, sha256_file), datasets.schemas (schema_hash), datasets.splits (IncidentRef, assemble_location_inductive), db.engine (make_sessionmaker), db.models (Artifact, EvalCampaign, EvalFaultInjection, EvalSplitManifest), domain.fabric (allocate, load_fabric). Also `sys.path.insert(0, REPO/"src")` hack (L23). |
| 8 | external dependencies | sqlalchemy (async), asyncio, glob, hashlib, json, subprocess; transitively PostgreSQL + docker + git CLIs. |
| 9 | global state | Module constants CAMPAIGN_KEY, CONTAINERS, REPO; sys.path mutation (L23). |
| 10 | side effects | Writes artifacts/manifests/gate9-dataset.json (L196-198, mkdir parents); DB INSERT into evaluation.split_manifests when absent (L184-192); prints summary; reads lab/fabric.yaml, uv.lock, data/raw_telemetry parquet tree, evals/reports/gate9-*.txt. |
| 11 | subprocess behavior | `git rev-parse HEAD` (L48-50) and `docker inspect --format {{.Image}} <container>` per container (L57-60); capture_output=True, text=True; NO check=True and NO timeout on either — failures yield empty strings silently. |
| 12 | command allow-list behavior | None — fixed argv lists (no shell=True), which is itself the safety property. |
| 13 | timeouts | None on subprocess or DB calls. |
| 14 | error handling | Essentially none: git failure → empty code_revision → build_manifest ValueError (accidental backstop); docker failure → container silently missing from image_digests (accepted, L61-62); `scalar_one()` raises if campaign row absent (L84); missing report files silently skipped (L133); no rollback/transaction discipline beyond single commit. |
| 15 | environment assumptions | Run from a repo checkout with .git; running/exited docker containers named clab-closcall-2s4l-leaf1, closcall-prometheus, closcall-postgres, closcall-gnmic; reachable Postgres (CLOSCALL_DB_PASSWORD); lab/fabric.yaml + uv.lock present; parquet corpus under data/raw_telemetry/campaign=gate8-full-corpus-v2/. |
| 16 | hardcoded names | CAMPAIGN_KEY, four container names, output path gate9-dataset.json, report names gate9-detection.txt/gate9-localization.txt, dataset_kind "gate9-corpus", Artifact.kind=="raw_telemetry_window", status strings "settled"/"quarantined", creation_command make-chain string (L166-169). |
| 17 | hardcoded addresses | None (DB address via engine config). |
| 18 | output formats | JSON manifest, indent=2, sort_keys, trailing newline (L198); windows_rollup format `"{hexdigest}({N} windows)"` (L74); human summary printed. |
| 19 | existing tests | NONE — no test targets this script (only manifest.py's builder is tested). |
| 20 | missing tests | Everything: gather() query shape, rollup determinism, subprocess-failure behavior, idempotent upsert. |
| 21 | reuse classification | DESIGN_REFERENCE_ONLY (the provenance-capture CHECKLIST is the harvest; the script is bound to closcall's DB schema, containers, and campaign). |
| 22 | exact reusable symbols | Patterns: git_revision, image_digests (docker-inspect digest capture), windows_rollup (sorted content-hash roll-up), the "gather everything then call build_manifest" orchestration shape. |
| 23 | symbols to rewrite | All of gather/run against VerifiedNet's run store; add check=True + timeouts to subprocess; remove sys.path hack; parametrize campaign/containers/paths. |
| 24 | symbols to reject | DB coupling (EvalCampaign/EvalFaultInjection queries), split-manifest upsert side-channel inside an "emit" script (mixed responsibility), hardcoded creation_command prose. |
| 25 | required adapter/interface | A RunStore interface supplying (master_seed, settled incidents, quarantined ids, artifact hashes) so the emitter is storage-agnostic. |
| 26 | proposed VerifiedNet destination | verifiednet/scripts/emit_manifest.py (thin CLI) + verifiednet/provenance/collect.py (the capture helpers). |
| 27 | provenance action | No license — reimplement from the captured-metadata checklist below; do not copy. Pin commit d192bf3 as design source. |
| 28 | risks | Silent-empty subprocess results (a machine without docker emits a manifest missing all image digests without warning — only code_revision has an accidental required-field backstop); no timeout can hang CI; sha256 of every parquet in the corpus can be slow; upsert keyed on (protocol, version) never updates a drifted hash. |

**Explicit answers (emit_manifest.py metadata captured):** source run id = CAMPAIGN_KEY "gate8-full-corpus-v2"; **git rev** = `git rev-parse HEAD` → code_revision; **lock hash** = sha256_file(uv.lock) → dependency_lock_hash; **image digests** = docker inspect .Image for srlinux/prometheus/postgres/gnmic containers; **seeds** = master_seed from the EvalCampaign DB row, seeds={"master": master_seed} ONLY (per-stage seeds NOT captured); config_hash = sha256(lab/fabric.yaml); topology_hash from build_topology_graph(allocate(load_fabric(...))); feature schema hash = schemas.schema_hash(); graph_schema_hash; split protocol/version/manifest_hash from assemble_location_inductive over settled DB incidents; exclusions = quarantined injection ids; content_hashes = {"corpus_windows_rollup": sorted-parquet roll-up} + sha256 of gate9-detection.txt / gate9-localization.txt; creation_command = fixed make-chain string.

---

## 14. scripts/emit_manifest_v3.py

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | scripts/emit_manifest_v3.py (224 lines) |
| 4 | file purpose | Standalone twin of emit_manifest.py for the v3 under-load corpus (`gate8-full-corpus-v3`): emits SEPARATE artifact `gate12_5-dataset-v3.json`. Deliberate copy-not-share: "two clean lineages, no shared mutable generator" (§16 — old results immutable) (docstring L4-8). |
| 5 | public symbols | Same five functions as v1 (git_revision L68, image_digests L74, windows_rollup L88, gather L99, run L168); constants CAMPAIGN_KEY="gate8-full-corpus-v3" (L45), MANIFEST_NAME (L46), BOUND_REPORTS 6-tuple (L48-55), CREATION_COMMAND (L56-59), CONTAINERS (L60, identical to v1). |
| 6 | private symbols | None. |
| 7 | internal imports | Identical set to v1. |
| 8 | external dependencies | Identical to v1; documented invocation `CLOSCALL_DB_PASSWORD=... uv run scripts/emit_manifest_v3.py` (L13). |
| 9 | global state | Same pattern; sys.path hack (L30). |
| 10 | side effects | Writes artifacts/manifests/gate12_5-dataset-v3.json; same idempotent split-manifest upsert; same reads. |
| 11 | subprocess behavior | Identical git/docker calls; still no check/timeout; docker docstring notes "works on exited containers too" (L75). |
| 12-13 | allow-list/timeouts | Fixed argv / none. |
| 14 | error handling | Same (near-none) as v1. |
| 15 | environment assumptions | Same as v1 plus v3 corpus tree data/raw_telemetry/campaign=gate8-full-corpus-v3/ and the six v3 report files. |
| 16 | hardcoded names | dataset_kind "gate12_5-corpus-v3"; BOUND_REPORTS: gate12_5-detection-v3.txt, localization-v3.txt, gate12_5-ablation.txt, gate12_5-localization-v1.txt, gate12_5-localization-v2.txt, gate12_5-localization-cv.txt; CREATION_COMMAND v3 make-chain. |
| 17 | hardcoded addresses | None. |
| 18 | output formats | Same JSON shape as v1, different filename/kind. |
| 19 | existing tests | NONE. |
| 20 | missing tests | Same as v1; also nothing prevents the two scripts drifting apart silently (they are ~95% duplicated by design). |
| 21 | reuse classification | DESIGN_REFERENCE_ONLY (same rationale as v1; ALSO a design lesson: VerifiedNet should get one parametrized emitter + frozen per-release CONFIG rather than copied scripts). |
| 22 | exact reusable symbols | The v1↔v3 immutability discipline (new benchmark version = new artifact, old emitter untouched) as a policy; BOUND_REPORTS-as-tuple pattern for content-binding study outputs. |
| 23 | symbols to rewrite | Everything, folded into one configurable emitter. |
| 24 | symbols to reject | The copy-the-whole-script versioning mechanism itself (keep the immutability GOAL, achieve it with versioned config data). |
| 25 | required adapter/interface | Same RunStore adapter as v1 + a release descriptor (campaign key, dataset kind, bound reports, creation command). |
| 26 | proposed VerifiedNet destination | verifiednet/provenance/collect.py + per-release config files. |
| 27 | provenance action | No license — reimplement; pin commit. |
| 28 | risks | Same silent-failure risks as v1; duplicated code invites divergence in exactly the provenance path where divergence is least detectable. |

**Explicit answers (v1→v3 differences):** identical capture machinery (same git rev, uv.lock hash, same four docker container digests, same seeds={"master": campaign.master_seed}, same split assembly, same DB queries and upsert). Differences ONLY: (1) CAMPAIGN_KEY v2→v3; (2) output file gate9-dataset.json → gate12_5-dataset-v3.json (MANIFEST_NAME constant); (3) dataset_kind "gate9-corpus" → "gate12_5-corpus-v3"; (4) bound reports: 2 gate9 reports (inline tuple) → 6 v3 study reports via BOUND_REPORTS constant (detection-under-load, localization ablation rule/MLP/GNN, feature ablation v1-vs-v2, per-version localization, leave-one-leaf-out CV); (5) creation_command mentions v3 under-load/fabric-wide pipeline and evaluate-sensors-v3; (6) constants hoisted (MANIFEST_NAME/BOUND_REPORTS/CREATION_COMMAND) and minor docstring/import tidying (asdict imported at top L24 vs inline L194 in v1). Neither version captures per-stage seeds, OS/kernel info, Python version, or wall-clock run time.

---

## 15. src/closcall/datasets/schemas.py (imported helper — item 13)

| # | Point | Finding |
|---|---|---|
| 1 | source repository | closcall |
| 2 | source commit | d192bf3cb86d96e6011f80d1d6915862397abab7 |
| 3 | exact source path | src/closcall/datasets/schemas.py (100 lines) |
| 4 | file purpose | Frozen data-contract schemas: pinned column tuples for raw telemetry (§9.1), causal features (§9.2), event envelope (§6), plus a leakage-guard forbidden-column set; `schema_hash()` content-pins version + all column sets. |
| 5 | public symbols | `SCHEMA_VERSION=1` (L14), `RAW_TELEMETRY_COLUMNS` (L18: event_time, received_at, ingested_at, topology_hash, node, interface, direction, metric, value, unit, is_counter, quality_flags, source_sequence, schema_version), `CAUSAL_FEATURE_COLUMNS` (L37: example_id, split, incident_runtime_id, window_start/end, as_of_at, node, interface, util_ratio, error_rate, discard_rate, sample_age_s, missingness_mask, feature_schema_hash, preprocessor_hash), `EVENT_ENVELOPE_FIELDS` (L56: schema_version, event_id, event_type, event_time, observed_at, source, trace_id, payload), `FORBIDDEN_FEATURE_COLUMNS` (L68: t_clear, t_settled, incident_duration, ground_truth, label, scenario_key, fault_class, split_answer), `schema_hash` (L82). |
| 6 | private symbols | None. |
| 7 | internal imports | None. |
| 8 | external dependencies | stdlib (hashlib, json). |
| 9 | global state | Frozen constants only. |
| 10-13 | side effects/subprocess/allow-list/timeouts | None / none / FORBIDDEN_FEATURE_COLUMNS is a deny-list enforced elsewhere / none. |
| 14 | error handling | None needed. |
| 15 | environment assumptions | None. |
| 16 | hardcoded names | The column vocabularies themselves (that is the point — frozen contract). |
| 17 | hardcoded addresses | None. |
| 18 | output formats | schema_hash = sha256 over sorted-keys JSON of {version + three column tuples} (L82-90); FORBIDDEN set deliberately NOT in the hash. |
| 19 | existing tests | tests/unit/test_dataset_schemas.py exists (not in inspection scope; presence noted). |
| 20 | missing tests | (out of scope; hash-pin test presumed in test_dataset_schemas.py). |
| 21 | reuse classification | WRAP_WITH_ADAPTER / DESIGN_REFERENCE_ONLY — the freeze-and-hash MECHANISM is reusable; the column vocabularies are closcall's. |
| 22 | exact reusable symbols | `schema_hash` pattern, FORBIDDEN_FEATURE_COLUMNS leakage-guard idea, SCHEMA_VERSION bump discipline. |
| 23 | symbols to rewrite | All column tuples for VerifiedNet's own contracts. |
| 24 | symbols to reject | None (vocabularies simply don't transfer). |
| 25 | required adapter/interface | None — pure constants module pattern. |
| 26 | proposed VerifiedNet destination | verifiednet/datasets/schemas.py (new vocabularies, same mechanism). |
| 27 | provenance action | No license — mechanism reimplementation is trivial; log commit. |
| 28 | risks | Forbidden set not being part of the hash means loosening the leakage guard does NOT change schema_hash — consider including it in VerifiedNet. |

Supporting note (item 13, read-at-signature level): `datasets/graph.py` provides `graph_schema_hash()` (GRAPH_SCHEMA_VERSION=1) and `build_topology_graph(topo: ResolvedTopology) -> TypedGraph` with a `topology_hash` field (graph.py L28, L86, L119); `datasets/splits.py` provides `IncidentRef` (L41), `SplitManifest{protocol, version, ..., manifest_hash}` (L67-74) and `assemble_location_inductive` (L89). These are consumed by both emit scripts; full inspection deferred to their own harvest ticket.

---

## Symbol-level harvest table — repo closcall

| symbol | file | harvest verb | required modifications | Wave A role |
|---|---|---|---|---|
| ResolvedTopology / ResolvedNode / ResolvedLink / ResolvedEndpoint | src/closcall/domain/fabric.py | copy symbol with modifications | Make role open-ended (not Literal spine/leaf/host); host_networks optional; keep extra=forbid + deterministic model_dump_json | Canonical resolved-topology contract consumed by renderer/IPAM/tests |
| FabricSpec (+ _Topology/_Pools/_Interfaces/_Nodes/_SwitchSpec/_HostSpec) | src/closcall/domain/fabric.py | reimplement behavior from specification | New grammar: role-agnostic node list + EXPLICIT link list; optional pools/interfaces fields; drop 2-spine/host-per-leaf structure | Source-of-truth spec for arbitrary (incl. 2-router P2P) topologies |
| allocate | src/closcall/domain/fabric.py | copy symbol with modifications | Replace leaves×spines loop + `uplinks` dict with iteration over explicit ordered link list (k = list index); keep k-th-/31 even/odd math, ordinal loopback/mgmt; parametrize gateway/.10 offsets | Deterministic IPAM for Wave A topologies |
| validate_fabric (check inventory) | src/closcall/domain/validate.py | reimplement behavior from specification | Typed error codes; re-derive pool-capacity formula from link count; add pool-overlap checks | Pre-deploy static validation gate |
| Ledger + LedgerRecord + Phase + UNRECONCILED + now_record | src/closcall/chaos/ledger.py | copy symbol nearly unchanged | Add tolerant/versioned JSONL decode (skip-or-quarantine torn last line); document single-writer; keep fsync WAL semantics | Write-ahead impairment ledger + startup reconciliation |
| Verdict, Predicate, Evidence, Snapshot, Claim, verify, committable | src/closcall/evidence/claims.py | copy symbol nearly unchanged | Decide + enforce untrusted-evidence policy inside verify; index by_id; pin interval inclusivity in docstring/tests | Deterministic claim verifier — core of the evidence flow |
| Budget + BudgetExhausted + ToolContext | src/closcall/evidence/tools.py | copy symbol nearly unchanged | Optionally pre-check budget before source fetch; otherwise as-is | Per-diagnosis call/row bounding for all evidence tools |
| _emit envelope + Record | src/closcall/evidence/tools.py | copy symbol with modifications | Fix evidence_id collisions (add seq/uuid); keep as-of filter, limit cap, trust tagging, trace | Single enforcement point for scope/causality/trust |
| get_interface_state / get_bgp_state / get_log_events (+ EvidenceSource protocol, trimmed) | src/closcall/evidence/tools.py | copy symbol with modifications | Trim protocol to Wave A methods; fix get_metric_window's `as_of - limit` rows-as-seconds conflation before enabling it | First evidence flow tool set |
| METRIC_TEMPLATES allow-list pattern | src/closcall/evidence/tools.py | copy symbol with modifications | New template vocabulary; keep reject-by-default | Free-form-query prevention |
| DatasetManifest + build_manifest + sha256_file | src/closcall/datasets/manifest.py | copy symbol nearly unchanged | Consider adding image_digests/seeds to REQUIRED_NONEMPTY; streaming sha256 | Reproducibility/provenance anchor for every dataset |
| JsonFormatter + configure_logging | src/closcall/observability/logging.py | copy symbol nearly unchanged | Optional stream param; consider non-destructive handler install | Structured logging for all Wave A services |
| git_revision / image_digests / windows_rollup / gather-then-build orchestration | scripts/emit_manifest.py + emit_manifest_v3.py | use only as architectural reference | Reimplement with check=True + timeouts, RunStore adapter, per-release config instead of script copies; fail loudly on missing digests | Manifest-emission pipeline design |
| schema_hash mechanism + FORBIDDEN_FEATURE_COLUMNS guard | src/closcall/datasets/schemas.py | reimplement behavior from specification | New column vocabularies; include forbidden set in the hash | Frozen-contract discipline for VerifiedNet schemas |
| test_all_p2p_addresses_unique_and_paired, test_loopbacks_unique, test_allocation_is_deterministic | tests/unit/test_fabric_ipam.py | copy symbol with modifications | Inline fixtures for new grammar; drop 2s4l constants | IPAM invariant suite |
| test_canon_worked_example_* + ASN/subnet constants | tests/unit/test_fabric_ipam.py | retain only as test fixture | None (stays in closcall) | Historical worked example |
| test_ledger.py suite (4 tests) | tests/unit/test_ledger.py | copy symbol with modifications | Add corrupted-line, terminal-phase (FAILED/QUARANTINED/SETTLED), empty-file tests | Ledger regression suite |
| test_claims.py suite (10 tests) | tests/unit/test_claims.py | copy symbol with modifications | Add Predicate.ANY, polarity=False-supported, boundary, unknown-operator, untrusted-evidence cases | Verifier adversarial suite |
| test_tools.py suite (8 tests incl. no-ground-truth structural guard) | tests/unit/test_tools.py | copy symbol with modifications | Cover remaining tools; pin trace format | Evidence-tool envelope suite |
| test_manifest.py suite (6 tests) | tests/unit/test_manifest.py | copy symbol with modifications | Parametrize drift test over all fields | Manifest contract suite |
| test_logging.py suite (5 tests) | tests/unit/test_logging.py | copy symbol nearly unchanged | Rename namespaces; add serialization-edge cases | Logging contract suite |
| test_fabric_validate.py mutation suite | tests/unit/test_fabric_validate.py | reimplement behavior from specification | New grammar + typed error codes instead of substrings | Validator negative suite |
| Copy-the-emitter versioning mechanism (v1 vs v3 twin scripts) | scripts/emit_manifest_v3.py | reject | Replace with one emitter + versioned release config (keep the immutability goal) | — |
| Host .1/.10 hardcoded offsets; `uplinks` 2-spine dict | src/closcall/domain/fabric.py | reject | Become spec fields / removed by link-list rewrite | — |


# Appendix C — STA + EVL per-file analyses (verbatim inspection fragment)

# Gate 2 Harvest Fragment: sonic-troubleshooting-agent + evpn-vxlan-frr-lab (frag_sta_evl)

Inspection date: 2026-07-11. Everything below is from local reads of the two pinned commits; no network access used.

Scope note on task item 2 (utilities imported by the fault): `faults/bgp_asn_mismatch.py` imports ONLY stdlib (`argparse`, `json`, `subprocess`, `sys`, `time`, lines 43-47). It imports no project-internal utility module, so there is no separate utility file to inspect. It also does NOT call any function in `collectors/sonic_state.py` — it carries its own private `_docker_exec` and BGP-summary parser (`_read_peer_raw`). `collect_bgp_summary` is inspected below because Wave A needs its BGP evidence shape, not because the fault calls it.

---

## File 1: sonic-troubleshooting-agent/faults/bgp_asn_mismatch.py

### Special-attention answers (complete fault lifecycle)

**Preconditions.**
- `_check_container_running()` (l.95-106): `docker ps --filter name=sonic-vs-troubleshoot --format {{.Names}}` with a direct `subprocess.run` (timeout=5, no `check=True`); raises `FaultInjectionError` if the name is absent from stdout, with pointer to `./scripts/bringup.sh`. Called at the top of `inject()`, `restore()`, and `status()`.
- `inject()` additionally requires the categorized state to be exactly `"established"` (peer present, state=="Established", remoteAs==65001) before mutating (l.231-236); otherwise raises with pointer to `scripts/configure_bgp.sh up`. This is the fail-loud pattern: the fault never sets up its own fixture.
- `_peer_reachable()` (l.109-120): single ICMP echo inside the container, `ping -c 1 -W 1 10.10.10.2` via `_docker_exec`; returns bool (True on exit 0, False on any `FaultInjectionError`). Used only by `restore()` (l.267) to refuse to reconfigure SUT-side BGP when the peer fixture is down.

**Inject (`_apply_inject`, l.196-203).** One `docker exec` of a single multi `-c` vtysh invocation:
```
vtysh -c "configure terminal" -c "router bgp 65000" -c "neighbor 10.10.10.2 remote-as 65002"
```
Then `inject()` polls `wait_for_state(lambda s: s == "mismatched", timeout=30.0)` and re-reads raw state for the "after" print. Raises if final category != "mismatched".

**State reads.**
- `_read_peer_raw()` (l.123-144): `vtysh -c "show bgp summary json"`; returns `(state, remoteAs)` tuple from `data["ipv4Unicast"]["peers"][PEER_IP]` — fields `state` (str) and `remoteAs` (int as FRR reports it). Returns `(None, None)` on empty output, `{}`, JSON decode error, or missing peer key. IPv4-unicast-only (unlike collect_bgp_summary which also reads ipv6Unicast).
- `read_peer_state()` (l.147-172): categorizes to `"established"` (Established + remoteAs==65001), `"mismatched"` (Idle + remoteAs==65002), `"removed"` (peer absent), else `"other:<state>:asn=<remoteAs>"` (catches FSM transition states Connect/OpenSent/Active).

**Polling (`wait_for_state`, l.182-193).** Predicate signature: `predicate(category_str) -> bool`. `deadline = time.monotonic() + timeout`; reads state once before the loop, then `while not predicate(last) and time.monotonic() < deadline: sleep(interval); re-read`. `POLL_INTERVAL_SECONDS = 0.5`. On timeout it does NOT raise — it returns the last observed category string; the caller (`inject`/`restore`) compares and raises `FaultInjectionError` naming the timeout. Timeouts: `INJECT_TIMEOUT_SECONDS = 30.0`, `RESTORE_TIMEOUT_SECONDS = 60.0`. Per-subprocess `COMMAND_TIMEOUT_SECONDS = 10` inside `_docker_exec`.

**Restore (`_apply_restore`, l.206-222).** Two `docker exec` calls:
```
vtysh -c "configure terminal" -c "router bgp 65000" -c "neighbor 10.10.10.2 remote-as 65001"
vtysh -c "clear bgp 10.10.10.2"
```
The `clear bgp <peer>` is documented as load-bearing (l.208-214): revert alone reconverged in ~15s under deep backoff (5 accumulated NOTIFICATIONs); revert + clear gives ~2s. Then polls for "established" with the 60s timeout.

**Constants (l.49-57).** `CONTAINER="sonic-vs-troubleshoot"`, `SUT_ASN="65000"`, `PEER_IP="10.10.10.2"`, `CORRECT_PEER_ASN="65001"`, `WRONG_PEER_ASN="65002"`, `INJECT_TIMEOUT_SECONDS=30.0`, `RESTORE_TIMEOUT_SECONDS=60.0`, `POLL_INTERVAL_SECONDS=0.5`, `COMMAND_TIMEOUT_SECONDS=10`.

**Exit codes (l.296-323).** 0 on success; 1 on any `FaultInjectionError` (printed as `error: ...` to stderr); argparse itself exits 2 on bad usage. No other codes.

**Idempotency.**
- `inject()` twice: NOT idempotent — second run sees category "mismatched" (not "established"), so the precondition check at l.231 raises and exits 1 without mutating. Safe-fail, not no-op.
- `restore()` twice: idempotent by explicit no-op guard — second run sees "established" and returns early with a message (l.263-266) before touching vtysh. Exit 0.

**vtysh idioms harvested (translate directly to plain FRR; drop the `docker exec <container>` prefix or keep it for containerlab):**
- Mutate: `vtysh -c "configure terminal" -c "router bgp <ASN>" -c "neighbor <IP> remote-as <ASN>"`
- Force reconvergence: `vtysh -c "clear bgp <peer-ip>"`
- Read: `vtysh -c "show bgp summary json"` → `ipv4Unicast.peers.<ip>.{state,remoteAs}`

### 28-point analysis

| # | Point | Finding |
|---|---|---|
| 1 | source repository | sonic-troubleshooting-agent |
| 2 | source commit | eb4c8185ec6d5fab77d526f07aa9f9766d8034bb |
| 3 | exact source path | faults/bgp_asn_mismatch.py |
| 4 | file purpose | CLI fault script: inject/restore/status for a BGP remote-as mismatch on neighbor 10.10.10.2 inside a SONiC-VS container, via vtysh; verifies effect by polling categorized peer state |
| 5 | public symbols | `FaultInjectionError`, `read_peer_state()`, `wait_for_state(predicate, timeout, interval)`, `inject()`, `restore()`, `status()`, `main()` |
| 6 | private symbols relevant to Wave A | `_docker_exec` (l.64), `_check_container_running` (l.95), `_peer_reachable` (l.109), `_read_peer_raw` (l.123), `_format_peer_line` (l.175), `_apply_inject` (l.196), `_apply_restore` (l.206) |
| 7 | internal imports | None — deliberately self-contained; does not import collectors or other faults (duplicates their `_docker_exec` pattern by copy, per docstrings l.67-69) |
| 8 | external dependencies | stdlib only: argparse, json, subprocess, sys, time. Runtime deps: `docker` CLI on PATH; FRR vtysh + ping inside the container |
| 9 | global state | Module-level constants only (l.49-57); no mutable globals, no caching |
| 10 | side effects | Mutates running FRR config in the container (remote-as change; NOT persisted to startup-config — no `write memory`); issues `clear bgp` (session reset); prints progress to stdout, errors to stderr |
| 11 | subprocess behavior | All exec via argv lists (`["docker","exec",CONTAINER,*args]`), `shell=False`, `capture_output=True, text=True, check=True, timeout=10` (l.72-78); `_check_container_running` uses its own run with timeout=5 and no check |
| 12 | command allow-list behavior | None — no allow-list; commands are hardcoded literals assembled from module constants, so surface is fixed but there is no enforcement mechanism |
| 13 | timeouts | Per-command 10s (`COMMAND_TIMEOUT_SECONDS`); container check 5s; inject convergence 30.0s; restore convergence 60.0s; poll interval 0.5s |
| 14 | error handling | `TimeoutExpired`/`CalledProcessError`/`FileNotFoundError` all wrapped into `FaultInjectionError` with cmd + stderr context (l.79-91); `main()` catches only `FaultInjectionError` → stderr + exit 1; `wait_for_state` never raises (returns last state; caller decides); JSON decode errors degrade to `(None,None)` → "removed" |
| 15 | environment assumptions | Docker Desktop host; container `sonic-vs-troubleshoot` running with FRR integrated-config vtysh; BGP lab pre-built by scripts/configure_bgp.sh (SUT AS 65000, peer 10.10.10.2 AS 65001, Established baseline); ping binary in container |
| 16 | hardcoded names | `CONTAINER="sonic-vs-troubleshoot"` (l.49); setup script names in error strings (`./scripts/bringup.sh`, `scripts/configure_bgp.sh up`) |
| 17 | hardcoded addresses | `PEER_IP="10.10.10.2"` (l.51); ASNs 65000/65001/65002 (l.50-53) |
| 18 | output formats | Human text lines to stdout (`before:`/`injecting:`/`after:`/`inject ok:`); `status` prints one category token (`established|mismatched|removed|other:<state>:asn=<n>`); errors `error: <msg>` to stderr; exit code 0/1 |
| 19 | existing tests | None (no test files anywhere in the repo); validation is behavioral, encoded in phase2 spike-findings docs referenced at l.9-17 |
| 20 | missing tests | Unit tests for `read_peer_state` categorization (all four branches), `_read_peer_raw` malformed-JSON paths, `wait_for_state` timeout semantics (returns-not-raises), inject-twice / restore-twice idempotency, exit codes |
| 21 | reuse classification | REFACTOR_AND_REUSE |
| 22 | exact reusable symbols | `wait_for_state` (generic deadline-poll, near-verbatim), `read_peer_state`/`_read_peer_raw` (parameterize peer/ASNs and exec transport), `_apply_inject`/`_apply_restore` (the vtysh command sequences, retarget exec), `FaultInjectionError`, the inject/restore lifecycle skeleton incl. the no-op restore guard and fail-loud precondition |
| 23 | symbols to rewrite | `_docker_exec` → VerifiedNet's device-exec abstraction (containerlab/SSH/netns); `_check_container_running` → generic node-alive precondition; constants → per-scenario topology config; `main()` CLI → VerifiedNet fault-runner interface |
| 24 | symbols to reject | None outright; `_format_peer_line` is trivial cosmetic (reimplement freely) |
| 25 | required adapter/interface | An exec-transport interface (`run_on_node(node, argv, timeout) -> str`) replacing `_docker_exec`; a fault-scenario parameter object (node, sut_asn, peer_ip, correct/wrong ASN, timeouts) replacing module constants |
| 26 | proposed VerifiedNet destination (candidate) | verifiednet/faults/bgp_asn_mismatch.py (fault logic + vtysh idioms) with `wait_for_state` lifted into verifiednet/lib/polling.py |
| 27 | provenance action | Record repo+commit+path in HARVEST provenance ledger; retain original docstring pointers to phase2 spike findings as design citations (the ~2s-vs-~15s clear-bgp evidence) |
| 28 | risks | Fault state judged solely from `show bgp summary json` FSM (no Bad Peer AS / 0202 subcode enrichment — explicitly out of scope per l.27-35, so diagnosis-evidence quality is limited); "Idle+wrong-AS" categorization can transiently read `other:Active/Connect` (poll absorbs this but a slow FRR could time out at 30s); running config mutation is not persisted, so a container restart silently reverts the fault; hardcoded single-peer assumption |

---

## File 2: sonic-troubleshooting-agent/collectors/sonic_state.py

Does the fault call any of these functions? **No.** `bgp_asn_mismatch.py` calls nothing here (no import). The four collectors are called only by `main.py:take_snapshot`. All four functions were read fully; `collect_bgp_summary` (l.184-239) is the Wave A evidence pattern.

**collect_bgp_summary evidence shape (l.184-239):** runs `vtysh -c "show bgp summary json"` via `_docker_exec`. Never raises — every failure path returns a dict. Returns `{"bgp_instance_present": bool, "neighbors": [{"neighbor": addr, "asn": remoteAs, "state": state-or-"unknown"}], "source": "vtysh show bgp summary json"[, "error": str]}`. Iterates BOTH `ipv4Unicast` and `ipv6Unicast` `.peers` (l.220-228). `bgp_instance_present` is True if any neighbor found OR either AF block is a dict containing an `"as"` key (l.230-233) — so a configured-but-peerless BGP instance still reads present. Empty output, non-JSON text ("No BGP process is configured"), and exec errors all yield `bgp_instance_present=False, neighbors=[]` (error key only on exec failure, l.194-217).

| # | Point | Finding |
|---|---|---|
| 1 | source repository | sonic-troubleshooting-agent |
| 2 | source commit | eb4c8185ec6d5fab77d526f07aa9f9766d8034bb |
| 3 | exact source path | collectors/sonic_state.py |
| 4 | file purpose | Pure-Python (no LLM) evidence collectors reading SONiC state: Redis CONFIG_DB/APP_DB/COUNTERS_DB, vtysh BGP summary, syslog tail; each returns a structured dict and converts failures to an `"error"` key instead of raising |
| 5 | public symbols | `CollectorError`, `collect_interface_state(interface)`, `collect_interface_counters(interface)`, `collect_bgp_summary()`, `collect_recent_logs(lines=50)` |
| 6 | private symbols relevant to Wave A | `_docker_exec` (l.49, same wrapper as the fault's), `_parse_redis_hgetall` (l.80, SONiC-Redis-specific), `_COUNTER_FIELDS` SAI map (l.33-40) |
| 7 | internal imports | None (json, subprocess only) |
| 8 | external dependencies | stdlib; runtime: docker CLI, redis-cli + vtysh + sh/tail inside container |
| 9 | global state | Constants: `CONTAINER`, DB numbers (CONFIG_DB=4, APP_DB=0, COUNTERS_DB=2), `COMMAND_TIMEOUT_SECONDS=10`, `_COUNTER_FIELDS`; no mutable state |
| 10 | side effects | Read-only against the device; `__main__` smoke block prints JSON. One nuance: `collect_recent_logs` runs `sh -c` inside the container (l.261-268) |
| 11 | subprocess behavior | argv-list `docker exec`, `shell=False` on host; `check=True`, `capture_output`, `text`, timeout=10. `collect_recent_logs` composes a container-side shell string with sentinel `__SYSLOG_MISSING__`; `lines` is int-coerced and clamped 0..500 before f-string interpolation (l.256-260) — deliberate injection defense |
| 12 | command allow-list behavior | None; fixed literal commands |
| 13 | timeouts | 10s per docker exec, uniform |
| 14 | error handling | `_docker_exec` raises `CollectorError` (timeout / non-zero / docker missing); every `collect_*` catches it and returns `{"error": ...}` — "failed collector is itself evidence" pattern (l.3-5). JSON decode failure in BGP summary treated as no-BGP-process, NOT as error (l.211-217) |
| 15 | environment assumptions | SONiC-VS container with Redis DB layout (PORT\| in CONFIG_DB, PORT_TABLE: in APP_DB, COUNTERS_PORT_NAME_MAP/COUNTERS:<oid> in COUNTERS_DB), integrated vtysh, /var/log/syslog; admin_status-absent-means-up SONiC convention (l.97, 117) |
| 16 | hardcoded names | `CONTAINER="sonic-vs-troubleshoot"`; Redis key patterns; SAI stat names; `Ethernet4` in the `__main__` smoke block |
| 17 | hardcoded addresses | None |
| 18 | output formats | Per-collector dicts with stable keys: interface_state{interface,admin_status,oper_status,source[,error]}; interface_counters{interface,rx/tx packets/errors/discards ints,source[,error]}; bgp_summary{bgp_instance_present,neighbors[{neighbor,asn,state}],source[,error]}; recent_logs{log_lines[],source[,error]} |
| 19 | existing tests | None; only the `__main__` manual smoke run (l.287-298) |
| 20 | missing tests | `_parse_redis_hgetall` odd-line input; BGP summary parse of empty/{} /plain-text/ipv6-only payloads; `bgp_instance_present` heuristic branches; counters missing-oid vs empty-hash |
| 21 | reuse classification | Split: `collect_bgp_summary` REFACTOR_AND_REUSE (evidence shape + never-raise contract); `_docker_exec` REFACTOR_AND_REUSE (merge with fault's copy behind the exec-transport adapter); `collect_interface_state`/`collect_interface_counters`/`_parse_redis_hgetall` RETAIN_ONLY_IN_ORIGINAL_REPO (SONiC-Redis-specific; plain FRR nodes have no CONFIG_DB/COUNTERS_DB); `collect_recent_logs` DESIGN_REFERENCE_ONLY (sentinel-vs-real-failure discrimination idea) |
| 22 | exact reusable symbols | `collect_bgp_summary` (retarget exec, keep dict shape incl. dual-AF iteration and `bgp_instance_present` heuristic), `CollectorError` + error-key convention, `_docker_exec` |
| 23 | symbols to rewrite | `collect_recent_logs` for non-SONiC log sources; `_docker_exec` transport |
| 24 | symbols to reject | `_parse_redis_hgetall`, `_COUNTER_FIELDS`, Redis DB constants — meaningless off SONiC (retain in original repo) |
| 25 | required adapter/interface | Same exec-transport interface as File 1; a collector registry interface (`name -> callable -> dict-with-optional-error`) so Gate 4 snapshots stay uniform |
| 26 | proposed VerifiedNet destination (candidate) | verifiednet/collectors/frr_bgp.py (collect_bgp_summary + error-key contract); shared exec transport in verifiednet/lib/exec.py |
| 27 | provenance action | Ledger entry repo+commit+path; note deliberate divergence: VerifiedNet drops Redis collectors (document as not-harvested with reason) |
| 28 | risks | `neighbors[].state` "unknown" default can mask malformed peer blocks; `bgp_instance_present` heuristic depends on FRR JSON emitting `"as"` per-AF (FRR version drift risk); silent-JSON-decode-as-absent could hide a genuinely corrupt vtysh output; 10s exec timeout may be tight on loaded CI hosts |

---

## File 3: sonic-troubleshooting-agent/main.py (scoped: scenario lifecycle + snapshot behavior)

Scoped per instructions to: `Scenario` dataclass (l.238-258), `SCENARIOS` registry + dispatch (l.261-310, 414-416), `take_snapshot` (l.135-142), `print_snapshot` (l.171-175) with `_one_line_summary` (l.145-168), and the run flow for `bgp_asn_mismatch`. Specialist/diagnosis agent internals (agents/*, blackboard internals) not inspected beyond their call sites.

**Exact lifecycle order for `--scenario bgp_asn_mismatch` (requires_bgp_lab=True):**
1. `parse_args` → registry lookup `SCENARIOS["bgp_asn_mismatch"]` (l.416). `--dry-run` short-circuits to `run_dry_run` (no mutation).
2. `is_container_running(CONTAINER)` gate → exit 2 if down (l.422-427).
3. `_run_configure_bgp("up")` — lab fixture up, 180s timeout; failure → exit 7 (l.434-441).
4. `before = take_snapshot(scenario.interface)`; `print_snapshot(before, "BEFORE")` (l.443-444).
5. `_call_with_stdout_to_stderr(scenario.inject)` — the fault's `inject()` with its stdout redirected to stderr (l.446-448). `injected = True`.
6. `time.sleep(post_inject_delay_seconds)` = 1.0s for this scenario (l.450, 305).
7. `after = take_snapshot(...)`; `print_snapshot(after, "AFTER")` (l.452-453).
8. Evidence filter: None for bgp_asn_mismatch, so `evidence_for_agent = after` unfiltered (l.455-460).
9. `Blackboard(user_complaint)`; `add_evidence(name, data)` per collector (l.462-464).
10. Fan-out: 4 specialists (triage, interface, bgp, logs) concurrently via `ThreadPoolExecutor(max_workers=4)`; individual failures non-fatal (l.473-490). [Non-deterministic / LLM — outside Gate 4 mirror.]
11. Fan-in: `produce_diagnosis(bb)` → diagnosis JSON to stdout; `DiagnosisError` → exit 3 (l.492-499). [LLM.]
12. `finally`: if injected and not `--keep-fault`: `scenario.restore()` (stdout→stderr); restore failure = warn + exit 4 (l.509-518). With `--keep-fault`: print manual cleanup commands instead (l.519-524).
13. `finally` continued: if lab up and not `--keep-fault`: `_run_configure_bgp("down")`; failure warn + exit 4 (l.526-533).

So: **fixture-up → BEFORE snapshot → inject → sleep(1.0) → AFTER snapshot → [filter] → blackboard → agents fan-out → diagnosis fan-in → restore → fixture-down.** The fault's own `wait_for_state` runs *inside* inject/restore, so convergence waiting is nested in steps 5 and 12, not a separate runner step. Deterministic parts for VerifiedNet's Gate 4 loop: steps 1-9, 12-13 (everything except the two agent steps). Note: no post-restore snapshot is taken — restore verification relies on the fault script's internal poll, a gap VerifiedNet should close.

**take_snapshot (l.135-142):** sequential (not parallel) calls to all four collectors; per-port collectors get `scenario.interface`; `recent_logs` fixed at 20 lines. Returns `{name: dict}`.

**print_snapshot (l.171-175):** stderr section header `=== LABEL ===` plus one line per collector via `_one_line_summary` (error short-circuits first; bgp_summary summarizes as `bgp_instance_present=<bool> neighbors=<count>`, l.161-165). Raw snapshots go to stderr summaries; only stdout carries the diagnosis JSON (stdout/stderr contract, l.30-35).

**Scenario dataclass (l.238-258):** frozen; fields `name, inject, restore, user_complaint, interface, requires_bgp_lab, evidence_filter, post_inject_delay_seconds, manual_restore_command`. `evidence_filter: Optional[Callable[[dict, str], dict]]` applied to the AFTER snapshot only. Registry is a plain dict; argparse `choices=sorted(SCENARIOS.keys())`, `--scenario` required, no default.

| # | Point | Finding |
|---|---|---|
| 1 | source repository | sonic-troubleshooting-agent |
| 2 | source commit | eb4c8185ec6d5fab77d526f07aa9f9766d8034bb |
| 3 | exact source path | main.py (inspected scope: lifecycle/snapshot; agents internals excluded per task) |
| 4 | file purpose | End-to-end scenario runner: registry-driven dispatch of fault scenarios; fixture management, before/after snapshots, evidence filtering, agent fan-out/fan-in, guaranteed cleanup |
| 5 | public symbols | `Scenario` dataclass, `SCENARIOS` registry, `is_container_running`, `take_snapshot`, `print_snapshot`, `run_dry_run`, `parse_args`, `main` |
| 6 | private symbols relevant to Wave A | `_eprint` (l.83), `_call_with_stdout_to_stderr` (l.88, contextlib.redirect_stdout capture), `_run_configure_bgp` (l.102), `_one_line_summary` (l.145), `_filter_logs_for_interface`/`_admin_down_evidence_filter` (l.178-235, pattern only) |
| 7 | internal imports | agents.{bgp_specialist,diagnosis,interface_specialist,logs_specialist,triage}, blackboard.blackboard.Blackboard, collectors.sonic_state (4 collect_*), faults.{bgp_asn_mismatch,bgp_neighbor_removal,interface_admin_down}; sys.path hack l.60-61 |
| 8 | external dependencies | stdlib (argparse, contextlib, io, json, subprocess, sys, time, concurrent.futures, dataclasses, pathlib, typing); runtime: docker CLI, scripts/configure_bgp.sh, Ollama (qwen2.5:7b-instruct) for agents |
| 9 | global state | `SCENARIOS` dict (immutable in practice), path constants, `CONFIGURE_BGP_TIMEOUT_SECONDS=180`; sys.path mutation at import (l.61) |
| 10 | side effects | Runs fixture script (container/network setup+teardown), injects/restores faults, calls local LLM, prints JSON to stdout and logs to stderr |
| 11 | subprocess behavior | argv lists, `shell=False`, capture_output/text; configure_bgp 180s timeout, docker ps 5s; `check=False` with manual returncode handling in `_run_configure_bgp` |
| 12 | command allow-list behavior | None; registry constrains scenario choice (argparse `choices`) but not commands |
| 13 | timeouts | configure_bgp.sh 180s; docker ps 5s; post-inject delay per scenario (1.0s for all three); no overall run timeout; agent calls unbounded here |
| 14 | error handling | Distinct exit codes: 0 ok, 2 container down, 7 fixture-up failed, 3 diagnosis failed, 4 restore/fixture-down failed (warn-level, only if exit still 0), 1 other unexpected; try/finally guarantees restore+teardown ordering (restore before lab down, comment l.507-508); `--keep-fault` deliberate skip with printed manual cleanup |
| 15 | environment assumptions | Docker Desktop; sonic-vs-troubleshoot running; Ollama serving qwen2.5:7b-instruct; repo-root cwd-independent via `Path(__file__)` |
| 16 | hardcoded names | `CONTAINER`, `DEFAULT_INTERFACE="Ethernet4"`, script path scripts/configure_bgp.sh, model name in help text |
| 17 | hardcoded addresses | None directly (addresses live in fault modules); user_complaint strings mention Ethernet4/BGP |
| 18 | output formats | stdout: diagnosis dict as pretty JSON only; stderr: `=== SECTION ===` headers + indented one-line summaries; dry-run: numbered plan to stderr |
| 19 | existing tests | None; `--dry-run` is the only built-in verification aid |
| 20 | missing tests | Exit-code matrix; finally-block ordering under mid-run exceptions; `--keep-fault` paths; evidence_filter application; `_call_with_stdout_to_stderr` capture |
| 21 | reuse classification | DESIGN_REFERENCE_ONLY (architecture: registry + lifecycle + exit-code discipline + stdout/stderr contract); the small pure helpers `take_snapshot`/`print_snapshot`/`Scenario` are copy-with-modifications candidates within that |
| 22 | exact reusable symbols | `Scenario` dataclass shape (frozen, evidence_filter/post_inject_delay/manual_restore_command fields), `take_snapshot`, `print_snapshot`+`_one_line_summary`, the try/finally cleanup ordering, exit-code scheme, `_call_with_stdout_to_stderr` |
| 23 | symbols to rewrite | `main()` for VerifiedNet Gate 4 loop (add post-restore snapshot; make agent steps pluggable/optional); `_run_configure_bgp` → generic fixture hook on Scenario; collector set → registry-driven instead of hardcoded four |
| 24 | symbols to reject | `_filter_logs_for_interface`'s SONiC-VS oper-error suppression specifics (keep the *pattern* of per-scenario evidence filters, reject the SONiC noise list); agents wiring |
| 25 | required adapter/interface | Scenario registry keyed by name with fixture_up/fixture_down callables; collector-set injection; agent-stage interface so deterministic Gate 4 runs can stub LLM steps |
| 26 | proposed VerifiedNet destination (candidate) | verifiednet/runner/gate4_loop.py (new implementation, this file as documented reference); Scenario dataclass → verifiednet/runner/scenario.py |
| 27 | provenance action | Ledger entry as design reference (repo+commit+path); cite lifecycle order and exit-code table in Gate 4 design doc |
| 28 | risks | No post-restore snapshot (restore verified only inside fault script) — carry-forward gap; snapshot is sequential so BEFORE/AFTER are not point-in-time atomic (collector skew up to ~4×10s worst case); fixed 1.0s post-inject delay is a magic number partially redundant with the fault's own convergence poll; sys.path insertion pattern should not be copied |

---

## File 4: evpn-vxlan-frr-lab/validate/checks.py (scoped: generic polling/reachability patterns only)

Whole file read (139 lines). Evaluated for harvest: `_run`, `container_running`, `bgp_underlay_established`, `loopback_reachable`, `host_can_ping`. EVPN-specific checks `evpn_imet_from_peer` (l.39), `vxlan_iface_exists` (l.76), `bridge_fdb_has_her_to` (l.85) are **DEFERRED to Wave B / EVPN backend** per Gate 1 scoping — not analyzed for harvest here beyond noting they follow the same `(ok, detail)` contract.

**Special-attention answers:**
- `_run` (l.9-11): `subprocess.run(cmd, shell=True, capture_output=True, text=True)` — **shell=True with f-string-interpolated commands, and NO timeout.** Returns `(returncode, stdout, stderr)`. Both properties disqualify it from VerifiedNet's truth path as-is: a hung `docker exec` blocks the validator forever, and shell interpolation of names/IPs is an injection hazard (benign here with static configs, unacceptable in harvested code).
- `bgp_underlay_established` (l.21-36): runs `docker exec <container> vtysh -c 'show ip bgp summary json'` (note `show ip bgp` vs the SONiC repo's `show bgp` — same JSON shape); parses `data["ipv4Unicast"]["peers"][peer_ip]`; **the Established indicator is the `state` field: `state == "Established"`** (l.35-36); returns `(state=="Established", f"{peer_ip}={state}")`. Distinguishes rc!=0 ("vtysh failed"), bad JSON, peer-not-configured, and wrong-state — good failure taxonomy.
- `loopback_reachable` (l.133-139): **deterministic single-shot** — `ping -c 1 -W 2 [-I <source>] <target>`; pass/fail is exactly ICMP exit code (rc==0). One packet, 2s wait, no retries, no thresholds. Includes the load-bearing routing insight (l.134-136): VTEP pings must source from the loopback because the underlay advertises only /32 loopbacks.
- `host_can_ping` (l.109-131): flushes neigh cache, sends `ping -c 15 -W 3`, scrapes "received" from ping's summary line, passes if `received >= 4`. **REJECTED as a truth-path pattern (per Gate 1 / owner decision), and independently on the merits:** the >=4/15 floor (l.109, 129) encodes acceptance of a 73%-loss data plane. Its own comment (l.110-117) admits it exists to tolerate Docker Desktop's slow BUM/ARP path without hardware split-horizon — an environment artifact, not a correctness criterion. A verification framework whose purpose is to assert "the network works" cannot define working as 4-of-15; it would mask real degradation (e.g., a duplicate-suppression bug or unidirectional flooding that still lets ~30% through would PASS). VerifiedNet truth checks must be deterministic and near-lossless (e.g., N-of-N with bounded warmup, or explicit loss budget declared per-scenario in config, never a buried default). Additionally it parses human ping output (locale/format fragile) and inherits _run's no-timeout shell=True. Verdict: REJECT for truth path; retain only in the original repo as a lab-environment workaround. The `ip neigh flush` pre-step is a separately noteworthy idea (stale-FAILED-neigh poisoning of validators) worth a documented note, not code harvest.

| # | Point | Finding |
|---|---|---|
| 1 | source repository | evpn-vxlan-frr-lab |
| 2 | source commit | 5b5a479bff19b1ae300f97434dbb0bcdc49adbea |
| 3 | exact source path | validate/checks.py |
| 4 | file purpose | Low-level pass/fail checks against lab containers; each returns `(ok: bool, detail: str)` for uniform reporting by validate_overlay.py |
| 5 | public symbols | `container_running`, `bgp_underlay_established`, `evpn_imet_from_peer`, `vxlan_iface_exists`, `bridge_fdb_has_her_to`, `host_can_ping`, `loopback_reachable` |
| 6 | private symbols relevant to Wave A | `_run` (l.9-11) — the shared subprocess wrapper all checks funnel through |
| 7 | internal imports | None (json, subprocess only) |
| 8 | external dependencies | stdlib; runtime: docker CLI; vtysh/ip/bridge/ping inside containers |
| 9 | global state | None — pure functions over subprocess output |
| 10 | side effects | Mostly read-only; exception: `host_can_ping` mutates container state via `ip neigh flush all` (l.118) before pinging |
| 11 | subprocess behavior | **shell=True throughout**, commands built by f-string interpolation of container names/IPs/ifaces (l.10, 15, 22-24, etc.); no argv form anywhere |
| 12 | command allow-list behavior | None |
| 13 | timeouts | **No subprocess timeout at all** in `_run`; only in-command waits: ping `-W 3` (host_can_ping), `-W 2` (loopback_reachable), `-W` implied by count. A wedged docker exec hangs the validator indefinitely |
| 14 | error handling | Return-tuple style, no exceptions: rc!=0 → (False, reason); JSONDecodeError → (False, "bad json"); missing peer → (False, "peer not configured"); ping-output parse failure silently leaves received=0 (l.126-128) |
| 15 | environment assumptions | Docker Desktop lab from this repo's docker-compose; FRR vtysh in leaf containers; lossy BUM path assumed acceptable (README-documented limitation baked into host_can_ping) |
| 16 | hardcoded names | Default `iface="vni10010"`, `expected_vni=10010` (l.76, 85); container names come in as parameters (good) |
| 17 | hardcoded addresses | None in signatures (peer IPs/VTEPs are parameters); IMET needle format `[3]:[0]:[32]:[<vtep>]` hardcodes FRR's EVPN prefix string layout (l.57) |
| 18 | output formats | Uniform `(bool, short-detail-string)` tuples; details truncate stderr to 60 chars (l.26, 50) |
| 19 | existing tests | None (validate_overlay.py is the runner, not a test suite) |
| 20 | missing tests | JSON-shape fixtures for bgp/evpn parsers; ping-summary parser against real ping variants; behavior on hung subprocess (would reveal the missing timeout) |
| 21 | reuse classification | Split: `bgp_underlay_established` REFACTOR_AND_REUSE (parse logic + failure taxonomy; replace transport); `loopback_reachable` REFACTOR_AND_REUSE (deterministic 1-packet semantics + source-from-loopback insight); `container_running` REFACTOR_AND_REUSE (trivial); `_run` REJECT (shell=True, no timeout — reimplement on the shared exec transport); `host_can_ping` REJECT for truth path / RETAIN_ONLY_IN_ORIGINAL_REPO (>=4/15 floor rejected per Gate 1 owner decision, rationale above); `evpn_imet_from_peer`, `vxlan_iface_exists`, `bridge_fdb_has_her_to` DEFERRED to Wave B/EVPN backend (not classified in Wave A) |
| 22 | exact reusable symbols | `bgp_underlay_established` body (ipv4Unicast.peers[ip].state=="Established" + peer-absent/bad-json taxonomy), `loopback_reachable` semantics (`ping -c 1 -W 2 -I <src>`), the `(ok, detail)` check contract, `container_running` |
| 23 | symbols to rewrite | `_run` → argv-based exec with mandatory timeout (share File 1/2 transport); all f-string shell commands → argv lists |
| 24 | symbols to reject | `host_can_ping` (min_replies=4/count=15 truth rule — rejected); ping-stdout scraping approach generally |
| 25 | required adapter/interface | Same exec-transport interface; a Check protocol `check(ctx) -> (ok, detail)` so VerifiedNet verifiers stay uniform with File 1's predicate polling (`wait_for_state(lambda: check(...)[0], ...)` composes cleanly) |
| 26 | proposed VerifiedNet destination (candidate) | verifiednet/checks/bgp.py (bgp_established), verifiednet/checks/reachability.py (loopback_reachable reimplemented); host_can_ping: nowhere (documented rejection in Gate 2 record) |
| 27 | provenance action | Ledger entry repo+commit+path; record the explicit REJECT of the 4/15 rule with owner-decision citation; mark the three EVPN checks as Wave B deferrals with this commit pinned |
| 28 | risks | If harvested carelessly: no-timeout hangs and shell injection (mitigated by mandatory transport rewrite); `show ip bgp summary json` vs `show bgp summary json` command-string drift between the two repos — VerifiedNet should standardize on one and verify JSON shape parity on its FRR version; ping-output parsing must not survive into harvested code |

---

## Symbol-level harvest table

| symbol | file | harvest verb | required modifications (explicit list) | Wave A role |
|---|---|---|---|---|
| `wait_for_state(predicate, timeout, interval=0.5)` | faults/bgp_asn_mismatch.py | copy symbol with modifications | 1) take the state-reader callable as a parameter instead of closing over module-level `read_peer_state`; 2) keep monotonic-deadline + return-last-state-never-raise semantics; 3) move to shared polling lib | Core Gate 4 convergence-wait primitive |
| `_read_peer_raw` | faults/bgp_asn_mismatch.py | copy symbol with modifications | 1) parameterize peer_ip; 2) route through exec-transport adapter instead of `_docker_exec`; 3) keep (None,None)-on-absent contract | Raw BGP peer state read for fault verification |
| `read_peer_state` | faults/bgp_asn_mismatch.py | copy symbol with modifications | 1) parameterize CORRECT/WRONG ASN and peer; 2) keep 4-way categorization incl. `other:<state>:asn=` catch-all | Fault-state oracle for ASN-mismatch scenario |
| `_apply_inject` / `_apply_restore` (vtysh sequences) | faults/bgp_asn_mismatch.py | copy symbol with modifications | 1) parameterize ASN/peer; 2) swap `docker exec` prefix for transport adapter; 3) preserve restore's two-step revert + `clear bgp <peer>` (documented ~2s vs ~15s reconvergence) | ASN-mismatch fault mutation on plain FRR |
| `inject()` / `restore()` lifecycle bodies | faults/bgp_asn_mismatch.py | copy symbol with modifications | 1) keep fail-loud established-precondition, before/after prints, no-op-restore guard, `_peer_reachable` restore guard; 2) replace prints with VerifiedNet structured logging; 3) parameterize timeouts (30s/60s defaults) | Fault lifecycle template for Wave A BGP fault |
| `_docker_exec` (both copies) | faults/bgp_asn_mismatch.py + collectors/sonic_state.py | reimplement behavior from specification | 1) single implementation behind `run_on_node(node, argv, timeout)` transport interface; 2) keep argv-list/no-shell, capture, check=True, per-command timeout, three-way exception wrapping with cmd+stderr context; 3) support containerlab/SSH backends | Shared exec transport |
| `FaultInjectionError` / `CollectorError` + error-key convention | faults/bgp_asn_mismatch.py, collectors/sonic_state.py | copy symbol nearly unchanged | 1) unify into VerifiedNet exception hierarchy; 2) keep collectors-never-raise / faults-raise split | Error-handling contract |
| `_check_container_running` | faults/bgp_asn_mismatch.py | reimplement behavior from specification | 1) generalize to node-alive precondition per transport backend; 2) keep fail-fast + actionable-setup-hint message style | Precondition gate |
| `_peer_reachable` | faults/bgp_asn_mismatch.py | copy symbol with modifications | 1) parameterize peer/transport; 2) keep 1-packet `-W 1` probe semantics | Restore-guard reachability probe |
| `collect_bgp_summary` | collectors/sonic_state.py | copy symbol with modifications | 1) transport adapter; 2) keep output dict shape {bgp_instance_present, neighbors[{neighbor,asn,state}], source[,error]}, dual-AF iteration, never-raise; 3) decide `show bgp` vs `show ip bgp` string and pin it | Wave A BGP evidence collector |
| `collect_interface_state` / `collect_interface_counters` / `_parse_redis_hgetall` / `_COUNTER_FIELDS` | collectors/sonic_state.py | retain only as test fixture | None (SONiC-Redis-specific; keep in original repo; optionally reuse their output dicts as fixture shapes for collector-contract tests) | Not in Wave A truth path |
| `collect_recent_logs` | collectors/sonic_state.py | use only as architectural reference | Reference the sentinel-vs-real-failure discrimination and input clamping when writing VerifiedNet log collectors | Log-collector design input |
| `Scenario` dataclass | main.py | copy symbol with modifications | 1) add fixture_up/fixture_down callables (replacing hardcoded configure_bgp.sh); 2) add post-restore verification hook; 3) keep frozen dataclass, evidence_filter, post_inject_delay, manual_restore_command fields | Gate 4 scenario registry entry type |
| `SCENARIOS` registry + argparse `choices` dispatch | main.py | use only as architectural reference | Reimplement as VerifiedNet scenario registry; keep no-silent-default rule | Scenario dispatch pattern |
| `take_snapshot` / `print_snapshot` / `_one_line_summary` | main.py | copy symbol with modifications | 1) collector set injected, not hardcoded; 2) keep {name: dict} shape and stderr one-line summaries; 3) consider parallel collection to reduce skew | BEFORE/AFTER evidence snapshots in Gate 4 loop |
| `main()` run flow (fixture-up → BEFORE → inject → sleep → AFTER → evidence → agents → restore → fixture-down; try/finally; exit codes 0/1/2/3/4/7) | main.py | use only as architectural reference | Reimplement deterministic subset; add post-restore snapshot; make agent stages pluggable/stubbed | Gate 4 loop blueprint |
| `_call_with_stdout_to_stderr` | main.py | copy symbol nearly unchanged | Only if VerifiedNet keeps print-based fault scripts; drop once structured logging lands | stdout hygiene shim |
| `_filter_logs_for_interface` (SONiC noise list) | main.py | use only as architectural reference | Keep per-scenario evidence-filter hook concept; reject SONiC-VS-specific suppression strings | Evidence-hygiene pattern |
| `_run` | validate/checks.py | reject | — (shell=True, f-string command interpolation, no timeout; superseded by shared transport) | None |
| `container_running` | validate/checks.py | copy symbol with modifications | 1) re-express on transport/`docker inspect` via argv; 2) keep (ok, detail) contract | Node-alive check |
| `bgp_underlay_established` | validate/checks.py | copy symbol with modifications | 1) transport rewrite (argv + timeout); 2) keep parse `ipv4Unicast.peers[ip].state == "Established"` and the four-way failure detail taxonomy; 3) unify command string with collect_bgp_summary's | Wave A BGP truth check |
| `loopback_reachable` | validate/checks.py | copy symbol with modifications | 1) transport rewrite; 2) keep deterministic `ping -c 1 -W 2` + optional `-I <source>` and the source-from-loopback rationale in a comment; 3) use exit code, never stdout parsing | Wave A underlay reachability check |
| `host_can_ping` (min_replies=4 of count=15) | validate/checks.py | reject | — REJECTED per Gate 1/owner decision: the >=4/15 floor accepts a 73%-loss path as PASS, encodes a Docker-Desktop BUM/ARP environment workaround as a truth rule, would mask partial data-plane faults; also scrapes ping stdout and rides no-timeout shell=True `_run`. Loss tolerance, if ever needed, must be an explicit per-scenario declared budget. The `ip neigh flush` insight is recorded as a design note only | None (documented rejection) |
| `evpn_imet_from_peer`, `vxlan_iface_exists`, `bridge_fdb_has_her_to` | validate/checks.py | use only as architectural reference (Wave A) | DEFERRED to Wave B / EVPN backend; re-evaluate against this pinned commit then | Not in Wave A |

