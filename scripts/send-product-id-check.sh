#!/usr/bin/env bash
# Sends a product-identification confirmation to Telegram: two photos side
# by side (the ad's actual video frame, and the best-candidate product photo
# found in Shopify/Stitchflow) plus Confirm/Reject buttons, so Suraj can
# approve, reject, or reply with the correct product name. See
# docs/architecture.md SS3d for the full design and why this exists.
#
# IMPORTANT - this script does NOT do the product lookup itself. Finding the
# candidate photo requires Shopify MCP access (search_products / get_product),
# which is only available to an interactive Claude session, never to the
# droplet's headless runs (documented gap - see scripts/_run-common.sh). So
# the calling session must already have done that lookup and saved both
# images locally before calling this script - this script only handles
# sending the already-resolved images to Telegram and recording state.
#
# Usage:
#   scripts/send-product-id-check.sh <video_id> <ad_frame_image_path> <candidate_image_path_or_NONE> <candidate_sku_or_NONE> <candidate_name> [comma_separated_ad_ids]
#
# If no Shopify/Stitchflow candidate photo could be found at all (checked
# both sources, neither had one), pass NONE for candidate_image_path - the
# message goes out as the ad frame alone with a forced-reply prompt asking
# Suraj to name it outright (no Correct/Wrong buttons in this case - fixed
# 2026-09-04, there's nothing to confirm/reject when no guess was offered
# at all, showing those buttons anyway was a real, confusing bug).
#
# ad_ids (added 2026-09-04, real gap - earlier confirmations recorded
# ad_ids: [] because this was never threaded through at all, which breaks
# the per-product spend rollup, SS3d, since it needs to know which ads'
# spend belongs to which product) - pass every ad_id currently using this
# video_id, comma-separated, no spaces. Optional; omit if genuinely unknown.

set -euo pipefail

