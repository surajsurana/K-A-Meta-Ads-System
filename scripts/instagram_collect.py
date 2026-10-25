#!/usr/bin/env python3
"""Daily Instagram content collector -> Ops Console Media Library (backlog #12).

Added 2026-10-25, Suraj: "if there is some content found on instagram about our
brand... can it not be found daily and downloaded and saved in our media
library". Runs from cron once a day around midnight IST (unattended jobs run at
night). Read-only against Instagram: GET calls only.

What it collects (only what Instagram's official API exposes):
  - own posts/Reels       GET /{ig}/media
  - own Stories           GET /{ig}/stories   (live 24h only, hence daily)
  - posts tagging us      GET /{ig}/tags
  - brand hashtags        ig_hashtag_search -> /recent_media (last 24h only)
Not possible via the API, and deliberately not attempted (no scraping):
untagged posts that just show a K&A outfit, or someone saying the name aloud.
Stories by others that @mention us arrive through the Instagram messaging
webhook (backlog #6) once that's live.

Each new item lands in ~/ka-meta-ads-dashboard/media-library/ with an index.json
entry (source "instagram_auto", found_via, the post link, who posted it) and
reviewed_by_social=false, so the daily heartbeat's Media Library check hands it
to social-community-manager exactly like a manual upload.

Space (Suraj chose option C, 2026-10-25): full videos are kept 30 days so they
play in the library, then the video file is deleted and the thumbnail + post
link stay. A video over MAX_VIDEO_MB is saved as thumbnail + link only, and
nothing is downloaded at all while the shared droplet has under MIN_FREE_GB free.

  python3 scripts/instagram_collect.py            # normal daily run
  python3 scripts/instagram_collect.py --dry-run  # list what it would save
"""

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("KA_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.expanduser("~/ka-meta-ads-dashboard/media-library")
INDEX = os.path.join(LIB, "index.json")
STATE = os.path.expanduser("~/ka-meta-ads/instagram_collect_state.json")
LOG = os.path.expanduser("~/ka-meta-ads-logs/instagram-collect.log")
GRAPH = "https://graph.facebook.com/v21.0"
IG = "17841401625784277"
OWN_HANDLE = "@karishmaashita"
HASHTAGS = ("karishmaashita", "kabykarishmaandashita", "karishmaandashita")
FIRST_RUN_LOOKBACK = timedelta(days=7)   # don't flood the library with old posts on day one
LOOKBACK = timedelta(days=3)             # normal runs: anything new from the last 3 days
VIDEO_KEEP = timedelta(days=30)
MAX_VIDEO_MB = 60
MIN_FREE_GB = 1.0
FIELDS = "id,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,caption,username"


def log(msg):
    line = "[{}] {}".format(datetime.now(timezone.utc).isoformat(timespec="seconds"), msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def token():
    with open(os.path.join(REPO, "meta_token.txt.txt"), encoding="utf-8") as f:
        return f.read().strip()


def get(path, params, tok):
    url = "{}/{}?{}".format(GRAPH, path, urllib.parse.urlencode(dict(params, access_token=tok)))
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except ValueError:
            msg = str(e)
        return {"error": msg[:200]}
    except (urllib.error.URLError, TimeoutError) as e:
        return {"error": str(e)[:200]}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except (ValueError, TypeError):
        return None


def found_items(tok):
    """[(found_via, media dict)] from every readable source."""
    out, errors = [], []
    for via, path, params in (("own_post", IG + "/media", {"fields": FIELDS, "limit": 25}),
                              ("own_story", IG + "/stories", {"fields": FIELDS}),
                              ("tagged", IG + "/tags", {"fields": FIELDS, "limit": 25})):
        d = get(path, params, tok)
        if "error" in d:
            errors.append("{}: {}".format(via, d["error"]))
            continue
        out += [(via, m) for m in d.get("data", [])]
    for tag in HASHTAGS:
        h = get("ig_hashtag_search", {"user_id": IG, "q": tag}, tok)
        hid = (h.get("data") or [{}])[0].get("id")
        if not hid:
            if "error" in h:
                errors.append("#{}: {}".format(tag, h["error"]))
            continue
        d = get(hid + "/recent_media", {"user_id": IG, "limit": 50,
                                        "fields": "id,media_type,media_url,permalink,timestamp,caption"}, tok)
        if "error" in d:
            errors.append("#{}: {}".format(tag, d["error"]))
            continue
        out += [("hashtag_" + tag, m) for m in d.get("data", [])]
    return out, errors


def first_child(m, tok):
    d = get(m["id"], {"fields": "children{media_type,media_url,thumbnail_url}"}, tok)
    kids = (d.get("children") or {}).get("data") or []
    return kids[0] if kids else {}


def download(url, dest, max_mb=None):
    with urllib.request.urlopen(url, timeout=180) as r:
        size = int(r.headers.get("Content-Length") or 0)
        if max_mb and size > max_mb * 1024 * 1024:
            return False
        with open(dest + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
    os.replace(dest + ".part", dest)
    return True


def shortcode(permalink):
    return (permalink or "").rstrip("/").split("/")[-1] or "post"


def collect(dry_run=False):
    tok = token()
    state = load_json(STATE, {})
    seen = set(state.get("seen_ids", []))
    first_run = not state
    cutoff = datetime.now(timezone.utc) - (FIRST_RUN_LOOKBACK if first_run else LOOKBACK)
    items, errors = found_items(tok)
    for e in errors:
        log("WARN source unavailable - " + e)
    index = load_json(INDEX, [])
    in_library = {e.get("ig_media_id") for e in index if e.get("ig_media_id")}
    new_entries, skipped_space = [], 0
    for via, m in items:
        mid = m.get("id")
        ts = parse_ts(m.get("timestamp"))
        if not mid or mid in seen or mid in in_library or not ts or ts < cutoff:
            continue
        src = m
        if m.get("media_type") == "CAROUSEL_ALBUM":
            src = dict(first_child(m, tok), permalink=m.get("permalink"))
        is_video = src.get("media_type") == "VIDEO" or m.get("media_product_type") == "REELS"
        handle = OWN_HANDLE if via.startswith("own") else ("@" + m["username"] if m.get("username") else "")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = "{}_ig_{}_{}".format(stamp, via, shortcode(m.get("permalink")))
        entry = {
            "id": base + (".mp4" if is_video else ".jpg"),
            "filename": base + (".mp4" if is_video else ".jpg"),
            "original_name": shortcode(m.get("permalink")),
            "caption": (m.get("caption") or "").strip()[:280],
            "content_type": "video/mp4" if is_video else "image/jpeg",
            "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reviewed_by_social": False,
            "source_handle": handle,
            "source": "instagram_auto",
            "found_via": via,
            "ig_media_id": mid,
            "ig_permalink": m.get("permalink", ""),
            "ig_posted_at": m.get("timestamp", ""),
        }
        if dry_run:
            new_entries.append(entry)
            continue
        free_gb = shutil.disk_usage(LIB).free / 1e9
        if free_gb < MIN_FREE_GB:
            skipped_space += 1
            continue
        try:
            if is_video:
                if src.get("thumbnail_url"):
                    download(src["thumbnail_url"], os.path.join(LIB, entry["filename"] + ".thumb.jpg"))
                    entry["thumbnail"] = entry["filename"] + ".thumb.jpg"
                ok = bool(src.get("media_url")) and download(src["media_url"], os.path.join(LIB, entry["filename"]),
                                                              max_mb=MAX_VIDEO_MB)
                if not ok:
                    # Instagram withholds some files (e.g. Stories with licensed music),
                    # or it's too big: keep the thumbnail + link only.
                    entry["video_removed_at"] = entry["uploaded_at"]
                    entry["video_note"] = "not downloaded (Instagram didn't provide the file, or it's over {} MB)".format(MAX_VIDEO_MB)
                    if not entry.get("thumbnail"):
                        continue
            else:
                if not src.get("media_url"):
                    continue
                download(src["media_url"], os.path.join(LIB, entry["filename"]))
            path = os.path.join(LIB, entry["filename"])
            entry["size"] = os.path.getsize(path) if os.path.exists(path) else 0
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log("WARN couldn't download {} ({}): {}".format(m.get("permalink"), via, e))
            continue
        new_entries.append(entry)
        seen.add(mid)

    if dry_run:
        for e in new_entries:
            print("would save:", e["found_via"], e["source_handle"], e["ig_permalink"], e["content_type"])
        print("{} new item(s); sources unavailable: {}".format(len(new_entries), len(errors)))
        return

    # Retention: after 30 days, drop the video file, keep thumbnail + link.
    removed = 0
    index = load_json(INDEX, [])   # re-read right before writing, in case of edits meanwhile
    now = datetime.now(timezone.utc)
    for e in index:
        if e.get("source") != "instagram_auto" or not (e.get("content_type") or "").startswith("video"):
            continue
        if e.get("video_removed_at") or e.get("keep"):
            continue
        added = parse_ts((e.get("uploaded_at") or "").replace("Z", "+0000"))
        if added and now - added > VIDEO_KEEP:
            try:
                os.remove(os.path.join(LIB, e["filename"]))
            except FileNotFoundError:
                pass
            e["video_removed_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            e["video_note"] = "video file removed after 30 days to save space; watch it on Instagram"
            removed += 1
    known = {e.get("ig_media_id") for e in index if e.get("ig_media_id")}
    index += [e for e in new_entries if e["ig_media_id"] not in known]
    save_json(INDEX, index)
    state["seen_ids"] = sorted(seen)[-5000:]
    state["last_run"] = now.isoformat(timespec="seconds")
    save_json(STATE, state)
    log("saved {} new item(s) to the Media Library ({}); videos expired: {}; skipped for low disk: {}".format(
        len(new_entries), ", ".join(sorted({e["found_via"] for e in new_entries})) or "none", removed, skipped_space))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    collect(ap.parse_args().dry_run)


if __name__ == "__main__":
    sys.exit(main())
