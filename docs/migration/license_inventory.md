# VerifiedNet — Gate 0: License Inventory

Status: **audit complete** (based on reading actual LICENSE files at the recorded commits)
Date: 2026-07-11

> **Scope note.** Everything in this document is engineering provenance guidance compiled from
> reading the repositories — it is not legal advice. The pinned Gate 0 commits are the audit
> baseline; uncommitted local changes are out of scope until separately inventoried.

## 1. Per-repository license status

| Repo | License file | License | Copyright line | Compatible with reuse in VerifiedNet? |
|---|---|---|---|---|
| closcall | **absent** | **none published** (all-rights-reserved by default toward the public) | n/a | **Owner reuse: permitted.** The copyright owner may reuse her own code freely — the missing public license is not a legal blocker for owner-driven harvesting. It IS a public open-source reuse and provenance issue: third parties cannot legally reuse closcall or closcall-derived components until a license is published. Recommended: add an explicit Apache-2.0 license to closcall **before publicly extracting or redistributing** closcall-derived components in VerifiedNet. Marked: **public-release/provenance action required** (not "reuse legally impossible"). |
| neuronoc-network-ops-assistant | LICENSE | MIT | © 2026 Chandana Nandi | Yes |
| sonic-troubleshooting-agent | LICENSE | MIT | © 2026 Chandana Nandi | Yes |
| sonic-intent-agent | LICENSE | MIT | © 2026 Chandana Nandi | Yes |
| evpn-vxlan-frr-lab | LICENSE | MIT | © 2026 Chandana Nandi | Yes |
| sonic-acl-validation-harness | LICENSE | **Apache-2.0** | (standard Apache text) | Yes — see mixing note below |
| constellation | LICENSE | MIT | © **2024 Chandana Reddy** | Yes for reference; **name/year inconsistency** should be corrected in that repo. Moot for code copy since Gate 2 policy is design-reference-only for constellation. |

## 2. Mixing and outbound-license analysis

- MIT → any: MIT-licensed code can be incorporated into an MIT or Apache-2.0 VerifiedNet with
  attribution preserved.
- Apache-2.0 (sonic-acl-validation-harness) → MIT project: permissible but requires carrying the
  Apache-2.0 notice/attribution for the copied portions; the cleaner path is licensing VerifiedNet
  itself as **Apache-2.0** (also gives an explicit patent grant, sensible for a benchmark platform
  meant for public/third-party use).
- **Proposal (awaiting owner approval): VerifiedNet outbound license = Apache-2.0**, with per-file
  provenance headers and a `NOTICE` file listing source repos, paths, and original licenses.
  No LICENSE or NOTICE file will be created until the owner approves this proposal.
  Status: **proposed — not implemented**.

## 3. Third-party ecosystem licenses touched by planned harvests

| Dependency | License | Used by (source) | Note |
|---|---|---|---|
| FRRouting 8.4.1 (container) | GPL-2.0 | evpn lab, neuronoc lab | Used as an unmodified, separate runtime container. That usage pattern generally preserves separation from VerifiedNet's own code, but this is engineering provenance guidance, not legal advice — licenses/notices and the final distribution packaging (especially if VerifiedNet ever ships images or bundles) must be reviewed before release. |
| SONiC / docker-sonic-vs | Apache-2.0 (SONiC project) | sonic-* repos | Runtime container; fine. `docker-sonic-vs-fixed` is a locally built derivative — its Dockerfile must be preserved in VerifiedNet infra with provenance. |
| Nokia SR Linux image | proprietary, free-to-pull license | closcall | **Cannot be redistributed.** VerifiedNet docs must instruct users to pull from Nokia's registry; never mirror the image. |
| pybatfish / Batfish | Apache-2.0 | sonic-intent-agent | fine |
| Ollama + qwen2.5:7b-instruct | MIT (client); model under Qwen/Apache-2.0-style terms | 4 repos | Model weights are not vendored; runtime pull. Record exact model tag/digest in manifests. |
| PyTorch / transformers / peft / datasets | BSD-3 / Apache-2.0 | sonic-troubleshooting-agent fine_tuning | fine |
| FAISS, sentence-transformers | MIT / Apache-2.0 | neuronoc | fine |
| gnmic, Prometheus, pgvector/Postgres | Apache-2.0 / Apache-2.0 / PostgreSQL+ (pgvector MIT-like) | closcall | pinned by digest in closcall `compose.yaml` — keep that practice |
| nicolaka/netshoot | Apache-2.0 | evpn lab | currently `:latest` (violates VerifiedNet quality rule — pin on harvest) |

## 4. Unclear / action-required items

1. **closcall has no published license** → public-release/provenance action required (not an
   owner-reuse blocker). The owner may harvest her own closcall code now; before VerifiedNet
   publicly extracts or redistributes closcall-derived components, an explicit license
   (recommended: Apache-2.0) should be committed to closcall so provenance records point at a
   licensed source. Status: **public-release/provenance action required**.
2. **constellation copyright line** says "Chandana Reddy 2024" while all other repos say
   "Chandana Nandi 2026" — harmless for design-reference use, but should be normalized.
3. sonic-acl-validation-harness Apache-2.0 attribution must be carried in VerifiedNet's notice
   materials if any of `acl/db_checks.py` (SAI mask logic, fingerprinting) is copied. The NOTICE
   file itself is created only after the outbound-license proposal is approved. Status: planned,
   contingent on §2 approval.
4. No source repo contains third-party vendored code (verified: no `vendor/`, no copied external
   modules found) — provenance burden is limited to the author's own seven repos.
