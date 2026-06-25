#!/usr/bin/env bash
# Sync agent memory between the live (gitignored) harness store and the durable,
# committed copy in docs/agent-memory/.
#
#   scripts/sync-memory.sh save     # live -> repo (run before committing)
#   scripts/sync-memory.sh restore  # repo -> live (run on a fresh clone / new machine)
#
# Only memories marked "scope: repo" in their frontmatter travel with the repo.
# Machine/environment-specific memories ("scope: machine", or unmarked) stay
# local so the committed copy stays relevant to anyone cloning the repo.
#
# The live path is derived from the repo's absolute path the same way the harness
# slugifies it: every "/" (including the leading one) becomes "-".
set -euo pipefail

mode="${1:-}"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

live="$HOME/.claude/projects/$(printf '%s' "$repo_root" | sed 's:/:-:g')/memory"
repo="docs/agent-memory"

is_repo_scoped() {
  # True if frontmatter declares "scope: repo" — at top level OR nested under
  # metadata: (the harness normalizes top-level custom keys into metadata, so we
  # match at any indentation).
  grep -qE '^[[:space:]]*scope:[[:space:]]*repo([[:space:]]|$)' "$1"
}

case "$mode" in
  save)
    mkdir -p "$repo"
    # Clear old portable copies (keep the human-written README).
    find "$repo" -maxdepth 1 -name '*.md' ! -name 'README.md' -delete
    if [ ! -d "$live" ]; then echo "No live memory found at $live"; exit 0; fi
    saved=0 skipped=0
    for f in "$live"/*.md; do
      [ -e "$f" ] || continue
      base="$(basename "$f")"
      [ "$base" = "MEMORY.md" ] && continue   # index is regenerated below
      if is_repo_scoped "$f"; then
        cp -f "$f" "$repo/"; saved=$((saved+1))
      else
        skipped=$((skipped+1))
      fi
    done
    # Regenerate a filtered index from the copied (repo-scoped) files.
    {
      echo "# Memory Index (repo-portable)"
      echo
      for f in "$repo"/*.md; do
        base="$(basename "$f")"
        { [ "$base" = "README.md" ] || [ "$base" = "MEMORY.md" ]; } && continue
        name="$(sed -n 's/^name:[[:space:]]*//p' "$f" | head -1)"
        desc="$(sed -n 's/^description:[[:space:]]*//p' "$f" | head -1)"
        echo "- [${name:-$base}]($base) — ${desc}"
      done
    } > "$repo/MEMORY.md"
    echo "Saved $saved repo-scoped memory file(s) -> $repo (skipped $skipped machine-scoped). Commit to persist."
    ;;
  restore)
    mkdir -p "$live"
    cp -f "$repo"/*.md "$live"/ 2>/dev/null || true
    rm -f "$live/README.md"   # README is repo-only docs, not a memory entry
    echo "Restored $repo -> $live"
    ;;
  *)
    echo "usage: $0 {save|restore}" >&2
    exit 1
    ;;
esac
