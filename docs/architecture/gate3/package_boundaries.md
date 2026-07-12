# Package Boundaries (Gate 3)

Enforced by the consolidated AST guard (`tests/security/test_import_boundaries.py`),
which runs in CI from the first commit and validates itself against deliberately
violating fixtures.

| Package | Single responsibility | May import | Must never import |
|---|---|---|---|
| `verifiednet.schemas` | versioned, DB-free data contracts | pydantic, stdlib | any VerifiedNet implementation package |
| `verifiednet.common` | canonical JSON, hashing, ids/RunContext, logging, errors | stdlib | (sibling root; imports no other package) |
| `verifiednet.runtime` | bounded argv-only execution, policies, transcripts | common | docker SDKs; anything above it |
| `verifiednet.labs` | LabBackend interface + FRR rendering | schemas, common, runtime | verifiers, incidents |
| `verifiednet.collectors` | read-only evidence collection | schemas, common, runtime.results | **runtime.mutation**, faults |
| `verifiednet.verifiers` | pure claim verification + checks + polling | schemas, common | runtime, labs, collectors |
| `verifiednet.faults` | fault lifecycle, ledger, ASN-mismatch scenario | schemas, common, runtime, labs, collectors, verifiers | incidents |
| `verifiednet.incidents` | oracle, IncidentRecord builder, manifest writers | schemas, common | runtime, labs, collectors, faults |

Dependency DAG (schemas and common are sibling roots; corrected in Gate 2.5 §7):

```
   schemas (root)          common (root)
        ▲                     ▲
        │            ┌────────┘
        │         runtime
        │            ▲
        ├──────┬─────┼──────────┐
     verifiers │   labs     collectors
        ▲      │     ▲          ▲
        └──── faults ┴──────────┘
                ▲
            incidents (data-only consumer)
```

Additional structural rules enforced by the guard: `subprocess` is importable only
by `verifiednet/runtime/process.py`; `shell=True` and `os.system` are banned across
the entire source tree.

Faults is the only package handed a mutation-capable executor. Collectors are
constructed with read-only executors and additionally cannot even import the
mutation module. Incidents can reach no execution surface at all.

The Gate 4 orchestrator (not yet written) will sit above all packages and wire
`evidence_provider` callables to real collectors against the live lab.
