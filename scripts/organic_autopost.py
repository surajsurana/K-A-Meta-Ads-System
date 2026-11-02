#!/usr/bin/env python3
"""Organic auto-publish on the content producer's Drive approval.

Added 2026-10-23. Suraj, directly: "no tap. drive checks only. lets see this
for a few days. if any problem happens we will then add a security layer of
lupta tap on telegram." Earlier, also directly: "Lupta approvals are fine to
be posted." Scope is deliberately narrow: ORGANIC Instagram feed posts
(single image or carousel), Reels (added 2026-11-02) and Stories only. This code never touches ads,
boosts, budgets or anything with spend.

Every decision here is made by plain code, never by an LLM:
  - at hand-off (`submit`) and again just before publishing (`publish`), the
    Drive evidence is re-checked through the Drive/Sheets APIs:
      1. every file is in folder "04 Approved" (FOLDER_04);
      2. every filename starts with the plan ID;
      3. the plan sheet's "Plan" tab says Status = Approved for that ID
         (anything else - Hold, Changes needed, blank - stops it);
      4. every file is owned by ALLOWED_OWNERS.
  - 2026-11-02: Suraj, directly: "i dont want to approve each post that lupta
    approves. it should go automatically". Feed images taller than 4:5 (the
    producer shoots 3:4) are trimmed evenly top and bottom to 4:5 and resized
    to 1080 wide - the same method the tap route used for LM-005/009/010 - and
    Reels are re-encoded to H.264/AAC mp4 (faststart) with ffmpeg, nothing else
    changed.
  - Suraj gets an FYI on Telegram with a Cancel button (the listener's
    existing X:<id> callback), then the permalink or the reason it stopped.
  - At the scheduled time the Telegram listener's scheduler runs
    `publish <log-id>`; nothing else triggers it.

Known weakness Suraj accepted (2026-10-23): the agents work through Suraj's
Google account, so a sheet cell or a Suraj-owned file is not proof that a
human approved. Only Lupta-owned files are. The fallback, if anything goes
wrong, is adding Lupta's own Telegram tap.

Runs on the droplet in its own venv (google-auth, requests, Pillow; ffmpeg for Reels) so the
listener stays stdlib-only:
  ~/venvs/ka-autopost/bin/python3 scripts/organic_autopost.py check  handoff.json
  ~/venvs/ka-autopost/bin/python3 scripts/organic_autopost.py submit handoff.json   (or "-" for stdin)
  ~/venvs/ka-autopost/bin/python3 scripts/organic_autopost.py publish <log-id>
  ~/venvs/ka-autopost/bin/python3 scripts/organic_autopost.py cancel  <log-id>   (to move a post: cancel, then submit again)

Hand-off JSON (written by the K&A Social Media session):
  {"plan_id": "LM-003", "type": "feed_carousel" | "feed_image" | "reel" | "story",
   "drive_file_ids": ["...", "..."],            # in slide order
   "caption": "...",                            # feed and reel; exact text
   "alt_text": ["...", "..."],                  # optional, one per image (feed images only)
   "scheduled_for": "2026-10-24T19:30:00+05:30",
   "approval": "CONTENT PRODUCER APPROVED – AUTO"}
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("KA_REPO") or os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
import telegram_approval_listener as L  # noqa: E402  (state store, Telegram, log)

HOME_DIR = os.path.expanduser("~/ka-meta-ads")
SA_FILE = os.path.join(HOME_DIR, "google_service_account.json")
HOST_DIR = os.path.expanduser("~/ka-meta-ads-dashboard/media-library")
HOST_URL = "http://168.144.66.161:8090/media-library/"
FOLDER_04 = "1j_RUlcgQTUAXaT7dqheOJlE-zXdte1jx"
SHEET_ID = "1YJUK1r4tU_r2yWoDwEDapitX8u7hGciY6Igncgpv4IA"
SHEET_TAB = "Plan"
ALLOWED_OWNERS = {"kirangelupta@gmail.com", "suraj.surana@gmail.com"}
APPROVAL_MARK = "CONTENT PRODUCER APPROVED – AUTO"
IG_USER = "17841401625784277"
GRAPH = "https://graph.facebook.com/v21.0"
TYPES = ("feed_image", "feed_carousel", "reel", "story")
CAPTIONED = ("feed_image", "feed_carousel", "reel")
IMAGE_MIMES = ("image/png", "image/jpeg")
VIDEO_MIMES = ("video/quicktime", "video/mp4")
FEED_MIN_RATIO = 0.74  # down to ~3:4 is trimmed to 4:5 (about 3% off top and bottom); taller stops
OUT_WIDTH = 1080
MAX_AHEAD = timedelta(days=14)
MIN_AHEAD = timedelta(minutes=5)
IST = timezone(timedelta(hours=5, minutes=30))


class Stop(Exception):
    """A check failed - nothing is scheduled or posted."""


# --- inputs ---------------------------------------------------------------

def load_handoff(path):
    if path == "-":  # e.g. ssh kmetaads@droplet '... submit -' < handoff.json
        h = json.load(sys.stdin)
    else:
        with open(path, encoding="utf-8") as f:
            h = json.load(f)
    problems = []
    pid = str(h.get("plan_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,40}", pid):
        problems.append("plan_id missing or has unexpected characters")
    if h.get("type") not in TYPES:
        problems.append("type must be one of " + ", ".join(TYPES))
    ids = h.get("drive_file_ids") or []
    if not ids or not all(isinstance(x, str) and x for x in ids):
        problems.append("drive_file_ids missing")
    if h.get("type") == "feed_carousel" and not (2 <= len(ids) <= 10):
        problems.append("a carousel needs 2-10 images")
    if h.get("type") in ("feed_image", "reel", "story") and len(ids) != 1:
        problems.append(h.get("type") + " takes exactly 1 file")
    if h.get("type") in CAPTIONED and not (h.get("caption") or "").strip():
        problems.append("feed posts and Reels need a caption")
    alts = h.get("alt_text") or []
    if alts and h.get("type") == "reel":
        problems.append("alt_text isn't supported on Reels")
    elif alts and len(alts) != len(ids):
        problems.append("alt_text must have one entry per image")
    if (h.get("approval") or "").strip() != APPROVAL_MARK:
        problems.append('approval must read exactly "' + APPROVAL_MARK + '"')
    try:
        when = datetime.fromisoformat(h.get("scheduled_for") or "")
        if when.tzinfo is None:
            raise ValueError
        now = datetime.now(timezone.utc)
        if when < now + MIN_AHEAD:
            problems.append("scheduled_for must be at least 5 minutes ahead")
        if when > now + MAX_AHEAD:
            problems.append("scheduled_for must be within 14 days")
    except ValueError:
        problems.append("scheduled_for must be ISO 8601 with an offset, e.g. 2026-10-24T19:30:00+05:30")
    if problems:
        raise Stop("; ".join(problems))
    h["plan_id"] = pid
    return h


# --- Google (read-only) ---------------------------------------------------

def google():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    if not os.path.exists(SA_FILE):
        raise Stop("Google read access isn't set up yet (no service-account key on the droplet)")
    creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=[
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ])
    return AuthorizedSession(creds)


def sheet_status(s, plan_id):
    r = s.get("https://sheets.googleapis.com/v4/spreadsheets/{}/values/{}".format(
        SHEET_ID, urllib.parse.quote(SHEET_TAB)), timeout=30)
    if r.status_code != 200:
        raise Stop("can't read the plan sheet ({})".format(r.status_code))
    rows = r.json().get("values") or []
    if not rows:
        raise Stop("the plan sheet's Plan tab is empty")
    header = [str(c).strip().lower() for c in rows[0]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None
    id_col = col("id", "plan id", "post id", "content id")
    st_col = col("status")
    if id_col is None or st_col is None:
        raise Stop("can't find the ID and Status columns in the plan sheet")
    matches = [row for row in rows[1:] if len(row) > id_col and str(row[id_col]).strip() == plan_id]
    if not matches:
        raise Stop("{} isn't in the plan sheet".format(plan_id))
    if len(matches) > 1:
        raise Stop("{} appears more than once in the plan sheet".format(plan_id))
    row = matches[0]
    return str(row[st_col]).strip() if len(row) > st_col else ""


def check_drive(s, plan_id, file_ids, kind):
    problems, files = [], []
    for fid in file_ids:
        r = s.get("https://www.googleapis.com/drive/v3/files/" + fid, params={
            "fields": "id,name,parents,owners(emailAddress),mimeType,trashed,md5Checksum",
            "supportsAllDrives": "true"}, timeout=30)
        if r.status_code != 200:
            problems.append("file {} can't be read ({})".format(fid, r.status_code))
            continue
        f = r.json()
        name = f.get("name", "")
        if f.get("trashed"):
            problems.append(name + " is in the trash")
        if FOLDER_04 not in (f.get("parents") or []):
            problems.append(name + " isn't in 04 Approved")
        if not name.startswith(plan_id):
            problems.append(name + " doesn't start with " + plan_id)
        owners = {(o.get("emailAddress") or "").lower() for o in f.get("owners") or []}
        if not owners or not owners <= ALLOWED_OWNERS:
            problems.append(name + " is owned by " + (", ".join(sorted(owners)) or "nobody known"))
        if kind == "reel" and f.get("mimeType") not in VIDEO_MIMES:
            problems.append(name + " isn't a MOV/MP4 video")
        if kind != "reel" and f.get("mimeType") not in IMAGE_MIMES:
            problems.append(name + " isn't a PNG/JPEG image")
        files.append(f)
    try:
        st = sheet_status(s, plan_id)
        if st.lower() != "approved":
            problems.append('the plan sheet says "{}", not "Approved"'.format(st or "blank"))
    except Stop as e:
        problems.append(str(e))
    if problems:
        raise Stop("; ".join(problems))
    return files


def download(s, f):
    r = s.get("https://www.googleapis.com/drive/v3/files/" + f["id"],
              params={"alt": "media", "supportsAllDrives": "true"}, timeout=300)
    if r.status_code != 200:
        raise Stop("couldn't download " + f.get("name", f["id"]))
    return r.content


def load_images(s, files, kind):
    from PIL import Image
    images = []
    for f in files:
        im = Image.open(io.BytesIO(download(s, f)))
        im.load()
        w, h = im.size
        ratio = w / float(h)
        if kind.startswith("feed") and not (FEED_MIN_RATIO <= ratio <= 1.92):
            raise Stop("{} is {}x{}: feed images must be between 3:4 and 1.91:1".format(f.get("name"), w, h))
        if kind.startswith("feed") and ratio < 0.8:
            # trim evenly top and bottom to exactly 4:5, as the tap route did
            cut = h - round(w * 5 / 4.0)
            im = im.crop((0, cut // 2, w, h - (cut - cut // 2)))
            w, h = im.size
        if w > OUT_WIDTH:
            im = im.resize((OUT_WIDTH, round(h * OUT_WIDTH / float(w))), Image.LANCZOS)
        images.append(im)
    if kind == "feed_carousel" and len({round(i.size[0] / float(i.size[1]), 3) for i in images}) > 1:
        raise Stop("carousel slides have different shapes; Instagram would crop them to the first")
    return images


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", path],
                       capture_output=True, text=True, timeout=120)
    try:
        d = json.loads(r.stdout)
        st = d["streams"][0]
        return int(st["width"]), int(st["height"]), float(d["format"]["duration"])
    except (ValueError, KeyError, IndexError):
        raise Stop("couldn't read the video file")


def load_video(s, files, convert=True):
    """Download the Reel and re-encode it to an Instagram-safe mp4; returns the temp path.
    convert=False (check/submit) only validates it: the encode takes minutes on the droplet."""
    f = files[0]
    tmp = tempfile.mkdtemp(prefix="ka-reel-")
    src, out = os.path.join(tmp, "src"), os.path.join(tmp, "out.mp4")
    try:
        with open(src, "wb") as fh:
            fh.write(download(s, f))
        w, h, dur = probe(src)
        if not (0.55 <= w / float(h) <= 0.57):
            raise Stop("{} is {}x{}: Reels must be vertical 9:16".format(f.get("name"), w, h))
        if not (3 <= dur <= 900):
            raise Stop("{} is {:.1f}s: Reels must be 3 s to 15 min".format(f.get("name"), dur))
        if not convert:
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?",
               "-map_metadata", "-1", "-dn", "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
               "-pix_fmt", "yuv420p", "-crf", "18", "-maxrate", "12M", "-bufsize", "24M",
               "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart"]
        if w > OUT_WIDTH:
            cmd += ["-vf", "scale={}:-2:flags=lanczos".format(OUT_WIDTH)]
        run = subprocess.run(cmd + [out], capture_output=True, text=True, timeout=900)
        if run.returncode != 0 or not os.path.exists(out):
            raise Stop("couldn't convert the Reel: " + run.stderr[-200:])
        if os.path.getsize(out) > 300 * 1024 * 1024:
            raise Stop("the converted Reel is over 300 MB")
        os.remove(src)
        return out
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def load_media(s, files, kind, convert=True):
    return load_video(s, files, convert) if kind == "reel" else load_images(s, files, kind)


def discard(media):
    if isinstance(media, str):  # a Reel's temp folder
        shutil.rmtree(os.path.dirname(media), ignore_errors=True)


# --- hosting + Instagram --------------------------------------------------

def host(images, log_id):
    os.makedirs(HOST_DIR, exist_ok=True)
    if isinstance(images, str):  # a Reel: the converted mp4's temp path
        name = "post-{}_01.mp4".format(re.sub(r"[^A-Za-z0-9_-]", "", log_id))
        shutil.copyfile(images, os.path.join(HOST_DIR, name))
        discard(images)
        url = HOST_URL + name
        req = urllib.request.Request(url, headers={"Range": "bytes=0-1023"})  # the server answers 501 to HEAD
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 206) or "video/mp4" not in resp.headers.get("Content-Type", ""):
                raise Stop("hosted video didn't load: " + url)
        return [url]
    urls = []
    for i, im in enumerate(images, 1):
        name = "post-{}_{:02d}.jpg".format(re.sub(r"[^A-Za-z0-9_-]", "", log_id), i)
        im.convert("RGB").save(os.path.join(HOST_DIR, name), "JPEG", quality=96, subsampling=0, optimize=True)
        url = HOST_URL + name
        with urllib.request.urlopen(url, timeout=30) as resp:
            if resp.status != 200 or "image/jpeg" not in resp.headers.get("Content-Type", ""):
                raise Stop("hosted image didn't load: " + url)
        urls.append(url)
    return urls


def unhost(log_id):
    pfx = "post-{}_".format(re.sub(r"[^A-Za-z0-9_-]", "", log_id))
    if not os.path.isdir(HOST_DIR):
        return
    for n in os.listdir(HOST_DIR):
        if n.startswith(pfx) and n.endswith((".jpg", ".mp4")):
            try:
                os.remove(os.path.join(HOST_DIR, n))
            except OSError:
                pass


def meta_token():
    with open(os.path.join(REPO, "meta_token.txt.txt"), encoding="utf-8") as f:
        return f.read().strip()


def ig(method, path, params, token):
    params = dict(params, access_token=token)
    data = urllib.parse.urlencode(params).encode()
    url = GRAPH + "/" + path
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except ValueError:
            msg = body
        raise RuntimeError(msg[:300])


def wait_finished(cid, token, tries=18):
    for _ in range(tries):
        st = ig("GET", cid, {"fields": "status_code"}, token).get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise Stop("Instagram couldn't process the post (" + st + ")")
        time.sleep(10)
    raise Stop("Instagram took too long to process the post")


def image_container(url, alt, token, extra):
    p = dict(extra, image_url=url)
    if alt:
        p["alt_text"] = alt
    try:
        return ig("POST", IG_USER + "/media", p, token)["id"]
    except RuntimeError as e:
        if alt and "alt_text" in str(e).lower():
            p.pop("alt_text")
            return ig("POST", IG_USER + "/media", p, token)["id"]
        raise


def caption_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def instagram_publish(spec, urls, token):
    kind, caption, alts = spec["type"], spec.get("caption") or "", spec.get("alt_text") or []
    if kind in CAPTIONED:
        recent = ig("GET", IG_USER + "/media", {"fields": "caption,permalink", "limit": 10}, token).get("data", [])
        for m in recent:
            if caption_hash(m.get("caption")) == caption_hash(caption):
                raise Stop("a post with this exact caption is already live: " + m.get("permalink", ""))
    if kind == "feed_carousel":
        kids = [image_container(u, alts[i] if i < len(alts) else "", token, {"is_carousel_item": "true"})
                for i, u in enumerate(urls)]
        for k in kids:
            wait_finished(k, token)
        cid = ig("POST", IG_USER + "/media", {"media_type": "CAROUSEL", "children": ",".join(kids),
                                              "caption": caption}, token)["id"]
    elif kind == "feed_image":
        cid = image_container(urls[0], alts[0] if alts else "", token, {"caption": caption})
    elif kind == "reel":
        cid = ig("POST", IG_USER + "/media", {"media_type": "REELS", "video_url": urls[0], "caption": caption,
                                              "share_to_feed": "true"}, token)["id"]
    else:
        cid = ig("POST", IG_USER + "/media", {"media_type": "STORIES", "image_url": urls[0]}, token)["id"]
    wait_finished(cid, token, 60 if kind == "reel" else 18)
    media_id = ig("POST", IG_USER + "/media_publish", {"creation_id": cid}, token)["id"]
    m = ig("GET", media_id, {"fields": "permalink,media_type,caption,children{id}"}, token)
    if kind in CAPTIONED and caption_hash(m.get("caption")) != caption_hash(caption):
        raise RuntimeError("published, but the caption doesn't match exactly: " + m.get("permalink", ""))
    if kind == "feed_carousel" and len((m.get("children") or {}).get("data") or []) != len(urls):
        raise RuntimeError("published, but the slide count doesn't match: " + m.get("permalink", ""))
    return media_id, m.get("permalink", "")


# --- learning log + state + Telegram -----------------------------------------

def append_log(entry):
    out = subprocess.run(["bash", os.path.join(SCRIPTS, "append-learning-log.sh"),
                          json.dumps(entry, ensure_ascii=False)],
                         cwd=REPO, capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        L.log("organic_autopost: learning-log append failed: " + (out.stdout + out.stderr)[-300:])


def when_text(iso):
    return datetime.fromisoformat(iso).astimezone(IST).strftime("%d %b, %I:%M %p IST")


def describe(spec):
    return {"feed_carousel": "{}-slide carousel post".format(len(spec["drive_file_ids"])),
            "feed_image": "single-image feed post", "reel": "Reel", "story": "Story"}[spec["type"]]


def cmd_check(path):
    spec = load_handoff(path)
    s = google()
    files = check_drive(s, spec["plan_id"], spec["drive_file_ids"], spec["type"])
    discard(load_media(s, files, spec["type"], convert=False))
    print("OK: {} passes every check ({}).".format(spec["plan_id"], ", ".join(f["name"] for f in files)))


def cmd_submit(path):
    spec = load_handoff(path)
    pid = spec["plan_id"]

    def dup(state):
        for k, e in state.items():
            if e.get("organic_plan_id") == pid and e.get("status") in ("scheduled", "publishing", "executed"):
                return None, k
        return None, None
    existing = L.with_state_lock(dup)
    if existing:
        raise Stop("{} is already {} (entry {})".format(pid, L.get_entry(existing).get("status"), existing))
    s = google()
    files = check_drive(s, pid, spec["drive_file_ids"], spec["type"])
    discard(load_media(s, files, spec["type"], convert=False))

    log_id = "KL-ORGANIC-{}-{}".format(pid, datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    token, chat_id = L.load_telegram_config()
    text = ("📅 Scheduled, approved by Lupta\n\n{} {} goes out on Instagram at {}.\n\n"
            "Checked: files in 04 Approved, named {}..., plan sheet says Approved.\n"
            "No action needed. Tap Cancel to stop it.\n\nPlan ID: {}").format(
        pid, describe(spec), when_text(spec["scheduled_for"]), pid, log_id)
    sent = L.tg_api(token, "sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": json.dumps(
        {"inline_keyboard": [[{"text": "Cancel scheduled post", "callback_data": "X:" + log_id}]]})})
    if not sent.get("ok"):
        raise Stop("couldn't send the FYI to Suraj, so nothing was scheduled")

    def put(state):
        state[log_id] = {"status": "scheduled", "executor": "organic_autopost", "organic_plan_id": pid,
                         "scheduled_for": spec["scheduled_for"], "chat_id": str(chat_id),
                         "message_id": sent["result"]["message_id"],
                         "sent_at": datetime.now(timezone.utc).isoformat(), "spec": spec}
        return state, None
    L.with_state_lock(put)
    append_log({
        "id": log_id, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "actor": "organic-autopost",
        "type": "decision", "subject": "Organic Instagram post " + pid,
        "summary": ("Content-producer-approved organic {} scheduled for {} without a Suraj tap, per Suraj's "
                    "2026-10-23 rule (Drive checks only). Code verified: files in 04 Approved, names start with {}, "
                    "plan sheet Status Approved, owners {}. Re-checked again at publish time.").format(
                        describe(spec), spec["scheduled_for"], pid,
                        ", ".join(sorted({(o.get("emailAddress") or "") for f in files for o in f.get("owners", [])}))),
        "post_spec": spec, "scheduled_for": spec["scheduled_for"], "source": "measured", "confidence": "high",
        "tags": ["organic-post", "auto-publish", "content-producer-approved", "social-media-handoff"],
    })
    print(json.dumps({"status": "scheduled", "log_id": log_id, "scheduled_for": spec["scheduled_for"]}))


def cmd_publish(log_id):
    entry = L.get_entry(log_id) or {}
    token_tg, chat_id = L.load_telegram_config()
    if entry.get("executor") != "organic_autopost" or entry.get("status") not in ("publishing", "scheduled"):
        print("not publishable: " + str(entry.get("status")))
        return
    spec = entry["spec"]
    pid = spec["plan_id"]
    try:
        s = google()
        files = check_drive(s, pid, spec["drive_file_ids"], spec["type"])
        media = load_media(s, files, spec["type"])
        urls = host(media, log_id)
        media_id, permalink = instagram_publish(spec, urls, meta_token())
    except Stop as e:
        L.finalize_status(log_id, "stopped", str(e))
        L.tg_api(token_tg, "sendMessage", {"chat_id": chat_id, "text":
                 "⏸ Not posted: {}\n\nReason: {}\n\nNothing went out. Fix it in Drive and send it again.\n\nPlan ID: {}".format(
                     pid, str(e), log_id)})
        append_log({"id": log_id + "-STOPPED", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "actor": "organic-autopost", "type": "observation", "subject": "Organic Instagram post " + pid,
                    "summary": "Scheduled organic post NOT published - re-check at publish time failed: " + str(e),
                    "source": "measured", "confidence": "high", "linked_to": [log_id],
                    "tags": ["organic-post", "auto-publish", "stopped"]})
        unhost(log_id)
        return
    except Exception as e:
        L.finalize_status(log_id, "failed", str(e)[:400])
        L.tg_api(token_tg, "sendMessage", {"chat_id": chat_id, "text":
                 "❗ Posting {} failed: {}\n\nCheck Instagram before retrying - it may be partly done.\n\nPlan ID: {}".format(
                     pid, str(e)[:300], log_id)})
        append_log({"id": log_id + "-FAILED", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "actor": "organic-autopost", "type": "observation", "subject": "Organic Instagram post " + pid,
                    "summary": "Scheduled organic post failed while publishing: " + str(e)[:400],
                    "source": "measured", "confidence": "high", "linked_to": [log_id],
                    "tags": ["organic-post", "auto-publish", "failed"]})
        return
    L.finalize_status(log_id, "executed", permalink)
    try:
        L.tg_api(token_tg, "editMessageText", {"chat_id": chat_id, "message_id": entry.get("message_id"),
                 "text": "✅ Posted: {} {}\n\n{}\n\nPlan ID: {}".format(pid, describe(spec), permalink, log_id)})
    except Exception:
        pass
    L.tg_api(token_tg, "sendMessage", {"chat_id": chat_id, "text":
             "✅ {} is live on Instagram (approved by Lupta)\n\n{}".format(pid, permalink)})
    append_log({"id": log_id + "-EXEC", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "actor": "organic-autopost", "type": "change", "subject": "Organic Instagram post " + pid,
                "summary": "Published {} {} at its scheduled time after re-checking the Drive approval: {} (media {}).".format(
                    pid, describe(spec), permalink, media_id),
                "source": "measured", "confidence": "high", "linked_to": [log_id],
                "tags": ["organic-post", "auto-publish", "published"]})
    unhost(log_id)


def cmd_cancel(log_id):
    """The content producer moved a post's date/time: stop the old schedule (same effect as Suraj's Cancel tap)."""
    def op(state):
        entry = state.get(log_id)
        if not entry or entry.get("executor") != "organic_autopost":
            return None, "not an organic auto-post"
        if entry.get("status") != "scheduled":
            return None, "already " + str(entry.get("status"))
        entry["status"] = "cancelled"
        entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
        state[log_id] = entry
        return state, "cancelled"
    outcome = L.with_state_lock(op)
    if outcome != "cancelled":
        raise Stop("can't cancel {}: {}".format(log_id, outcome))
    entry = L.get_entry(log_id)
    token, chat_id = L.load_telegram_config()
    try:
        L.tg_api(token, "editMessageText", {"chat_id": chat_id, "message_id": entry.get("message_id"), "text":
                 "↪️ Moved: {} was taken off {} because Lupta changed its date/time. The new slot follows.\n\nPlan ID: {}".format(
                     entry.get("organic_plan_id"), when_text(entry["scheduled_for"]), log_id)})
    except Exception:
        pass
    append_log({"id": log_id + "-CANCELLED", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "actor": "organic-autopost", "type": "observation",
                "subject": "Organic Instagram post " + str(entry.get("organic_plan_id")),
                "summary": "Scheduled organic post cancelled before publishing (content producer changed its date/time); "
                           "nothing was posted.", "source": "human_told", "confidence": "high", "linked_to": [log_id],
                "tags": ["organic-post", "auto-publish", "cancelled"]})
    print(json.dumps({"status": "cancelled", "log_id": log_id}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("check", "submit", "publish", "cancel"))
    ap.add_argument("target", help="hand-off JSON file (check/submit) or log id (publish/cancel)")
    a = ap.parse_args()
    try:
        {"check": cmd_check, "submit": cmd_submit, "publish": cmd_publish, "cancel": cmd_cancel}[a.command](a.target)
    except Stop as e:
        print("STOPPED: " + str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
