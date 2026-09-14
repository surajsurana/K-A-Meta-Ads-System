#!/usr/bin/env bash
# Sends ONE Telegram approval request with FOUR buttons - "Feed", "Story",
# "Reject" (both), "Hold" (both) - for a UGC/content repost where the same
# asset+caption could go to either destination and Suraj should choose at
# approval time, not have social-community-manager pick for him unasked.
# Added 2026-09-14, real user request: "I want an option to post on story
# or feed for such things... sometimes I want to repost but on story and
# not in my feed."
#
# Usage: scripts/send-telegram-approval-repost-choice.sh <feed-plan-id> <story-plan-id>
#   e.g. scripts/send-telegram-approval-repost-choice.sh KL-2026-09-14-101500 KL-2026-09-14-101512
#
# Prerequisite: social-community-manager must have already written TWO
# sibling type=decision learning-log entries for the SAME content - one
# whose plan posts to Feed, one whose plan posts to Story - each carrying
# the other's id in its own "paired_plan_id" field (this is what lets
# telegram_approval_listener.py's dispatch_execution refuse to post the
# same content twice if both ever somehow got approved). This script
# verifies that pairing exists before sending - it will NOT send two
# unrelated plans side by side just because you pass two ids.
#
# Tapping "Feed" or "Story" runs that ONE sibling's plan through the
# completely ordinary single-plan approve path (scripts/telegram_approval_listener.py's
# ordinary "A:<id>" callback - no special-casing needed there at all).
# Tapping "Reject" or "Hold" resolves BOTH siblings at once via the new
# "RB:"/"HB:" paired callback prefixes, so the untapped destination's plan
# never sits dangling pending forever.

set -euo pipefail

if [ $# -ne 2 ]; then
  echo "Usage: $0 <feed-plan-id> <story-plan-id>" >&2
  exit 2
fi

FEED_ID="$1"
STORY_ID="$2"
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

FEED_LINE="$(grep -E "\"id\":[[:space:]]*\"${FEED_ID}\"" knowledge/learning-log.jsonl | tail -1)"
STORY_LINE="$(grep -E "\"id\":[[:space:]]*\"${STORY_ID}\"" knowledge/learning-log.jsonl | tail -1)"
if [ -z "$FEED_LINE" ]; then
  echo "ERROR: plan id ${FEED_ID} not found in knowledge/learning-log.jsonl." >&2
  exit 1
fi
if [ -z "$STORY_LINE" ]; then
  echo "ERROR: plan id ${STORY_ID} not found in knowledge/learning-log.jsonl." >&2
  exit 1
fi

FEED_TYPE="$(printf '%s' "$FEED_LINE" | jq -r '.type // "?"')"
STORY_TYPE="$(printf '%s' "$STORY_LINE" | jq -r '.type // "?"')"
if [ "$FEED_TYPE" != "decision" ] || [ "$STORY_TYPE" != "decision" ]; then
  echo "ERROR: both ids must be type=decision entries (got ${FEED_TYPE} / ${STORY_TYPE})." >&2
  exit 1
fi

# Verify the two plans actually reference each other - refuses to send two
# unrelated plans as if they were a genuine either/or choice.
FEED_PAIR="$(printf '%s' "$FEED_LINE" | jq -r '.paired_plan_id // empty')"
STORY_PAIR="$(printf '%s' "$STORY_LINE" | jq -r '.paired_plan_id // empty')"
if [ "$FEED_PAIR" != "$STORY_ID" ] || [ "$STORY_PAIR" != "$FEED_ID" ]; then
  echo "ERROR: ${FEED_ID} and ${STORY_ID} don't reference each other via paired_plan_id - refusing to send them as a Feed/Story choice. (Feed's paired_plan_id=${FEED_PAIR:-<none>}, Story's paired_plan_id=${STORY_PAIR:-<none>})" >&2
  exit 1
fi

# Destination-neutral caption body: each sibling's own telegram_summary
# starts with "Instagram: Feed post" / "Instagram: Story" per hard rule 5 in
# social-community-manager.md - strip that first line (and the blank line
# after it) so the shared caption doesn't pre-announce one destination while
# also offering the other as a button. The actual content description
# (what it shows, why it's worth reposting) is identical on both siblings by
# construction, so either one's remainder is fine to use - Feed's is used
# here arbitrarily.
FEED_SUMMARY="$(printf '%s' "$FEED_LINE" | jq -r '.telegram_summary // empty')"
if [ -z "$FEED_SUMMARY" ]; then
  echo "ERROR: ${FEED_ID} has no telegram_summary - cannot build a caption." >&2
  exit 1
fi
CONTENT_BODY="$(printf '%s\n' "$FEED_SUMMARY" | tail -n +3)"

CAPTION="🔔 Approval needed — choose Feed or Story

${CONTENT_BODY}

Feed plan: ${FEED_ID}
Story plan: ${STORY_ID}"

REPLY_MARKUP=$(jq -n --arg feed "$FEED_ID" --arg story "$STORY_ID" '{
  inline_keyboard: [
    [
      {text: "✅ Feed",  callback_data: ("A:" + $feed)},
      {text: "✅ Story", callback_data: ("A:" + $story)}
    ],
    [
      {text: "❌ Reject", callback_data: ("RB:" + $feed + "|" + $story)},
      {text: "🕒 Hold",   callback_data: ("HB:" + $feed + "|" + $story)}
    ]
  ]
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

# Record BOTH plan ids as pending against the same message - the listener's
# state store is keyed by plan_id, not message_id, so this is safe: whichever
# button gets tapped resolves via its own plan_id lookup.
python3 "$REPO_DIR/scripts/telegram_approval_listener.py" --record-sent \
  --plan-id "$FEED_ID" --chat-id "$TELEGRAM_CHAT_ID" --message-id "$MESSAGE_ID"
python3 "$REPO_DIR/scripts/telegram_approval_listener.py" --record-sent \
  --plan-id "$STORY_ID" --chat-id "$TELEGRAM_CHAT_ID" --message-id "$MESSAGE_ID"

echo "Sent Feed/Story choice for ${FEED_ID} (Feed) / ${STORY_ID} (Story) (Telegram message_id ${MESSAGE_ID})."
