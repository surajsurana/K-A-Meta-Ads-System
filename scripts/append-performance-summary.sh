#!/usr/bin/env bash
# Safe append-only writer for knowledge/performance-summaries.jsonl - the
# structured store of each period's headline numbers (spend, orders, order
# value, blended CPO, blended ROAS), added 2026-09-05 alongside the
# weekly->monthly->quarterly->half-yearly->yearly rollup system (see
# scripts/rollup_performance_summaries.py and docs/learning-layer-design.md
# SS8). Before this, these five numbers were computed fresh every review and
# only ever sent as Telegram text - never stored anywhere machine-readable,
# so there was nothing for a rollup to roll up. This is that missing store.
#
# Same safety discipline as scripts/append-learning-log.sh (fetch, rebase,
# append, commit, push, retry-on-race, never force-push) - kept as its own
# script rather than merged into that one, matching this project's existing
# precedent of a dedicated append script per file (see
# scripts/append-product-map.sh's own reasoning) rather than a shared
# generic helper.
#
# Usage: scripts/append-performance-summary.sh '<single-line-json-object>'
#   Called by prompts/weekly-review.md (a "weekly" row) and
#   prompts/monthly-strategic-review.md (a "monthly" row) right after
#   computing the standard five headline numbers - in addition to, not
#   instead of, the existing Telegram notification.
#
# Exit code 0 = entry is committed AND pushed to origin. Any other exit code
# means the entry did NOT make it to GitHub - the caller must treat this as
# a real failure, not log-and-continue.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "ERROR: expected exactly one argument (the JSON line to append)." >&2
  exit 2
fi

JSON_LINE="$1"

if ! printf '%s' "$JSON_LINE" | grep -qE '^\{.*\}$'; then
  echo "ERROR: argument does not look like a single-line JSON object. Refusing to write." >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SUMMARY_FILE="knowledge/performance-summaries.jsonl"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

sync_to_latest() {
  git fetch origin "$BRANCH"
  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort || true
    echo "ERROR: rebase onto origin/$BRANCH failed - something touched" >&2
    echo "performance-summaries.jsonl in a way that isn't a pure append (or" >&2
    echo "another kind of conflict). Refusing to auto-resolve. Manual review required." >&2
    exit 3
  fi
}

sync_to_latest

printf '%s\n' "$JSON_LINE" >> "$SUMMARY_FILE"
git add "$SUMMARY_FILE"
git commit -m "performance-summary: append entry ($(date -u +%Y-%m-%dT%H:%M:%SZ))" --quiet

MAX_ATTEMPTS=5
ATTEMPT=1
while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
  echo "[append-performance-summary] push attempt $ATTEMPT/$MAX_ATTEMPTS" >&2
  if git push origin "$BRANCH"; then
    echo "[append-performance-summary] pushed successfully on attempt $ATTEMPT" >&2
    exit 0
  fi
  echo "[append-performance-summary] push rejected (likely a concurrent writer) - retrying..." >&2
  ATTEMPT=$((ATTEMPT + 1))
  sleep $((RANDOM % 3 + 1))
  sync_to_latest
done

echo "ERROR: could not push performance-summary entry after $MAX_ATTEMPTS attempts." >&2
echo "The commit exists locally but is NOT on GitHub. Do not treat this entry" >&2
echo "as logged. Resolve manually before continuing." >&2
exit 1
