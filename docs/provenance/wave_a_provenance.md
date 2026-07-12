# Wave A Provenance Register (Gate 3)

Every adapted symbol, per the Gate 3 provenance requirement. Source commits are the
Gate 0 pinned baseline. Harvest verbs follow the Gate 2/2.5 corrected
classifications. **ClosCall path used: REIMPLEMENT FROM SPECIFICATION** — closcall
has no published license (Gate 0 public-release/provenance action still open), so
no closcall expression was copied; behavior was reimplemented against the Gate 2
appendix-B specifications. This register plus NOTICE carry the attribution.

| # | VerifiedNet destination | Source repo (license) | Commit | Source path :: symbol | Classification / verb | Modifications | Tests added |
|---|---|---|---|---|---|---|---|
| 1 | `runtime/process.py::default_runner` | neuronoc-network-ops-assistant (MIT) | 5f24447 | `backend/app/lab/collector.py::_default_runner`, `CommandRunner` | REFACTOR_AND_REUSE / copy with modifications | argv-type enforcement, mandatory timeout, bytes-aware truncation + flag, RawResult shape, rc sentinels → typed fields | `tests/unit/test_runtime_readonly.py`, `tests/failure/test_runtime_failures.py` |
| 2 | `runtime/policy.py::CommandPolicy` | neuronoc (MIT) | 5f24447 | `collector.py::_assert_show_command`, `_FORBIDDEN_VTYSH_TOKENS` | REFACTOR_AND_REUSE / copy with modifications (Gate 2.5 §14) | parameterized policy object; dead multi-word tokens dropped; metachar rejection made explicit | `tests/unit/test_runtime_policy.py` |
| 3 | `runtime/policy.py::TargetPolicy` | neuronoc (MIT) | 5f24447 | `collector.py::_assert_known_router` | REFACTOR_AND_REUSE / copy with modifications | allow-set injected (topology-derived), not module constant | same |
| 4 | `runtime/mutation.py` write-ahead transcript | closcall (no published license) | d192bf3 | `executor/audit_guard.py::guarded_mutation` | reimplemented from specification | transcript-based write-ahead; blocks mutation on write failure | `tests/failure/test_runtime_failures.py` |
| 5 | `collectors/frr/bgp.py`, `interfaces.py`, `routes.py` parsers | neuronoc (MIT) | 5f24447 | `collector.py::_peers_from`, `_scrape_router`, `_scrape_route_table`, `_cap_text` | REFACTOR_AND_REUSE / copy with modifications | detached from NN schemas → EvidenceRecord; sorted, bounded, ParserError on malformed (no silent fallback) | `tests/unit/test_collectors_frr.py`, `tests/failure/test_collectors_failures.py` |
| 6 | `collectors/frr/reachability.py` | evpn-vxlan-frr-lab (MIT) | 5b5a479 | `validate/checks.py::loopback_reachable` | REFACTOR_AND_REUSE / copy with modifications | through bounded executor; 3/3 all-success policy; **≥4/15 floor (`host_can_ping`) REJECTED** per owner decision | same |
| 7 | `collectors/base.py::make_evidence_record` | closcall (no published license) | d192bf3 | `evidence/tools.py::_emit` envelope | reimplemented from specification | content-derived evidence_id (fixes id collision `source:subject:metric:at`) | `tests/unit/test_collectors_frr.py` |
| 8 | `verifiers/claims.py` | closcall (no published license) | d192bf3 | `evidence/claims.py::Claim/Predicate/Verdict/verify/committable` | reimplemented from specification (Gate 2.5 §14: copy-with-modifications semantics) | trusted-evidence enforcement inside verify; ANY predicate specified + tested; contradictory→FAIL | `tests/unit/test_verifiers_claims.py` (every predicate) |
| 9 | `verifiers/polling.py::poll_until` | sonic-troubleshooting-agent (MIT) | eb4c818 | `faults/bgp_asn_mismatch.py::wait_for_state` | REFACTOR_AND_REUSE / copy with modifications | typed predicate; injected clock/sleep; consecutive-confirmation (Gate 2.5 W9); caller-raises semantics kept | `tests/unit/test_polling.py` |
| 10 | `faults/frr_commands.py` | sonic-troubleshooting-agent (MIT) | eb4c818 | `_apply_inject`, `_apply_restore` vtysh sequences | REFACTOR_AND_REUSE / copy with modifications | re-targeted at plain FRR; ASN/peer as data; clear-bgp retained; MutationCommandPolicy tightened to exact named shapes (freeze-check 5) | `tests/unit/test_faults_commands.py` |
| 11 | `faults/bgp_remote_as_mismatch.py` lifecycle | sonic-troubleshooting-agent (MIT) | eb4c818 | fault lifecycle order, precondition/idempotency behavior | REFACTOR_AND_REUSE / re-expressed behind FaultScenario | ledger-phase guards (Gate 2.5 W7); onset requires mismatch AND not-Established; b-side unchanged check | `tests/unit/test_faults_lifecycle.py`, `tests/failure/test_lifecycle_failures.py` |
| 12 | `faults/ledger.py` | closcall (no published license) | d192bf3 | `chaos/ledger.py::Ledger/LedgerRecord/Phase` | reimplemented from specification (Gate 2.5 §14) | FaultScenario-aligned phases; legal-transition guards; torn-final-line tolerance; JSONL fsync | `tests/unit/test_ledger.py`, `tests/property/test_ledger_properties.py` |
| 13 | `common/hashing.py::sha256_file` | closcall (no published license) | d192bf3 | `datasets/manifest.py::sha256_file` | behavioral near-direct; expression reimplemented (license path) | streamed read; canonical-JSON coupling for object hashes | `tests/unit/test_common_hashing.py` |
| 14 | `common/logging.py` | closcall (no published license) | d192bf3 | `observability/logging.py::JsonFormatter/configure_logging` | reimplemented from specification | run/scenario/phase/incident context fields | `tests/unit/test_common_logging.py` |
| 15 | `schemas/topology.py` | closcall (no published license) | d192bf3 | `domain/fabric.py::FabricSpec/ResolvedTopology` | reimplemented from specification (Gate 2 verdict: grammar cannot express p2p) | explicit links/sessions; /30 rule; session↔ASN cross-validation | `tests/property/test_topology_properties.py` |
| 16 | `labs/frr/render.py` | neuronoc (MIT) | 5f24447 | `infra/lab/configs/*` FRR idioms, `docker-compose.lab.yml` | DESIGN_REFERENCE / generated from TopologySpec | no container_name; NET_ADMIN only (SYS_ADMIN rejected); deterministic ordering | `tests/unit/test_labs_render.py`, `tests/property/test_render_properties.py` |
| 17 | `tests/security/test_import_boundaries.py` | neuronoc (MIT) | 5f24447 | `backend/tests/test_{remediation,validation,telemetry}.py` AST scans | REFACTOR_AND_REUSE / copy with modifications | three copies consolidated into one policy-driven guard; self-validation fixtures added | guard self-tests (6) |
| 18 | `schemas/manifests.py` capture set | closcall (no published license) | d192bf3 | `scripts/emit_manifest*.py` | architectural reference only | OS/Python/seeds captured; writers fail loudly | `tests/unit/test_incidents_manifests.py` |
| 19 | `incidents/oracle.py`, `incidents/builder.py` | — (new; Gate 1 NO_ACCEPTABLE_SOURCE) | — | — | new implementation | — | `tests/unit/test_incidents_*.py`, `tests/contract/test_incident_shapes.py` |
| 20 | `common/canonical.py`, `common/runctx.py` | — (new; Gate 2.5 W5/W11) | — | — | new implementation | — | `tests/unit/test_common_canonical.py`, `tests/property/test_canonical_properties.py`, `tests/unit/test_common_hashing.py` |

