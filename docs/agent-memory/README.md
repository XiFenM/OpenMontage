# Agent Memory (project-level, version-controlled)

This folder is the **durable, committed copy** of the AI agent's project memory
— operational facts, environment quirks, and decisions that are non-obvious and
worth keeping across sessions *and across machines*.

## Why this exists

The agent harness keeps its working memory in `.claude/memory/`, which is
**gitignored** (see `.gitignore`) so per-environment scratch memory never gets
committed. The downside: that memory is lost when you move to a new machine or
re-clone the repo. This folder is the migration-safe mirror — it travels with
the repository.

- **Working copy (live, gitignored):** `.claude/memory/`
- **Durable copy (committed, this folder):** `docs/agent-memory/`

`MEMORY.md` is the index; each other `*.md` file is a single memory.

## What travels (scope)

Only memories that are **relevant to this repository and portable across
machines** are committed here. Each memory declares `scope` in its frontmatter
(under `metadata:`):

- `scope: repo` → committed to this folder (e.g. provider tools, the Remotion
  fix, setup gotchas any clone hits).
- `scope: machine` → stays only in `.claude/memory/` (e.g. "this box has no
  system python, use uv" — true here, irrelevant to a fresh clone elsewhere).

`make memory-save` copies only `scope: repo` files and regenerates the index
above; machine-scoped memories are deliberately skipped.

## Syncing the two

Run from the repo root (or use the Make targets below):

```bash
# Push live memory -> durable repo copy (do this before committing)
make memory-save

# Seed live memory <- durable repo copy (do this on a fresh clone / new machine)
make memory-restore
```

On a brand-new environment the harness starts with empty memory, so run
`make memory-restore` once after cloning to re-seed `.claude/memory/` from this
folder. Commit after `make memory-save` so new memories survive the next move.