if [ $# -lt 5 ] || [ $# -gt 6 ]; then
  echo "Usage: $0 <video_id> <ad_frame_image_path> <candidate_image_path_or_NONE> <candidate_sku_or_NONE> <candidate_name> [comma_separated_ad_ids]" >&2
  exit 2
fi

VIDEO_ID="$1"
AD_FRAME_PATH="$2"
CANDIDATE_IMAGE_PATH="$3"
CANDIDATE_SKU="$4"
CANDIDATE_NAME="$5"
AD_IDS="${6:-}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [ ! -f telegram_config.txt ]; then
  echo "ERROR: telegram_config.txt not found - cannot send product-id check." >&2
  exit 1
fi
# shellcheck disable=SC1091
source telegram_config.txt
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "ERROR: telegram_config.txt is missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID." >&2
  exit 1
fi

if [ ! -f "$AD_FRAME_PATH" ]; then
  echo "ERROR: ad frame image not found at $AD_FRAME_PATH" >&2
  exit 2
fi

CHECK_ID="PID-$(date -u +%Y%m%d-%H%M%S)"

# Two structurally different cases (fixed 2026-09-04, real user-caught bug:
# a no-candidate message was still showing a meaningless "Correct" button -
# correct compared to WHAT? There was no guess being offered at all, so
# tapping it would have saved the placeholder "please identify" text as if
# it were a real product name).

if [ "$CANDIDATE_IMAGE_PATH" != "NONE" ] && [ -f "$CANDIDATE_IMAGE_PATH" ]; then
  # Case A: a real candidate exists - two photos + Correct/Wrong buttons on
  # a separate follow-up message (sendMediaGroup doesn't support inline
  # keyboards on the group itself).
  MEDIA_JSON=$(cat <<EOF
[
  {"type":"photo","media":"attach://ad_frame","caption":"📱 META - what the ad actually shows"},
  {"type":"photo","media":"attach://candidate","caption":"🗂️ OUR CATALOG - best guess: ${CANDIDATE_NAME}"}
]
EOF
)
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMediaGroup" \
    -F "chat_id=${TELEGRAM_CHAT_ID}" \
    -F "media=${MEDIA_JSON}" \
    -F "ad_frame=@${AD_FRAME_PATH}" \
    -F "candidate=@${CANDIDATE_IMAGE_PATH}" \
    > /tmp/pid_mediagroup_response.json
  QUESTION_TEXT="Is this the same product?

Ad video: ${VIDEO_ID}
Best guess: ${CANDIDATE_NAME}${CANDIDATE_SKU:+ (${CANDIDATE_SKU})}

Check ID: ${CHECK_ID}"

  REPLY_MARKUP=$(python3 -c "
import json
print(json.dumps({'inline_keyboard': [[
    {'text': '✅ Correct', 'callback_data': 'PY:${CHECK_ID}'},
    {'text': '❌ Wrong - let me tell you', 'callback_data': 'PN:${CHECK_ID}'}
]]}))
" 2>/dev/null || py -c "
import json
print(json.dumps({'inline_keyboard': [[
    {'text': '✅ Correct', 'callback_data': 'PY:${CHECK_ID}'},
    {'text': '❌ Wrong - let me tell you', 'callback_data': 'PN:${CHECK_ID}'}
]]}))
")

  RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${QUESTION_TEXT}" \
    --data-urlencode "reply_markup=${REPLY_MARKUP}")
  INITIAL_STATUS="pending"
else
  # Case B: no candidate at all - nothing to confirm/reject, so don't offer
  # those buttons. Go straight to a forced reply on the photo itself asking
  # Suraj to name it - one message, no confusing middle step.
  QUESTION_TEXT="What product is this? No catalog match was found automatically - reply below with the product name (or SKU if you know it).

Ad video: ${VIDEO_ID}

Check ID: ${CHECK_ID}"

  REPLY_MARKUP=$(python3 -c "
import json
print(json.dumps({'force_reply': True, 'input_field_placeholder': 'Product name or SKU'}))
" 2>/dev/null || py -c "
import json
print(json.dumps({'force_reply': True, 'input_field_placeholder': 'Product name or SKU'}))
")

  RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendPhoto" \
    -F "chat_id=${TELEGRAM_CHAT_ID}" \
    -F "photo=@${AD_FRAME_PATH}" \
    -F "caption=${QUESTION_TEXT}" \
    -F "reply_markup=${REPLY_MARKUP}")
  INITIAL_STATUS="awaiting_correction"
fi

MESSAGE_ID=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['message_id'])" 2>/dev/null || \
             printf '%s' "$RESPONSE" | py -c "import json,sys; print(json.load(sys.stdin)['result']['message_id'])")

if [ -z "$MESSAGE_ID" ]; then
  echo "ERROR: could not parse message_id from Telegram response: $RESPONSE" >&2
  exit 1
fi

python3 "$REPO_DIR/scripts/telegram_approval_listener.py" --record-product-check \
  --check-id "$CHECK_ID" --chat-id "$TELEGRAM_CHAT_ID" --message-id "$MESSAGE_ID" \
  --video-id "$VIDEO_ID" --candidate-sku "${CANDIDATE_SKU:-NONE}" --candidate-name "$CANDIDATE_NAME" --ad-ids "$AD_IDS" --initial-status "$INITIAL_STATUS" \
  || py "$REPO_DIR/scripts/telegram_approval_listener.py" --record-product-check \
     --check-id "$CHECK_ID" --chat-id "$TELEGRAM_CHAT_ID" --message-id "$MESSAGE_ID" \
     --video-id "$VIDEO_ID" --candidate-sku "${CANDIDATE_SKU:-NONE}" --candidate-name "$CANDIDATE_NAME" --ad-ids "$AD_IDS" --initial-status "$INITIAL_STATUS"

echo "Sent product-id check $CHECK_ID for video $VIDEO_ID (message_id=$MESSAGE_ID)"
