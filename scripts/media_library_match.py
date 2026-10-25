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

Also compared against (added 2026-10-25, Suraj: "some items can be in our
instagram feed as reels or content available to ads system somehow. so there
is no need to upload that"): our recent Instagram feed posts/Reels
(FEED_LIMIT) and the most recent videos/images already in the Meta ad account
(ADS_LIMIT). Those files are downloaded to a temp folder only long enough to
fingerprint them, one at a time, then deleted; only the fingerprints are
cached. An item found there is not "missing": the ads system can use it
from Meta directly.

Results go to ~/ka-meta-ads-dashboard/media-library/matches.json, a separate
file from index.json so the dashboard's own saves can never wipe them:
  {"<collected item id>": {"upload": "<upload id | feed permalink | ad video/image id>" | null,
                           "where": "uploads" | "feed" | "ad_account" | null,
                           "label": "<what to show>", "distance": n,
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
import urllib.error
import urllib.parse
import urllib.request
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
REPO = os.environ.get("KA_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = "https://graph.facebook.com/v21.0"
IG = "17841401625784277"
AD_ACCOUNT = "act_686170454752172"
FEED_LIMIT = 50
ADS_LIMIT = 60
MAX_DOWNLOAD_MB = 120


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


def graph(path, params, tok):
    url = "{}/{}?{}".format(GRAPH, path, urllib.parse.urlencode(dict(params, access_token=tok)))
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print("WARN Meta read failed for {}: {}".format(path.split("?")[0], e))
        return {}


def meta_sources(tok):
    """Content the ads system can already use from Meta directly:
    [(cache key, where, label, ref, [(url, is_video), ...])]."""
    out = []
    d = graph(IG + "/media", {"limit": FEED_LIMIT, "fields": "id,media_type,media_url,thumbnail_url,permalink,timestamp,"
                                                          "children{media_type,media_url,thumbnail_url}"}, tok)
    for m in d.get("data", []):
        parts = (m.get("children") or {}).get("data") or [m]
        urls = []
        for c in parts:
            if c.get("media_type") == "VIDEO":
                if c.get("media_url"):
                    urls.append((c["media_url"], True))
                if c.get("thumbnail_url"):
                    urls.append((c["thumbnail_url"], False))
            elif c.get("media_url"):
                urls.append((c["media_url"], False))
        if urls:
            when = (m.get("timestamp") or "")[:10]
            out.append(("feed:" + m["id"], "feed", "On our Instagram feed ({})".format(when),
                        m.get("permalink", ""), urls))
    d = graph(AD_ACCOUNT + "/advideos", {"limit": ADS_LIMIT, "fields": "id,title,source,picture,created_time"}, tok)
    for v in d.get("data", []):
        urls = [(v["source"], True)] if v.get("source") else []
        if v.get("picture"):
            urls.append((v["picture"], False))
        if urls:
            out.append(("adv:" + v["id"], "ad_account", "In the ad account: " + (v.get("title") or "video " + v["id"])[:60],
                        v["id"], urls))
    d = graph(AD_ACCOUNT + "/adimages", {"limit": ADS_LIMIT, "fields": "hash,name,url,created_time"}, tok)
    for im in d.get("data", []):
        if im.get("url"):
            out.append(("adi:" + im["hash"], "ad_account", "In the ad account: " + (im.get("name") or "image")[:60],
                        im["hash"], [(im["url"], False)]))
    return out


def remote_hashes(urls):
    """Download each file to a temp folder, fingerprint it, delete it."""
    hs = []
    with tempfile.TemporaryDirectory() as d:
        for i, (url, is_video) in enumerate(urls):
            path = os.path.join(d, "f{}{}".format(i, ".mp4" if is_video else ".jpg"))
            try:
                with urllib.request.urlopen(url, timeout=180) as r:
                    size = int(r.headers.get("Content-Length") or 0)
                    if size > MAX_DOWNLOAD_MB * 1024 * 1024:
                        continue
                    with open(path, "wb") as f:
                        while True:
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                hs += video_hashes(path) if is_video else image_hashes(path)
            except Exception as e:
                print("WARN could not fingerprint a Meta file: {}".format(str(e)[:120]))
            finally:
                if os.path.exists(path):
                    os.remove(path)
    return hs


def is_match(hs, other):
    if not hs or not other:
        return None
    mins = [min(distance(a, b) for b in other) for a in hs]
    agreeing = sum(1 for m in mins if m <= FRAME_MATCH)
    if agreeing >= MIN_AGREEING or (len(hs) <= 2 and min(mins) <= SINGLE_MATCH):
        return min(mins)
    return None


def distance(a, b):
    return bin(a ^ b).count("1")


def main():
    index = load(INDEX, [])
    matches = load(MATCHES, {})
    cache = load(CACHE, {})
    uploads = [it for it in index if it.get("source") != "instagram_auto"]
    collected = [it for it in index if it.get("source") == "instagram_auto"]
    # candidates, in order of preference: Your uploads first, then Meta
    cands = [(u["id"], "uploads", "In Your uploads", hashes_for(u, cache)) for u in uploads]
    ext_keys = set()
    try:
        with open(os.path.join(REPO, "meta_token.txt.txt"), encoding="utf-8") as f:
            tok = f.read().strip()
    except OSError:
        tok = None
        print("WARN no Meta token - comparing against Your uploads only")
    for key, where, label, ref, urls in (meta_sources(tok) if tok and collected else []):
        ext_keys.add(key)
        ent = cache.get(key)
        if not ent or ent.get("key") != HASH_VERSION:
            ent = {"key": HASH_VERSION, "h": remote_hashes(urls)}
            cache[key] = ent
        cands.append((ref, where, label, ent["h"]))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    found = 0
    for it in collected:
        if matches.get(it["id"], {}).get("manual"):
            continue
        hs = hashes_for(it, cache)
        best = None
        for ref, where, label, ch in cands:
            score = is_match(hs, ch)
            if score is not None and (best is None or score < best[3]):
                best = (ref, where, label, score)
        found += bool(best)
        matches[it["id"]] = {"upload": best[0] if best else None, "where": best[1] if best else None,
                             "label": best[2] if best else None, "distance": best[3] if best else None,
                             "checked_at": now, "manual": False}
    # forget entries for items that no longer exist / Meta files no longer listed
    ids = {it["id"] for it in index}
    matches = {k: v for k, v in matches.items() if k in ids}
    cache = {k: v for k, v in cache.items() if k in ids or k in ext_keys}
    save(MATCHES, matches)
    save(CACHE, cache)
    where = {}
    for it in collected:
        w = (matches.get(it["id"]) or {}).get("where") or ("manual" if (matches.get(it["id"]) or {}).get("upload") else "missing")
        where[w] = where.get(w, 0) + 1
    print("{} collected item(s) checked against {} upload(s) and {} Meta file(s): {}".format(
        len(collected), len(uploads), len(ext_keys), ", ".join("{} {}".format(v, k) for k, v in sorted(where.items()))))


if __name__ == "__main__":
    sys.exit(main())
