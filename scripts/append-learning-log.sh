#!/usr/bin/env bash
# Safe append-only writer for knowledge/learning-log.jsonl.
#
# Why this exists: the learning log now has multiple independent writers —
# interactive VS Code sessions and unattended droplet cron runs — that can
# fire close together in time. A naive `echo '{...}' >> file && git commit &&
# git push` can silently lose another writer's entry on a race (whichever
# push lands second either fails, or worse, force-overwrites). This script
# makes a single append atomic and safe: fetch, rebase onto the latest
# remote state, append, commit, push, and retry-on-race — never force-push,
# never silently drop an entry. If it can't complete safely, it fails loudly
# instead of guessing.
#
# Usage: scripts/append-learning-log.sh '<single-line-json-object>'
#   (call from the project root, or from anywhere — the script cds to its
#   own repo root first)
#
# Exit code 0 = entry is committed AND pushed to origin. Any other exit code
# means the entry did NOT make it to GitHub — the caller (an agent) must
# treat this as a real failure, not log-and-continue, and must not touch
# any live Meta/Instagram write until this is resolved.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "ERROR: expected exactly one argument (the JSON line to append)." >&2
  exit 2
fi

JSON_LINE="$1"

# Basic sanity check: must look like a single JSON object on one line.
if ! printf '%s' "$JSON_LINE" | grep -qE '^\{.*\}$'; then
  echo "ERROR: argument does not look like a single-line JSON object. Refusing to write." >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="knowledge/learning-log.jsonl"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

MAX_ATTEMPTS=5
ATTEMPT=1

while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
  echo "[append-learning-log] attempt $ATTEMPT/$MAX_ATTEMPTS" >&2

  # 1. Get the true latest remote state before touching anything.
  git fetch origin "$BRANCH"

  # 2. Bring the local branch up to date. Rebase, not merge — this file is
  #    strictly append-only (never edited in place), so two independent
  #    appends are always trivially fast-forward/auto-mergeable UNLESS
  #    something genuinely unexpected happened (e.g. someone hand-edited an
  #    existing line). If rebase can't proceed cleanly, abort and fail loud
  #    rather than attempt any automatic conflict resolution on this file.
  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort || true
    echo "ERROR: rebase onto origin/$BRANCH failed — this means something touched" >&2
    echo "the learning log in a way that isn't a pure append (or another kind of" >&2
    echo "conflict). Refusing to auto-resolve. Manual review required." >&2
    exit 3
  fi

  # 3. Append the new entry locally.
  printf '%s\n' "$JSON_LINE" >> "$LOG_FILE"

  # 4. Commit just this file.
  git add "$LOG_FILE"
  git commit -m "learning-log: append entry ($(date -u +%Y-%m-%dT%H:%M:%SZ))" --quiet

  # 5. Try to push. If someone else pushed between our fetch and now, this
  #    fails — loop back to step 1 and retry, never force-push.
  if git push origin "$BRANCH"; then
    echo "[append-learning-log] pushed successfully on attempt $ATTEMPT" >&2
    exit 0
  fi

  echo "[append-learning-log] push rejected (likely a concurrent writer) — retrying..." >&2
  ATTEMPT=$((ATTEMPT + 1))
  sleep $((RANDOM % 3 + 1))
done

echo "ERROR: could not push learning-log entry after $MAX_ATTEMPTS attempts." >&2
echo "The commit exists locally but is NOT on GitHub. Do not treat this entry" >&2
echo "as logged. Resolve manually (git status / git log) before continuing," >&2
echo "and do not run 'git reset --hard' on this checkout until it's pushed." >&2
exit 1
