---
name: memory-sync
description: This project mirrors agent memory into the git repo so it survives environment migration
scope: repo
metadata: 
  node_type: memory
  type: project
  originSessionId: 96982a05-1815-4255-9163-643db0afe646
---

Agent memory has a **durable committed copy** in the repo at `docs/agent-memory/`, mirroring the live (gitignored) harness store at `.claude/memory/`. This exists so memory survives moving machines / re-cloning (`.claude/memory/` is gitignored by design).

**Why:** The user asked for project-level memory that doesn't get lost on environment migration.

**How to apply:**
- Before finishing meaningful work, run `make memory-save` (or `bash scripts/sync-memory.sh save`) to copy live memory → `docs/agent-memory/`, then commit it.
- On a fresh clone / new machine, run `make memory-restore` once to re-seed `.claude/memory/` from the repo copy.
- The sync script derives the live path from the repo's absolute path (every `/` → `-`), matching how the harness slugifies it.

See [[env-setup-uv]] for environment setup on a new machine.
