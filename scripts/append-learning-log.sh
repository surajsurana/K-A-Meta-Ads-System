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

# Fetch + rebase onto the true latest remote state. Rebase, not merge — this
# file is strictly append-only (never edited in place), so two independent
# appends are always trivially fast-forward/auto-mergeable UNLESS something
# genuinely unexpected happened (e.g. someone hand-edited an existing line).
# If rebase can't proceed cleanly, abort and fail loud rather than attempt
# any automatic conflict resolution on this file. Called once before the
# first commit below, and again on every push retry (to replay our already-
# committed entry onto whatever changed in the meantime) — never to redo
# the append itself, which happens exactly once regardless of how many
# retries the push needs (fixed 2026-09-05: the append used to sit *inside*
# the retry loop, so a real push conflict — the exact scenario this script
# exists to handle — would re-append the same entry a second time before
# retrying, silently duplicating it if that second push succeeded).
sync_to_latest() {
  git fetch origin "$BRANCH"
  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort || true
    echo "ERROR: rebase onto origin/$BRANCH failed — this means something touched" >&2
    echo "the learning log in a way that isn't a pure append (or another kind of" >&2
    echo "conflict). Refusing to auto-resolve. Manual review required." >&2
    exit 3
  fi
}

sync_to_latest

# Append the new entry locally, exactly once.
printf '%s\n' "$JSON_LINE" >> "$LOG_FILE"

# Keep knowledge/open-followups.json in sync in the same commit (added
# 2026-09-05 — see that script's own docstring for why this exists: without
# it, the daily heartbeat's due-now sweep has to re-scan the entire, ever-
# growing log from scratch every day). Never let an index-update hiccup
# block the actual log write — the log entry is the source of truth; the
# index is a derived cache and `--rebuild` can always regenerate it if it
# ever drifts.
(python3 "$REPO_ROOT/scripts/update-open-followups.py" --append "$JSON_LINE" || \
 py "$REPO_ROOT/scripts/update-open-followups.py" --append "$JSON_LINE") || \
 echo "[append-learning-log] WARNING: open-followups.json index update failed - log entry still proceeding, run 'python scripts/update-open-followups.py --rebuild' to repair" >&2

# Commit both files together, so the index is never out of sync with
# whichever commit actually reflects this entry.
git add "$LOG_FILE" "knowledge/open-followups.json"
git commit -m "learning-log: append entry ($(date -u +%Y-%m-%dT%H:%M:%SZ))" --quiet

MAX_ATTEMPTS=5
ATTEMPT=1

while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
  echo "[append-learning-log] push attempt $ATTEMPT/$MAX_ATTEMPTS" >&2

  # Try to push. If someone else pushed between our fetch and now, this
  # fails — re-sync (rebasing our existing commit, never re-appending) and
  # retry, never force-push.
  if git push origin "$BRANCH"; then
    echo "[append-learning-log] pushed successfully on attempt $ATTEMPT" >&2
    exit 0
  fi

  echo "[append-learning-log] push rejected (likely a concurrent writer) — retrying..." >&2
  ATTEMPT=$((ATTEMPT + 1))
  sleep $((RANDOM % 3 + 1))
  sync_to_latest
done

echo "ERROR: could not push learning-log entry after $MAX_ATTEMPTS attempts." >&2
echo "The commit exists locally but is NOT on GitHub. Do not treat this entry" >&2
echo "as logged. Resolve manually (git status / git log) before continuing," >&2
echo "and do not run 'git reset --hard' on this checkout until it's pushed." >&2
exit 1
