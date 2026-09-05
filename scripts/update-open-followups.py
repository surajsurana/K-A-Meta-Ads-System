#!/usr/bin/env python3
"""Maintains knowledge/open-followups.json - a small index of learning-log
entries that carry a `follow_up` and haven't been resolved yet (no later
entry's `linked_to` points back at them).

Why this exists (added 2026-09-05, real incident): the daily heartbeat's
"due-now sweep" used to grep the *entire* learning-log.jsonl for every
`follow_up` field, then cross-reference every `linked_to` in the whole file
to work out which ones were still open - recomputed from scratch, every
single day, forever. At 253 entries this already produced a 490KB grep dump
and pushed a single "cheap daily check" close enough to its 20-minute cap
that it got killed by timeout twice in one week. That cost only grows as
the log grows; this index makes the daily cost constant instead, because
the heartbeat can just read this small file instead of re-deriving it.

This script is the *only* thing that writes open-followups.json - never
hand-edit it. Two modes:

    update-open-followups.py --append '<json-line>'
        Fast path, called by append-learning-log.sh right after every real
        append. If the new entry has a follow_up, add it to the index; if
        it has a linked_to, remove whatever it resolves from the index.
        O(1) in the size of the existing log - never re-scans the file.

    update-open-followups.py --rebuild
        Full recompute by scanning the whole learning-log.jsonl once -
        same logic recipe 8 used to run inline, kept here only as a
        bootstrap/repair tool (first-time setup, or recovering from a
        suspected drift), never as part of the daily path.

Both modes are safe to call from either the append-learning-log.sh
commit (same commit as the log entry itself, so the index never drifts
out of sync with what's actually pushed) or standalone.
"""
import json
import os
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(REPO_DIR, "knowledge", "learning-log.jsonl")
INDEX_FILE = os.path.join(REPO_DIR, "knowledge", "open-followups.json")


def load_index():
    if not os.path.exists(INDEX_FILE):
        return {}
    with open(INDEX_FILE, encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def save_index(index):
    # Keyed by id for O(1) add/remove; written as a sorted-by-id object so
    # diffs stay small and reviewable, same spirit as the log's own
    # append-only discipline even though this file IS rewritten in place
    # (it's a derived index, not a source of truth - learning-log.jsonl
    # remains the only append-only record).
    ordered = dict(sorted(index.items()))
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
        f.write("\n")


def apply_entry(index, entry):
    entry_id = entry.get("id")
    if entry.get("follow_up") and entry_id:
        index[entry_id] = {
            "id": entry_id,
            "date": entry.get("date"),
            "type": entry.get("type"),
            "subject": entry.get("subject"),
            "follow_up": entry.get("follow_up"),
        }
    for resolved_id in entry.get("linked_to") or []:
        index.pop(resolved_id, None)


def cmd_append(json_line):
    entry = json.loads(json_line)
    index = load_index()
    apply_entry(index, entry)
    save_index(index)


def cmd_rebuild():
    index = {}
    if not os.path.exists(LOG_FILE):
        save_index(index)
        return
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            apply_entry(index, json.loads(line))
    save_index(index)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--append":
        cmd_append(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "--rebuild":
        cmd_rebuild()
    else:
        print("Usage: update-open-followups.py --append '<json-line>' | --rebuild", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
