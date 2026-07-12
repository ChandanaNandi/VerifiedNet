# Data Contracts and Behavioral Interfaces (Gate 3)

All data schemas: Pydantic v2, `frozen=True`, `extra="forbid"`, `strict=True`,
explicit `schema_version: Literal[1]`, UTC-aware timestamps only, JSON round-trip
and invalid-input tests in `tests/contract` / `tests/unit`. Deterministic
serialization goes through `verifiednet.common.canonical` exclusively.

## Data schemas (verifiednet.schemas)

| Schema | Purpose | Notes |
|---|---|---|
| TopologySpec | topology + addressing + explicit `sessions:` | /30 p2p links enforced; session `remote_as` cross-checked against real node ASNs; sessions are first-class (faults target sessions) |
| ScenarioDefinition | what was asked (id, family, template, params, timeouts) | wrong-ASN etc. live here as data, never as code constants |
| FaultInjection | what actually happened (before/after values, transcript refs) | ground-truth input |
| EvidenceRecord | one captured observation: raw + hash + normalized + source | content-derived `evidence_id` (fixes upstream id-collision) |
| EvidenceBundle | phase-grouped records; `seal()` makes append raise | immutability tested |
| VerificationCheck | claim + metric + predicate + expected + phase | metric keys match collector conventions |
| VerificationResult | verdict + evidence ids + observed values | only PASS is committable |
| GroundTruth | fault metadata + verdicts + accepted evidence | machine label only; free text rejected by validator |
| IncidentRecord | canonical accepted/rejected incident | RecoveryResult merged into `restoration` + `recovery_results` (owner-approved); status-consistency validators |
| RunManifest | run identity: git rev, lock hash, topology hash, digests, seeds | Gate 2.5 W1 |
| EnvironmentManifest | OS/kernel/arch/python/runtime/image digests/FRR version | Gate 3 fills with fixtures; Gate 4 records live values |

Deferred by design (Gate 2.5): DatasetManifest, DatasetSplit, ModelPrediction,
BenchmarkRun, EvaluationReport. RecoveryResult intentionally does not exist.

`ExecResult` (+`ExecStatus`) is a runtime-owned serializable model, not a core schema.

`Phase` is a canonical `StrEnum` (baseline/onset/recovery/precondition); schema
phase fields coerce a string value to the enum via a `BeforeValidator` and always
store the enum member (freeze-check correction 4). `evidence_provider` is invoked
with `Phase` members, never raw strings.

## Behavioral interfaces (in their owning packages — Gate 2.5 W2)

| Interface | Package | Methods |
|---|---|---|
| LabBackend | labs | start, stop, reset, health_check, topology, execute_readonly, capture_environment_metadata |
| EvidenceCollector | collectors | name, collect(phase) → EvidenceRecord |
| Verifier | verifiers | verify(check, bundles) → VerificationResult |
| FaultScenario | faults | validate_preconditions, inject, verify_onset, restore, verify_recovery |
| ProcessRunner / TranscriptWriter / MutationExec | runtime / faults | injection points for tests and Gate 4 adapters |

DatasetExporter and ModelAdapter interfaces are deferred with their gates.

## Verdict semantics

`Verdict ∈ {PASS, FAIL, UNKNOWN, INSUFFICIENT}`. Untrusted evidence can never
verify a claim (skipped when `require_trusted`); untrusted-only observations yield
INSUFFICIENT. Contradictory trusted observations yield FAIL with detail. Only PASS
commits toward ground truth. `Predicate.ANY` asserts existence of at least one
trusted observation and is fully tested (upstream had it untested).
