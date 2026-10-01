#!/usr/bin/env bash
# Sends an interactive Telegram approval request (inline APPROVE/REJECT/HOLD
# buttons) for one specific, already-validated learning-log `decision` plan.
#
# Usage: scripts/send-telegram-approval.sh <plan-id>
#   e.g. scripts/send-telegram-approval.sh KL-2026-08-21-093000
#
# Called by an agent (headless or interactive) once media-buyer/social-
# community-manager has finished a validated plan that's genuinely ready for
# the user's approval. Does NOT execute anything itself — it only sends the
# message and records that a request is now pending. The actual execution
# happens in scripts/telegram_approval_listener.py, triggered only by a
# verified button tap, never by this script.
#
# Design note: the plan's own `summary` field in the learning log is long,
# technical prose (by design, for a human reading it in a learning-log grep)
# - not fit for a Telegram message (4096 char cap, and unreadable on a phone
# regardless of the cap). This sends a short, human-scannable caption plus
# the plan id, not the full plan text. Full detail is one grep away.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <plan-id>" >&2
  exit 2
fi

PLAN_ID="$1"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [ ! -f telegram_config.txt ]; then
  echo "ERROR: telegram_config.txt not found — cannot send approval request." >&2
  exit 1
fi
# shellcheck disable=SC1091
source telegram_config.txt
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "ERROR: telegram_config.txt is missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID." >&2
  exit 1
fi

# Pull the plan's subject + a short lead-in from its own summary for the
# caption. Keep it short and skimmable — the point is "what is this", not
# "here is the full plan", which is what the buttons + a follow-up grep are
# for.
# -E with an optional-whitespace class after the colon: most entries are
# written as compact JSON ("id":"...") but not all of them are - confirmed
# 2026-08-23 on a real production entry (KL-2026-08-20-170700) written with
# a space ("id": "...") by whatever tool produced it. A rigid no-space-only
# grep silently failed to find a genuine plan and refused to send it.
PLAN_LINE="$(grep -E "\"id\":[[:space:]]*\"${PLAN_ID}\"" knowledge/learning-log.jsonl | tail -1)"
if [ -z "$PLAN_LINE" ]; then
  echo "ERROR: plan id ${PLAN_ID} not found in knowledge/learning-log.jsonl — refusing to send an approval request for a plan that doesn't exist." >&2
  exit 1
fi

SUBJECT="$(printf '%s' "$PLAN_LINE" | jq -r '.subject // "(no subject)"')"
ACTOR="$(printf '%s' "$PLAN_LINE" | jq -r '.actor // "?"')"
TYPE="$(printf '%s' "$PLAN_LINE" | jq -r '.type // "?"')"
if [ "$TYPE" != "decision" ]; then
  echo "ERROR: ${PLAN_ID} is type=${TYPE}, not type=decision — refusing to send an approval request for a non-plan entry." >&2
  exit 1
fi
TELEGRAM_SUMMARY="$(printf '%s' "$PLAN_LINE" | jq -r '.telegram_summary // empty')"

# Plain text, deliberately no parse_mode: plan content is free-form (real
# plans are full of underscored API field names like app_destination/
# video_id/adset_id) and Telegram's legacy Markdown parser throws a hard 400
# on unescaped/unpaired _, *, `, [ characters - confirmed live 2026-08-21 (a
# single "sent_at" in a test summary broke the send outright with "can't
# find end of the entity"). Not worth a MarkdownV2 escaping scheme for a
# caption whose only job is to be skimmable.
if [ -n "$TELEGRAM_SUMMARY" ]; then
  # Preferred path (added 2026-08-23, user feedback - the raw technical
  # summary is unreadable on a phone, one big paragraph, no object names).
  # telegram_summary is written by the plan's own agent specifically for
  # this message - plain English, real line breaks, names the actual
  # campaign/ad set/ad. Used verbatim; the template below only adds a
  # header/footer, it never touches the agent's own formatting.
  CAPTION="🔔 Approval needed

${TELEGRAM_SUMMARY}

Plan ID: ${PLAN_ID}"
else
  # Fallback for entries written before this convention existed (or
  # anything that skipped it) - the old truncated-technical-summary
  # approach, worse but still functional, so an older plan can still be
  # sent rather than blocking on a missing field.
  SUMMARY_SNIPPET="$(printf '%s' "$PLAN_LINE" | jq -r '.summary' | head -c 500)"
  CAPTION="🔔 K&A Meta Ads — plan awaiting your approval
(no plain-English summary was written for this one - showing the raw technical plan instead)

${SUBJECT}
(validated by ${ACTOR})

${SUMMARY_SNIPPET}...

Full detail: grep ${PLAN_ID} knowledge/learning-log.jsonl
Plan id: ${PLAN_ID}"
fi

# Inline keyboard. callback_data stays well under Telegram's 64-byte cap
# (short prefix + plan id). Single source of truth for the prefix scheme -
# telegram_approval_listener.py must use the exact same prefixes.
REPLY_MARKUP=$(jq -n --arg pid "$PLAN_ID" '{
  inline_keyboard: [[
    {text: "✅ APPROVE", callback_data: ("A:" + $pid)},
    {text: "❌ REJECT",  callback_data: ("R:" + $pid)},
    {text: "🕒 HOLD",    callback_data: ("H:" + $pid)}
  ]]
}')

RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode chat_id="${TELEGRAM_CHAT_ID}" \
  --data-urlencode text="${CAPTION}" \
  --data-urlencode reply_markup="${REPLY_MARKUP}")

OK=$(printf '%s' "$RESPONSE" | jq -r '.ok')
if [ "$OK" != "true" ]; then
  echo "ERROR: Telegram sendMessage failed: $RESPONSE" >&2
  exit 1
fi
MESSAGE_ID=$(printf '%s' "$RESPONSE" | jq -r '.result.message_id')

# Record the pending approval request. State lives OUTSIDE the git repo,
# same reasoning as run logs - it's runtime state, not project code, and
# must never be lost to a `git reset --hard`. The listener claims/updates
# entries here under a file lock; this initial write uses the same locked
# helper (via python) for consistency, not a second ad-hoc mechanism.
python3 "$REPO_DIR/scripts/telegram_approval_listener.py" --record-sent \
  --plan-id "$PLAN_ID" \
  --chat-id "$TELEGRAM_CHAT_ID" \
  --message-id "$MESSAGE_ID"

echo "Sent approval request for ${PLAN_ID} (Telegram message_id ${MESSAGE_ID})."
