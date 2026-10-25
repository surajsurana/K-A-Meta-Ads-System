#!/usr/bin/env python3
"""Match Instagram-collected Media Library items to "Your uploads" (backlog #13).

Added 2026-10-25, Suraj: "can we match the our stories and found on
instagram content to your uploads content. then the missing content in your
uploads can be marked on that card and filtered out also so we can get that
uploaded". Each item in the Our Stories / Found on Instagram tabs is marked
"in Your uploads" or "not in Your uploads", so the team knows which originals
to upload, and the ads system uses the uploaded file (a collected video whose
file Instagram withheld can never be an ad on its own; see media-buyer.md).

How: a 256-bit difference hash (dHash 16x16) on images and on frames taken
across each video (ffmpeg, one frame a second, up to MAX_FRAMES), skipping
blank/near-solid frames (a black frame otherwise "matches" every other black
frame). A collected item is in Your uploads when at least MIN_AGREEING of its
frames are within FRAME_MATCH bits of some frame of the same upload, or, for a
single photo/cover, when it's within SINGLE_MATCH bits.
Calibrated 2026-10-25 on the live library: an upload re-encoded, cropped 8%
and resized still scored 18 (5 frames agreeing); a trimmed, brightened clip
scored 5 (8/8 frames); different videos never came below ~84, mostly 95-130.
The 64-bit aHash used by find-duplicate-creative.py was tried first and
produced false matches here (one blank frame; two different corsets at 8).

Results go to ~/ka-meta-ads-dashboard/media-library/matches.json, a separate
file from index.json so the dashboard's own saves can never wipe them:
  {"<collected item id>": {"upload": "<upload id>" | null, "distance": n,
                           "checked_at": "...", "manual": true|false}}
"manual" entries come from the dashboard's "Already uploaded" button and are
never overwritten by the automatic match.

Runs nightly right after scripts/instagram_collect.py, and in the background
right after any upload. Uses the ka-autopost venv (Pillow):
  ~/venvs/ka-autopost/bin/python3 scripts/media_library_match.py
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

LIB = os.path.expanduser("~/ka-meta-ads-dashboard/media-library")
INDEX = os.path.join(LIB, "index.json")
MATCHES = os.path.join(LIB, "matches.json")
CACHE = os.path.expanduser("~/ka-meta-ads/media_hash_cache.json")
FRAME_MATCH = 40      # of 256 bits
SINGLE_MATCH = 30
MIN_AGREEING = 2
BLANK_STDDEV = 15
MAX_FRAMES = 40
HASH_VERSION = "dhash16-v2"


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def dhash(img):
    """None for a blank/near-solid frame, else a 256-bit difference hash."""
    from PIL import Image, ImageStat
    g = img.convert("L")
    if ImageStat.Stat(g).stddev[0] < BLANK_STDDEV:
        return None
    px = list(g.resize((17, 16), Image.LANCZOS).getdata())
    v = 0
    for r in range(16):
        for c in range(16):
            if px[r * 17 + c] > px[r * 17 + c + 1]:
                v |= 1 << (r * 16 + c)
    return v


def image_hashes(path):
    from PIL import Image
    with Image.open(path) as im:
        h = dhash(im)
    return [h] if h is not None else []


def video_hashes(path):
    from PIL import Image
    out = []
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-vf", "fps=1,scale=160:-1",
                        "-frames:v", str(MAX_FRAMES), os.path.join(d, "f%03d.png")],
                       timeout=180, check=False)
        for n in sorted(os.listdir(d)):
            with Image.open(os.path.join(d, n)) as im:
                h = dhash(im)
            if h is not None:
                out.append(h)
    return out


def hashes_for(item, cache):
    """All hashes for one library item: its file (image or video frames) plus
    its cover/thumbnail. Cached by filename + size + mtime."""
    files = [f for f in (item.get("filename"), item.get("thumbnail")) if f]
    key_parts, paths = [], []
    for f in files:
        p = os.path.join(LIB, f)
        if os.path.exists(p):
            st = os.stat(p)
            key_parts.append("{}:{}:{}".format(f, st.st_size, int(st.st_mtime)))
            paths.append(p)
    key = HASH_VERSION + "|" + "|".join(key_parts)
    if not key:
        return []
    if cache.get(item["id"], {}).get("key") == key:
        return cache[item["id"]]["h"]
    hs = []
    for p in paths:
        try:
            if p.lower().endswith((".mp4", ".mov", ".webm", ".m4v")):
                hs += video_hashes(p)
            else:
                hs += image_hashes(p)
        except Exception as e:  # unreadable file: no hashes, reported as unmatched
            print("WARN could not hash {}: {}".format(os.path.basename(p), e))
    cache[item["id"]] = {"key": key, "h": hs}
    return hs


def distance(a, b):
    return bin(a ^ b).count("1")


def main():
    index = load(INDEX, [])
    matches = load(MATCHES, {})
    cache = load(CACHE, {})
    uploads = [it for it in index if it.get("source") != "instagram_auto"]
    collected = [it for it in index if it.get("source") == "instagram_auto"]
    up_hashes = {u["id"]: hashes_for(u, cache) for u in uploads}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    found = 0
    for it in collected:
        if matches.get(it["id"], {}).get("manual"):
            continue
        hs = hashes_for(it, cache)
        hit, hit_score = None, None
        for uid, uh in up_hashes.items():
            if not hs or not uh:
                continue
            mins = [min(distance(a, b) for b in uh) for a in hs]
            agreeing = sum(1 for m in mins if m <= FRAME_MATCH)
            ok = agreeing >= MIN_AGREEING or (len(hs) <= 2 and min(mins) <= SINGLE_MATCH)
            if ok and (hit_score is None or min(mins) < hit_score):
                hit, hit_score = uid, min(mins)
        found += bool(hit)
        matches[it["id"]] = {"upload": hit, "distance": hit_score,
                             "checked_at": now, "manual": False}
    # forget entries for items that no longer exist
    ids = {it["id"] for it in index}
    matches = {k: v for k, v in matches.items() if k in ids}
    cache = {k: v for k, v in cache.items() if k in ids}
    save(MATCHES, matches)
    save(CACHE, cache)
    print("{} collected item(s) checked against {} upload(s): {} in Your uploads, {} missing".format(
        len(collected), len(uploads), found + sum(1 for it in collected if matches.get(it["id"], {}).get("manual")
                                                 and matches[it["id"]].get("upload")),
        sum(1 for it in collected if not matches.get(it["id"], {}).get("upload"))))


if __name__ == "__main__":
    sys.exit(main())
