#!/usr/bin/env bash
# Safe git wrapper around scripts/rollup_performance_summaries.py - the
# actual weekly->monthly->quarterly->half-yearly->yearly rollup for
# knowledge/performance-summaries.jsonl (added 2026-09-05, Suraj's
# proposal - see docs/learning-layer-design.md SS8).
#
# Called once per monthly review run (prompts/monthly-strategic-review.md),
# since that's the only cadence where a calendar month has just closed -
# the natural moment to check whether last month's weeklies can now roll
# up, which can in turn make a quarter/half-year/year newly rollable in
# the same pass (the Python script cascades all of that in one call).
#
# Why this ISN'T just another safe-append script like
# scripts/append-performance-summary.sh / append-learning-log.sh: those
# only ever add a line, so a plain git rebase trivially reconciles two
# concurrent writers. A rollup both ADDS (the new rolled-up row) and
# REMOVES (the rows it replaces) in the same operation - a rewrite, not a
# pure append - so a naive "rebase the old diff onto new state" could
# reapply a removal that no longer makes sense, or miss a row someone else
# just added. Instead, on every attempt (including retries), this
# re-fetches, re-rebases, and RE-RUNS the Python computation fresh against
# whatever the true current state is - never replays a stale decision.
# rollup_performance_summaries.py is written to be idempotent (a no-op on
# an already-rolled-up state), which is exactly what makes this safe to
# simply retry from scratch rather than needing real conflict resolution.
#
# Usage: scripts/run-performance-rollup.sh
# Exit 0 whether or not a rollup actually happened - "nothing was due" is
# success, not a no-op error. Non-zero only on a genuine failure (git sync
# problem, or exhausting retries on a real push race).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SUMMARY_FILE="knowledge/performance-summaries.jsonl"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

sync_to_latest() {
  git fetch origin "$BRANCH"
  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort || true
    echo "ERROR: rebase onto origin/$BRANCH failed during rollup - refusing to" >&2
    echo "auto-resolve. Manual review required." >&2
    exit 3
  fi
}

run_python() {
  python3 "$REPO_ROOT/scripts/rollup_performance_summaries.py" "$REPO_ROOT/$SUMMARY_FILE" 2>/dev/null || \
  py "$REPO_ROOT/scripts/rollup_performance_summaries.py" "$REPO_ROOT/$SUMMARY_FILE"
}

apply_result() {
  # $1 = the JSON result from rollup_performance_summaries.py
  python3 -c "
import json, sys, datetime
result = json.loads('''$1''')
removed = set(result.get('removed', []))
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

path = '$SUMMARY_FILE'
kept = []
try:
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get('id') not in removed:
                kept.append(entry)
except FileNotFoundError:
    pass

for row in result.get('added', []):
    row['generated_at'] = now
    kept.append(row)

with open(path, 'w', encoding='utf-8') as f:
    for entry in kept:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
print('applied: +' + str(len(result.get('added', []))) + ' -' + str(len(result.get('removed', []))))
" 2>/dev/null || py -c "
import json, sys, datetime
result = json.loads('''$1''')
removed = set(result.get('removed', []))
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

path = '$SUMMARY_FILE'
kept = []
try:
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get('id') not in removed:
                kept.append(entry)
except FileNotFoundError:
    pass

for row in result.get('added', []):
    row['generated_at'] = now
    kept.append(row)

with open(path, 'w', encoding='utf-8') as f:
    for entry in kept:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
print('applied: +' + str(len(result.get('added', []))) + ' -' + str(len(result.get('removed', []))))
"
}

MAX_ATTEMPTS=5
ATTEMPT=1

sync_to_latest

while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
  RESULT="$(run_python)"
  CHANGED="$(printf '%s' "$RESULT" | grep -o '"changed": *true' || true)"

  if [ -z "$CHANGED" ]; then
    echo "[run-performance-rollup] nothing due for rollup right now." >&2
    exit 0
  fi

  apply_result "$RESULT"
  git add "$SUMMARY_FILE"
  git commit -m "performance-summary: rollup ($(date -u +%Y-%m-%dT%H:%M:%SZ))" --quiet

  if git push origin "$BRANCH"; then
    echo "[run-performance-rollup] rollup pushed successfully on attempt $ATTEMPT" >&2
    exit 0
  fi

  echo "[run-performance-rollup] push rejected (likely a concurrent writer) - recomputing fresh and retrying..." >&2
  # Hard reset (not soft): rebase below needs a clean working tree, and this
  # commit only ever contains our own failed-to-push rollup attempt, which
  # is about to be fully recomputed from scratch anyway - nothing here is
  # worth preserving across a retry.
  git reset --hard HEAD~1
  ATTEMPT=$((ATTEMPT + 1))
  sleep $((RANDOM % 3 + 1))
  sync_to_latest
done

echo "ERROR: could not push performance-summary rollup after $MAX_ATTEMPTS attempts." >&2
exit 1
