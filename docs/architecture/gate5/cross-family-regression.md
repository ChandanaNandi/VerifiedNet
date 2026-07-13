# Gate 5.6 — Cross-Family Regression and Isolation

**Status:** IMPLEMENTED and live-verified. Proves that all four fault families
and their bounded cases run without contaminating one another, produce
repeatable truth-bearing outputs, coexist in one integrity-verifiable index, and
fail safely. Additive tests and one live spot-check suite; no production
redesign.

## Sequential isolation

Every catalog case runs with FRESH lab ownership; after each case the lab is
proven fully healthy before the next begins. The live reverse-orientation suite
(`test_frr_reverse_orientation.py`) runs the four `router_b` cases sequentially
into one shared index, and after EACH case independently asserts, via host-side
Compose-label queries, that zero project containers and zero project networks
remain — no state is carried into the next case. Offline, the same discipline is
exercised over all nine catalog cases with a fresh `CatalogLabSim` per case,
asserting the lab returns to full health (session Established, both links up,
both neighbors present, both prefixes advertised) after every case, a terminal
`RECOVERY_VERIFIED` ledger, and no unpaired mutation transcript entry.

## Shared-index regression

All approved cases are written into ONE run index and verified together: every
run id is unique, every run digest matches, every run loads through the index,
and the family/template of each entry matches its catalog case. The live reverse
suite proves the same for the four live `router_b` runs. Tampering one run's
persisted incident makes the whole index refuse to verify, while every untouched
run still verifies and loads independently from its own directory — one bad run
cannot mask or corrupt the others.

## Repeatability

For one case per family, two runs with different `run_id`s produce identical
truth-bearing outputs: template and root-cause label, target node, fault
before/after values, the sorted set of check ids, the sorted
`(check_id, verdict, observed)` verdict tuples, and the restoration's
forced-reset flag. Legitimately volatile values are excluded — timestamps, run
ids, content-derived incident ids, and run-local sequence numbers. Consistent
with the Gate 4/5 honesty rule, the two runs do NOT share a whole-directory
digest (real wall-clock timestamps differ); we assert the run digests *differ*
and never claim byte identity across live runs.

## Failure isolation

Tests prove: an uncatalogued or forged `ScenarioCase` is refused before any lab
action; invalid scenario parameters fail validation before any mutation; a
duplicate `case_id` is rejected at catalog construction; and no family can
invoke another family's mutation shape (a neighbor-removal command is denied by
the prefix family's policy and vice versa — each family's exact
`MutationCommandShape` allow-list is disjoint from the others).

## Security and policy regression

Re-verified with the catalog added: catalog case data is plain scalars (a
callable in `parameters` fails schema validation); a `target_node` carrying an
embedded vtysh command is rejected as an unknown node before any command is
built (scenario command construction reads only topology-derived values, never
raw parameter strings); every approved family's mutation shapes are `vtysh -c`
sequences and the runtime binary allow-list remains `{"vtysh"}` — **no new
binary, no `ip link`, no shell execution**; and the composition-root import
boundary still holds (no `src` package below `orchestrator` imports it, catalog
included). The existing guards (collectors cannot import mutation; artifacts
remain data-only; subprocess confined to `runtime/process.py`) are unchanged.

## Live evidence (canonical host)

The reverse-orientation suite (`test_frr_reverse_orientation.py`) ran all four
`router_b` cases live on the canonical host as part of the closure tier
(macOS/arm64, Docker 29.1.3 / Compose 2.40.3-desktop.1, pinned
`frrouting/frr:v8.4.1@sha256:0f8c174d…`, FRR 8.4.1_git). Each reverse case
(`ras-rev`, `nr-rev`, `if-rev`, `pf-rev`) produced an accepted, indexed,
reload-verified run with the fault targeting `router_b`, the peer (`router_a`)
never mutated, a terminal `RECOVERY_VERIFIED` ledger, and paired mutation
transcripts — proving the abstractions are orientation-independent. After EACH
case, independent host-side Compose-label queries confirmed zero project
containers and zero project networks; the four runs coexisted in ONE verified
shared index. The full live tier was **26 tests passed, 527 deselected, in
194.53s**, with zero `vnet-*` containers/networks remaining.
