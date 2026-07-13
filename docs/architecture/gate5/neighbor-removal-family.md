# Gate 5.1 + 5.2 — Shared Lifecycle Enablers and the BGP Neighbor-Removal Family

**Status:** IMPLEMENTED. Gate 5.1 added exactly the four shared enablers the
approved plan (`fault-family-plan.md`, §7/§13) proved necessary; Gate 5.2 added
the first new verified fault family, **BGP neighbor removal** (missing-object
taxonomy). Everything is additive: no Gate 3 or Gate 4 contract changed, no
schema changed, no verifier redesigned, and the remote-AS family's behavior is
regression-proven unchanged. **No AI component determines ground truth** —
every truth value below originates from deterministic collectors and the frozen
`ClaimVerifier`.

## Gate 5.1 — the four enablers (nothing else)

1. **Evidence-provider phase plans as pure data.**
   `LiveScenarioEvidenceProvider` (one implementation, unchanged collection
   machinery) now accepts optional `phase_plans: dict[Phase, tuple[NodePlan,
   ...]]`. `NodePlan` is a frozen dataclass (`node`, `collectors`,
   `route_prefixes`, `expected_peers`) validated at construction. With the
   parameter omitted the provider builds exactly the Gate 4 remote-AS plans —
   the remote-AS binding passes `None`, so that family's evidence is
   byte-identical to Gate 4. A phase missing from an explicit mapping raises;
   plans are never guessed.

2. **Explicit fault-family binding.** `orchestrator/families.py` defines the
   frozen `FaultFamilyBinding` (template id, root-cause label, generator,
   scenario factory, mutation shapes, phase-plan builder) and the
   hand-maintained tuple `APPROVED_FAMILY_BINDINGS` with exactly two entries.
   `run_accepted_incident` takes `binding=` (default: the remote-AS binding —
   existing call sites unchanged) and refuses a scenario whose `template_id`
   does not match the binding. No plugin system, no registration, no
   decorators, no discovery, no reflection, no dynamic imports. Artifact
   naming and the eight-phase lifecycle are deliberately NOT per-family.

3. **New verification checks** (factories only; `ClaimVerifier` untouched):
   `bgp_peer_present` / `bgp_peer_absent` (over `bgp.peer.<ip>.present`),
   `iface_admin_up` / `iface_admin_down` (over `iface.<name>.admin`),
   `iface_oper_down` (over `iface.<name>.oper`), `reachability_fails`
   (over `ping.<ip>.all_success`). Metric keys reuse existing collector
   normalization exactly.

4. **`BgpSummaryCollector.expected_peers` (additive).** For each explicitly
   requested peer the collector ALSO emits `bgp.peer.<ip>.present` =
   `"true"|"false"` — the `RoutePresenceCollector` requested-object
   discipline. A removed peer becomes AFFIRMATIVE `"false"` evidence (FAILable)
   instead of a silently missing metric (INSUFFICIENT). Default `()` emits
   byte-identical Gate 4 metrics; no existing metric changed, no schema
   version bumped.

   **Live-discovered FRR behavior (recorded evidence, first live run):**
   FRR 8.4.1 OMITS the entire `ipv4Unicast` object from
   `show ip bgp summary json` once the LAST IPv4-unicast neighbor is removed —
   the default parser correctly raised `ParserError: missing ipv4Unicast
   object` mid-onset (and the composition root's `finally` restored the lab
   and tore down to zero residue, exactly as designed). In expected-peers mode
   that omission IS the zero-peers observation, so `parse_bgp_summary` gained
   an opt-in `allow_missing_af` used ONLY by expected-peers collection; the
   DEFAULT contract (Gate 3/4 collectors, convergence helper) is unchanged —
   a missing address family remains a loud parse failure. Both offline sims
   emulate the real omission, and both directions are unit-tested.

## Gate 5.2 — the neighbor-removal family

**Mutation (router_a only; `TargetPolicy` denies everything else).** Three
exact shapes (`bgp_neighbor_removal_mutation_shapes`, ADR-0005 discipline —
exact count, exact order, fullmatch, parameters only in ASN/IPv4 positions):

```
remove_neighbor : configure terminal / router bgp <ASN> / no neighbor <IPv4>
restore_neighbor: configure terminal / router bgp <ASN>
                  / neighbor <IPv4> remote-as <ASN>
                  / address-family ipv4 unicast / neighbor <IPv4> activate
clear_bgp       : clear bgp <IPv4>
```

FRR's `no neighbor` deletes the peer object INCLUDING its address-family
activation, and the lab renders `no bgp default ipv4-unicast` — so the
five-command restore (ending in the load-bearing `activate`) recreates the
neighbor exactly as rendered originally; a truncated restore matches no shape
(policy) and an unactivated restore is caught by recovery route checks
(deterministic FAIL, offline-failure-tested).