## Gate 4 additions (new integration code — no external source adapted)

The Gate 4 live-execution layer is new VerifiedNet code that composes the
existing Gate 3 contracts against the real lab; it adapts no external source.

| # | VerifiedNet destination | Origin | Notes | Tests |
|---|---|---|---|---|
| G4-1 | `labs/frr/{backend,exec_adapter,compose_project,convergence,fixture_capture}.py` | new (Gate 4) | live FRR-on-Compose backend, logical/transport adapters, bounded BGP convergence, provenance fixture capture | `tests/unit/test_labs_frr_*`, `tests/integration/*` |
| G4-2 | `labs/frr/scenario_evidence.py::LiveScenarioEvidenceProvider` | new (Gate 4) | wires the existing Gate 3 collectors to the scenario's evidence callable over the READ-ONLY executor; never touches mutation | `tests/unit/test_labs_frr_incident_wiring.py`, `tests/integration/test_frr_remote_as_accepted_incident.py` |
| G4-3 | `FrrComposeBackend.build_mutation_adapter` | new (Gate 4) | explicit, separately-constructed mutation capability (never on the LabBackend protocol); `TargetPolicy` restricts mutation to the fault's node | `tests/unit/test_labs_frr_incident_wiring.py`, `tests/failure/test_labs_frr_incident_failures.py` |
| G4-4 | `labs/frr/rejected_scenario.py::RejectedPreconditionRun` | new (Gate 4) | honest precondition-rejection path: impossible RFC 5737 route evaluated by the existing verifier; only a deterministic FAIL rejects (INSUFFICIENT/UNKNOWN raise loudly); ledger stays PENDING, zero mutation, no ground truth | `tests/unit/test_labs_frr_rejected_scenario.py`, `tests/integration/test_frr_precondition_rejected_incident.py` |

ADR 0015 records the live-proven deviations (SYS_ADMIN required by FRR 8.4.1
`privs_init`; API-delivered inline configs; `interface_name` pinning) — row 16
above (`SYS_ADMIN rejected`) is superseded for the live backend by that ADR.

Not carried over (rejected, recorded here for completeness): EVL `_run`
(shell=True/f-string/no timeout), EVL `host_can_ping` 4/15 floor, NN
`container_name` + `pull_policy: never` compose patterns, STA per-agent Ollama
clients, sonic-intent-agent module-global `proposed_plans` (Wave B anyway).

Parser fixtures under `tests/fixtures/frr/` are **source-derived and provisional**;
they must be re-recorded against the live plain-FRR lab at Gate 4 before any live
correctness claim.
