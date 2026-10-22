#!/usr/bin/env python3
"""Post the 🆘 Lead quality SOS button in Suraj's K&A Telegram chat and pin
it, so it's always one tap away (added 2026-10-22). Run once on the droplet,
where telegram_config.txt lives:
  python3 scripts/send_sos_button.py
Re-running posts a fresh button and pins that one instead; old buttons keep
working too (the callback carries no state). The press itself is handled by
handle_sos_callback() in scripts/telegram_approval_listener.py.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telegram_approval_listener import load_telegram_config, tg_api  # noqa: E402

TEXT = ("🆘 Lead quality SOS\n\n"
        "Press this when enquiry quality suddenly looks bad (lots of useless chats). "
        "The system checks which ads and placements the bad enquiries came from and what changed, "
        "and replies here within about 30 minutes: routine, or a fix for your approval.\n\n"
        "It only investigates. It never changes an ad by itself.")


def main():
    token, chat_id = load_telegram_config()
    markup = {"inline_keyboard": [[{"text": "🆘 Lead quality SOS", "callback_data": "SOS:lead-quality"}]]}
    sent = tg_api(token, "sendMessage", {"chat_id": chat_id, "text": TEXT, "reply_markup": json.dumps(markup)})
    if not sent.get("ok"):
        print("send failed:", sent.get("description"))
        sys.exit(1)
    mid = sent["result"]["message_id"]
    pinned = tg_api(token, "pinChatMessage", {"chat_id": chat_id, "message_id": mid, "disable_notification": True})
    print("sent message", mid, "| pinned:", pinned.get("ok"), pinned.get("description", ""))


if __name__ == "__main__":
    main()
