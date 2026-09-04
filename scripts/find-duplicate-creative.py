#!/usr/bin/env python3
"""Checks whether a candidate ad video is visually the same footage as one
already mapped in knowledge/creative-product-map.jsonl, even if Meta gave it
a different video_id - which happens when the same source file gets
uploaded twice (once per ad) instead of one ad creative referencing another
ad's existing video_id. See docs/architecture.md SS3d.

Real incident this exists for (2026-09-04): "Every Crystal Placed by Hand"
and "First Love | Three Expressions" are two different ads, but both were
built from the exact same source video, uploaded separately - two different
Meta video_ids, byte-different re-encodes, but the same content. The
existing "group by video_id" logic (which already handles multiple ad_ids
correctly sharing ONE video_id) had no way to catch this, so the same
product-identification question got asked twice.

Why perceptual hashing, not exact byte comparison: two uploads of "the same"
video are re-encoded independently by Meta, so their thumbnail JPEGs are
byte-different even for an identical frame (confirmed live: 432459 bytes vs
437292 bytes for what is visibly the same shot). An average-hash (aHash) is
tolerant of that - shrink to a small grayscale grid, threshold against the
mean, compare Hamming distance.

Why this runs interactively, never in the always-on listener: needs Pillow,
which is deliberately not installed on the droplet (telegram_approval_
listener.py stays stdlib-only by design - see docs/architecture.md). This
also fits the existing rule that identification work only happens in an
interactive session (creative-copywriter.md) - never headless.

Usage:
    python find-duplicate-creative.py <candidate_video_id>

Prints one JSON object to stdout:
    {"match": true, "matched_video_id": "...", "distance": 3,
     "products": [...], "ad_ids": [...], "status": "confirmed"}
  or
    {"match": false, "closest_video_id": "...", "closest_distance": 24}
  or, if the map is empty / candidate thumbnail can't be fetched:
    {"match": false, "error": "..."}

A `distance` of 0-8 (out of 64 bits) is treated as the same footage -
tuned from the real case above (distance 2) with headroom; genuinely
different images in this catalog run 20+ apart. Caller decides what to do
with a match - this script only reports, it never writes to the map or
touches Telegram itself.
"""
import json
import os
import sys
import urllib.request
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    print(json.dumps({"match": False, "error": "Pillow not installed - run `pip install Pillow` in this interactive session (never on the droplet)"}))
    sys.exit(1)

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_FILE = os.path.join(REPO_DIR, "knowledge", "creative-product-map.jsonl")
TOKEN_FILE = os.path.join(REPO_DIR, "meta_token.txt.txt")
HASH_SIZE = 8  # 8x8 = 64-bit hash
MATCH_THRESHOLD = 8  # Hamming distance, out of 64 bits

SKIP_STATUSES = {"pending_telegram_confirmation"}  # not yet a trustworthy answer to propagate


def load_token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def fetch_preferred_thumbnail_bytes(video_id, token):
    url = f"https://graph.facebook.com/v21.0/{video_id}/thumbnails?access_token={token}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read())
    items = data.get("data", [])
    if not items:
        return None
    preferred = [t for t in items if t.get("is_preferred")]
    chosen = preferred[0] if preferred else items[0]
    with urllib.request.urlopen(chosen["uri"], timeout=20) as resp:
        return resp.read()


def ahash(image_bytes):
    img = Image.open(BytesIO(image_bytes)).convert("L").resize(
        (HASH_SIZE, HASH_SIZE), Image.LANCZOS
    )
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for p in pixels:
        bits = (bits << 1) | (1 if p >= avg else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def latest_entries_by_video_id():
    """Same rule as everywhere else this map is read: last line per
    video_id wins (docs/architecture.md SS3d)."""
    latest = {}
    if not os.path.exists(MAP_FILE):
        return latest
    with open(MAP_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            latest[entry["video_id"]] = entry
    return latest


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"match": False, "error": "usage: find-duplicate-creative.py <candidate_video_id>"}))
        sys.exit(2)
    candidate_video_id = sys.argv[1]
    token = load_token()

    candidate_bytes = fetch_preferred_thumbnail_bytes(candidate_video_id, token)
    if candidate_bytes is None:
        print(json.dumps({"match": False, "error": f"no thumbnails available for video_id={candidate_video_id}"}))
        sys.exit(1)
    candidate_hash = ahash(candidate_bytes)

    known = latest_entries_by_video_id()
    known.pop(candidate_video_id, None)  # never match against itself if it's already mapped

    best_video_id, best_distance, best_entry = None, None, None
    for video_id, entry in known.items():
        if entry.get("status") in SKIP_STATUSES:
            continue
        try:
            known_bytes = fetch_preferred_thumbnail_bytes(video_id, token)
        except Exception:
            continue  # a since-deleted/expired video shouldn't crash the whole scan
        if known_bytes is None:
            continue
        distance = hamming(candidate_hash, ahash(known_bytes))
        if best_distance is None or distance < best_distance:
            best_video_id, best_distance, best_entry = video_id, distance, entry

    if best_video_id is not None and best_distance <= MATCH_THRESHOLD:
        print(json.dumps({
            "match": True,
            "matched_video_id": best_video_id,
            "distance": best_distance,
            "products": best_entry.get("products", []),
            "ad_ids": best_entry.get("ad_ids", []),
            "status": best_entry.get("status"),
        }))
    elif best_video_id is not None:
        print(json.dumps({"match": False, "closest_video_id": best_video_id, "closest_distance": best_distance}))
    else:
        print(json.dumps({"match": False, "error": "creative-product-map.jsonl has no comparable entries yet"}))


if __name__ == "__main__":
    main()
