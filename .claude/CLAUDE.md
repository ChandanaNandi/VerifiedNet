# VerifiedNet — Permanent Repository Coordination Protocol

This file is the standing operating procedure for any Claude session working on this
repository. **The repository is the only source of truth. Never assume repository state
from memory or from an earlier conversation.** Repository operations may have been
performed outside a Claude session (history cleanup, author-email correction, releases,
tag changes), so commit hashes, branches, tags, remotes, Git identity, and published
history may have changed. **Inspect first, act second.**

## Canonical facts (verify locally every time)

- Repository (local): `/Users/nandichandana/Downloads/VerifiedNet`
- GitHub remote: `https://github.com/ChandanaNandi/VerifiedNet.git`
- Branch: `main`
- Gate 3 tag: `v0.3-gate3-complete` — must remain at commit `7d27463`. Never move,
  delete, recreate, or force-update it.
- The GitHub release for `v0.3-gate3-complete` already exists. Never recreate or modify it
  unless explicitly requested.

## Required Git identity

All commits must use exactly:

```
Chandana Nandi <119757091+ChandanaNandi@users.noreply.github.com>
```

GitHub email-privacy protection is enabled: any personal Gmail address in a commit causes
push rejection (`GH007`). **Never** pass an inline identity override (never use
`git -c user.email=… commit` or `git commit --author=…`). Rely on the repository-local
config. If `git config user.email` is anything other than the noreply address above, stop
and set it before committing:

```
git config user.name "Chandana Nandi"
git config user.email "119757091+ChandanaNandi@users.noreply.github.com"
git config user.name && git config user.email   # verify
```

## Mandatory pre-work inspection (run and report before any change)

```
cd /Users/nandichandana/Downloads/VerifiedNet
git status
git branch -vv
git remote -v
git log -5 --oneline --decorate
git tag --list
git config user.name
git config user.email
```

Do not continue until these have been inspected and reported.

## Before every commit

1. Confirm the working-tree changes are limited to the approved task.
2. Show `git diff --stat` (and the actual diff for policy/doc files).
3. Confirm no unrelated file changed.
4. Confirm the repo identity is the GitHub noreply identity (above).
5. Create the commit only after those checks — with no inline identity override.
6. Immediately verify:

```
git show --no-patch --format=full HEAD
```

Both `Author` and `Commit` must be
`Chandana Nandi <119757091+ChandanaNandi@users.noreply.github.com>`. If not, amend before
pushing:

```
git commit --amend --no-edit --reset-author
```

Then verify again.

## Before every push

```
git fetch origin
git status
git log -3 --oneline --decorate
```

Confirm branch, ahead/behind state, and tag locations. Then:

```
git push origin main
```

If GitHub rejects the push, **stop and report the exact error**. Do not invent a
workaround, do not force-push, do not rewrite history, do not change tags. Wait for
approval.

## Never do these without explicit approval

Force-push; move, delete, or recreate tags; rewrite published history; recreate or delete
releases; merge unrelated commits; modify Gate 3; redesign or expand Gate 4; start Gate 5+;
implement SLM, RAG, GraphRAG, orchestration, agents, memory, or persistent workflows.
Those belong to future gates.

## Honesty rules

- Never say a commit was pushed unless the push command succeeded and was verified.
- Never say something was tagged or released unless it actually exists on GitHub and was
  verified.
- Never say tests or a live run passed unless the command actually ran and succeeded.
- Repository state is always more authoritative than prior conversation memory.

## Current gate status

- Gates 0–3: complete, released, tagged (`v0.3-gate3-complete` at `7d27463`).
- Gate 4: approved but **not started**. Implement exactly the approved Gate 4 plan; do not
  redesign, expand, or move functionality between gates.
- Gate 4 scope is only: live two-router FRR lab; accepted remote-AS-mismatch incident;
  rejected healthy-lab precondition failure; deterministic verification; recovery proof;
  manifests; transcripts; cleanup; live fixture capture. **No models, AI, agents, RAG,
  memory, GraphRAG, orchestration, or persistent workflows during Gate 4.**
- Layers 2–8 in `docs/architecture/final-platform-vision.md` are planning only; planned ≠
  done.

## Working style

Work in small commits. After each commit, report: files changed, tests run, assumptions,
remaining risks, and the next step. Never continue silently.
