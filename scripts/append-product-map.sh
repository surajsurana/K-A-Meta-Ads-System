#!/usr/bin/env bash
# Safe append-only writer for knowledge/creative-product-map.jsonl.
#
# This is the permanent, one-time-decided lookup table: which real
# Stitchflow/Shopify product(s) does a given ad creative (identified by its
# Meta video_id) actually show. Written once when a creative is matched
# (at build time, or via the Telegram photo-confirmation flow), never
# re-decided automatically afterward - see docs/architecture.md SS3d.
#
# Same safety discipline as scripts/append-learning-log.sh (added
# 2026-09-04, second file needing this exact pattern - see that script's
# own header for the full rationale): fetch, rebase onto latest origin,
# append, commit, push, retry-on-race, never force-push, fail loud not
# silent. Kept as its own script rather than refactoring
# append-learning-log.sh into a shared generic helper - that script is
# live, cron-depended-upon infrastructure and not worth the risk of
# touching for a DRY cleanup; a small amount of duplication here is the
# safer trade.
#
# Usage: scripts/append-product-map.sh '<single-line-json-object>'
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

MAP_FILE="knowledge/creative-product-map.jsonl"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

MAX_ATTEMPTS=5
ATTEMPT=1

while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
  echo "[append-product-map] attempt $ATTEMPT/$MAX_ATTEMPTS" >&2

  git fetch origin "$BRANCH"

  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort || true
    echo "ERROR: rebase onto origin/$BRANCH failed - this means something touched" >&2
    echo "the product map in a way that isn't a pure append (or another kind of" >&2
    echo "conflict). Refusing to auto-resolve. Manual review required." >&2
    exit 3
  fi

  printf '%s\n' "$JSON_LINE" >> "$MAP_FILE"

  git add "$MAP_FILE"
  git commit -m "product-map: append entry ($(date -u +%Y-%m-%dT%H:%M:%SZ))" --quiet

  if git push origin "$BRANCH"; then
    echo "[append-product-map] pushed successfully on attempt $ATTEMPT" >&2
    exit 0
  fi

  echo "[append-product-map] push rejected (likely a concurrent writer) - retrying..." >&2
  ATTEMPT=$((ATTEMPT + 1))
  sleep $((RANDOM % 3 + 1))
done

echo "ERROR: could not push product-map entry after $MAX_ATTEMPTS attempts." >&2
echo "The commit exists locally but is NOT on GitHub. Do not treat this entry" >&2
echo "as logged. Resolve manually (git status / git log) before continuing," >&2
echo "and do not run 'git reset --hard' on this checkout until it's pushed." >&2
exit 1
