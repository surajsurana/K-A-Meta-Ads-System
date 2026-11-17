#!/usr/bin/env python3
"""Desk inbox for plain messages Suraj types to the K&A Meta Ads Telegram bot
(added 2026-11-17, real user request - two typed messages were silently
dropped because the listener only understood button taps and product-check
replies).

The listener saves every typed message from the configured chat here and
replies "Got it". A Meta Ads Claude session answers them later, on its own
schedule - no API credits, no unattended answering. Same pattern as
StockTradingBot's desk_inbox.py.

Usage (on the droplet, from the repo root):
  python3 scripts/desk_inbox.py list              # pending messages as JSON
  python3 scripts/desk_inbox.py count             # number pending
  python3 scripts/desk_inbox.py answer <id> --file <path>
        # sends the file's text as a Telegram reply to that message, then
        # marks it answered (only after Telegram accepts it)
  python3 scripts/desk_inbox.py done <id> [--note "..."]
        # marks it handled without sending anything (e.g. answered in the session)

Answering never changes the ad account. A message asking for a change is
raised with Suraj in the session and goes through the usual approval route.
"""
import argparse
import fcntl
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

STATE_DIR = os.path.expanduser("~/ka-meta-ads")
INBOX_FILE = os.path.join(STATE_DIR, "desk_inbox.json")
LOCK_FILE = os.path.join(STATE_DIR, ".desk_inbox.lock")
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TELEGRAM_CONFIG = os.path.join(REPO_DIR, "telegram_config.txt")


def _with_lock(fn):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            items = []
            if os.path.exists(INBOX_FILE):
                with open(INBOX_FILE) as f:
                    content = f.read().strip()
                    items = json.loads(content) if content else []
            new_items, result = fn(items)
            if new_items is not None:
                tmp = INBOX_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(new_items, f, indent=2, ensure_ascii=False)
                os.replace(tmp, INBOX_FILE)
            return result
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def add(chat_id, message_id, text, reply_to_text=None):
    """Called by the listener. Returns the new item's id."""
    def fn(items):
        new_id = str(max([int(i["id"]) for i in items] or [0]) + 1)
        items.append({
            "id": new_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "reply_to_text": reply_to_text,
            "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "pending",
        })
        return items, new_id
    return _with_lock(fn)


def pending():
    return _with_lock(lambda items: (None, [i for i in items if i["status"] == "pending"]))


def _mark(item_id, status, note=None):
    def fn(items):
        for i in items:
            if i["id"] == str(item_id):
                i["status"] = status
                i["handled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                if note:
                    i["note"] = note
                return items, i
        return None, None
    return _with_lock(fn)


def _get(item_id):
    return _with_lock(lambda items: (None, next((i for i in items if i["id"] == str(item_id)), None)))


def _token():
    cfg = {}
    with open(TELEGRAM_CONFIG) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg["TELEGRAM_BOT_TOKEN"]


def answer(item_id, text):
    item = _get(item_id)
    if item is None:
        sys.exit(f"no inbox item {item_id}")
    if item["status"] != "pending":
        sys.exit(f"item {item_id} is already {item['status']}")
    params = {"chat_id": item["chat_id"], "text": text,
              "reply_to_message_id": item["message_id"], "allow_sending_without_reply": "true"}
    req = urllib.request.Request(f"https://api.telegram.org/bot{_token()}/sendMessage",
                                 data=urllib.parse.urlencode(params).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=35) as resp:
        result = json.loads(resp.read().decode())
    if not result.get("ok"):
        sys.exit(f"Telegram refused the answer, item left pending: {result}")
    _mark(item_id, "answered", note=text[:500])
    print(f"answered {item_id}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("count")
    a = sub.add_parser("answer")
    a.add_argument("id")
    a.add_argument("--file", required=True)
    d = sub.add_parser("done")
    d.add_argument("id")
    d.add_argument("--note")
    args = ap.parse_args()
    if args.cmd == "list":
        print(json.dumps(pending(), indent=2, ensure_ascii=False))
    elif args.cmd == "count":
        print(len(pending()))
    elif args.cmd == "answer":
        with open(args.file) as f:
            answer(args.id, f.read().strip())
    elif args.cmd == "done":
        if _mark(args.id, "done", note=args.note) is None:
            sys.exit(f"no inbox item {args.id}")
        print(f"marked {args.id} done")


if __name__ == "__main__":
    main()
