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
#   scripts/send-product-id-check.sh <video_id> <ad_frame_image_path> <candidate_image_path_or_NONE> <candidate_sku_or_NONE> <candidate_name>
#
# If no Shopify/Stitchflow candidate photo could be found at all (checked
# both sources, neither had one), pass NONE for candidate_image_path - the
# message still goes out with just the ad frame and a text-only question
# asking Suraj to identify it from the name/description alone.

set -euo pipefail

if [ $# -ne 5 ]; then
  echo "Usage: $0 <video_id> <ad_frame_image_path> <candidate_image_path_or_NONE> <candidate_sku_or_NONE> <candidate_name>" >&2
  exit 2
fi

VIDEO_ID="$1"
AD_FRAME_PATH="$2"
CANDIDATE_IMAGE_PATH="$3"
CANDIDATE_SKU="$4"
CANDIDATE_NAME="$5"

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

# Send the photo(s) first. sendMediaGroup requires 2+ items and doesn't
# support inline keyboards on the group itself - buttons go on a separate
# follow-up sendMessage, which is also what carries the question text and
# is the message check-id tracking + reply-matching is anchored to.
if [ "$CANDIDATE_IMAGE_PATH" != "NONE" ] && [ -f "$CANDIDATE_IMAGE_PATH" ]; then
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
else
  # No candidate photo found anywhere (Shopify checked, Stitchflow checked,
  # neither had one) - still send the ad frame alone so Suraj has something
  # to look at, and ask him to name it outright.
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendPhoto" \
    -F "chat_id=${TELEGRAM_CHAT_ID}" \
    -F "photo=@${AD_FRAME_PATH}" \
    -F "caption=From the ad video (${VIDEO_ID}) - no matching photo found in Shopify or Stitchflow to compare against." \
    > /tmp/pid_mediagroup_response.json
  QUESTION_TEXT="What product is this? No catalog match was found automatically - please reply with the product name (or SKU if you know it).

Ad video: ${VIDEO_ID}

Check ID: ${CHECK_ID}"
fi

# The buttons + tracked message. --data-urlencode throughout (never plain
# -d) - same reason as every other Telegram send in this system: a literal
# & in real text (product names, brand copy) would silently truncate the
# message under application/x-www-form-urlencoded semantics otherwise.
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

MESSAGE_ID=$(printf '%s' "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['message_id'])" 2>/dev/null || \
             printf '%s' "$RESPONSE" | py -c "import json,sys; print(json.load(sys.stdin)['result']['message_id'])")

if [ -z "$MESSAGE_ID" ]; then
  echo "ERROR: could not parse message_id from Telegram response: $RESPONSE" >&2
  exit 1
fi

python3 "$REPO_DIR/scripts/telegram_approval_listener.py" --record-product-check \
  --check-id "$CHECK_ID" --chat-id "$TELEGRAM_CHAT_ID" --message-id "$MESSAGE_ID" \
  --video-id "$VIDEO_ID" --candidate-sku "${CANDIDATE_SKU:-NONE}" --candidate-name "$CANDIDATE_NAME" \
  || py "$REPO_DIR/scripts/telegram_approval_listener.py" --record-product-check \
     --check-id "$CHECK_ID" --chat-id "$TELEGRAM_CHAT_ID" --message-id "$MESSAGE_ID" \
     --video-id "$VIDEO_ID" --candidate-sku "${CANDIDATE_SKU:-NONE}" --candidate-name "$CANDIDATE_NAME"

echo "Sent product-id check $CHECK_ID for video $VIDEO_ID (message_id=$MESSAGE_ID)"