**Verification chain** (`BgpNeighborRemovalScenario`, mirroring the frozen
Gate 4 lifecycle; same ledger, executor, transcripts, polling):

| Phase | Checks (deterministic, poll-twice where noted) |
|---|---|
| Precondition | `bgp_established`, `bgp_peer_present`, `reachability_ok`, `iface_operational`, `route_present(T, P_lo)`; captures baseline `config.sha256` for BOTH nodes |
| Onset (polled ×2) | `bgp_peer_absent(T)` — affirmative `"false"`; `bgp_not_established(P)`; `route_absent(T, P_lo)`; `route_absent(P, T_lo)` |
| Onset invariants | `iface_operational(T)`, `reachability_ok(T)` (link unaffected), `config_unchanged(P)` |
| Recovery (polled ×2) | `bgp_established(T)`, `bgp_peer_present(T)`, `remote_as_equals(T, correct)` |
| Recovery final | `route_present` both directions; **`config_unchanged(T, baseline_sha)` — the byte-identical restore proof** |

**Recovery proof.** The target's post-restore `show running-config` must hash
EXACTLY to its precondition baseline (FRR serializes canonically; Gate 5.0
plan §6/§8). The equality is a persisted, committable verifier verdict inside
`GroundTruth` — not a test-only assertion — and a serialization drift is a
loud FAIL that refuses the accepted record; it is never weakened silently.

**Evidence plans** (`_neighbor_removal_phase_plans`, pure data): onset
requests each loopback on exactly one node (so `route_absent` never sees the
owner's connected route); config is collected on the PEER only at onset and on
the TARGET only at recovery, so each `config_unchanged` sees exactly one
`config.sha256` observation (the verifier is metric-keyed and target-blind);
the target's BGP collection always names the peer in `expected_peers`.

**FaultInjection encoding:** `method="vtysh-no-neighbor"`,
`parameter_name="neighbor"`, `before_value="<peer_ip> remote-as <asn>"`,
`after_value="removed"` (schema unchanged).

## Offline verification

Full suite on the development container and canonical host: `ruff` clean,
`mypy` clean (71 source files), **472 offline tests passed** (440 baseline +
32 new), 23 live tests collected (skip without Docker). New coverage: exact
shape allow/deny (incl. truncated restore, reordered/extended sequences,
cross-family command denial, non-vtysh binary denial); collector
`expected_peers` present/absent/default-unchanged; the six check factories;
full lifecycle through the REAL scenario + executor + provider with the REAL
family plans (`NeighborLabSim`, canonical config text — byte-identical restore
proven offline); failure paths (remove fails → `INJECTING` + recovery path
open; restore fails → `RESTORING`; missing activation → non-committable
recovery routes AND config hash; double-inject refused; restore-after-restored
is a mutationless no-op; peer never mutated); composition-root wiring through
the binding (accepted, template-mismatch refused) and **cross-family
regression** (remote-AS + neighbor-removal runs in ONE verified index, both
reload-verified, distinct digests).

## Live verification (canonical host)

The full live integration tier ran on the canonical host (macOS/arm64, Docker
29.1.3 / Compose 2.40.3-desktop.1, pinned
`frrouting/frr:v8.4.1@sha256:0f8c174d…`, FRR 8.4.1_git):

```
23 integration tests passed, 474 deselected, in 80.27s.
```

`test_accepted_live_neighbor_removal_incident` passed end to end through the
composition root with the family binding: affirmative peer absence observed
(`bgp_peer_absent` verdict, observed `("false",)`), peer-side session down,
routes withdrawn both directions, neighbor recreated with activation, session
re-established with the correct remote-as, routes restored, and the
**byte-identical running-config recovery verdict
(`config_unchanged:router_a:…:recovery`) committable live** — FRR's canonical
serialization returned the exact baseline hash after remove + recreate, as the
Gate 5.0 analysis predicted. Three mutation transcript pairs (remove, restore,
clear), all on `router_a`; every ground-truth evidence id resolves in the
persisted run; the run is indexed and reload-verified through the index.

Gate 4 regression is fully green in the same run: the remote-AS accepted
incident, the precondition-rejected incident, the shared-index test, and the
entire healthy-lab/evidence/fixture tier all passed unchanged. Zero `vnet-*`
containers and zero `vnet-*` networks remained afterwards — including after
the FIRST live attempt, whose mid-onset parser failure (the recorded FRR
missing-AF behavior above) was cleanly restored and torn down by the
composition root's `finally` path.

## Explicitly not done

No interface shutdown, no prefix withdrawal, no scenario parameterization, no
multiple scenarios per family, no rejected-path changes, no new schemas, no
dataset/benchmark generation, no SLM/RAG/GraphRAG/memory/workflows/agents, no
scheduler/planner/CLI/dashboard, no Gate 6. Gate 5.3 (interface shutdown,
probe-first) awaits approval.
