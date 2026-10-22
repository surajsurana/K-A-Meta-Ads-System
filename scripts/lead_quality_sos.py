#!/usr/bin/env python3
"""Lead-quality SOS trigger - shared by the Telegram button and the Ops
Console dashboard button (added 2026-10-22, Suraj: "sometimes lead quality
drops too much... when i press this the system should activate and try to
find out where these bad enquiries are coming from and is it routine
sometimes or change is required").

One SOS at a time, with a cooldown between presses, so repeated taps (or a
stray dashboard click) can't pile up runs. The run itself is read-only:
scripts/run-lead-quality-sos.sh -> prompts/lead-quality-sos.md, which never
calls a Meta/Instagram write endpoint; any fix it finds goes to Suraj as a
normal Telegram approval.

Usage:
  python3 scripts/lead_quality_sos.py trigger --source telegram|dashboard
      -> prints JSON {"status": "started"|"running"|"cooldown", ...}
  python3 scripts/lead_quality_sos.py finish     (called by the run script on exit)
  python3 scripts/lead_quality_sos.py status     (for the dashboard)
State: ~/ka-meta-ads/sos_state.json (outside git), flock-protected.
"""

import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HOME_DIR = os.path.expanduser("~/ka-meta-ads")
STATE_FILE = os.path.join(HOME_DIR, "sos_state.json")
LOCK_FILE = STATE_FILE + ".lock"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_SCRIPT = os.path.join(REPO, "scripts", "run-lead-quality-sos.sh")
COOLDOWN = timedelta(minutes=60)
# A run that somehow never called `finish` (killed hard) stops counting as
# "running" after this, so the button can't stay stuck forever.
STALE_RUN = timedelta(minutes=90)


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def parse(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def current_status(state):
    started = parse(state.get("started_at"))
    if state.get("status") == "running" and started:
        if pid_alive(state.get("pid")) and now() - started < STALE_RUN:
            return "running"
    if started and now() - started < COOLDOWN:
        return "cooldown"
    return "idle"


def with_lock(fn):
    os.makedirs(HOME_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def trigger(source):
    def go():
        state = load()
        st = current_status(state)
        if st == "running":
            return {"status": "running", "started_at": state.get("started_at"), "source": state.get("source")}
        if st == "cooldown":
            nxt = parse(state["started_at"]) + COOLDOWN
            return {"status": "cooldown", "last_started_at": state.get("started_at"), "next_allowed_at": iso(nxt)}
        env = dict(os.environ, KA_SOS_SOURCE=source)
        proc = subprocess.Popen(["bash", RUN_SCRIPT], cwd=REPO, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        state = {"status": "running", "source": source, "started_at": iso(now()), "pid": proc.pid}
        save(state)
        return {"status": "started", "started_at": state["started_at"], "source": source}
    return with_lock(go)


def finish():
    def go():
        state = load()
        state["status"] = "finished"
        state["finished_at"] = iso(now())
        save(state)
        return state
    return with_lock(go)


def status():
    state = load()
    out = dict(state)
    out["status"] = current_status(state) if state else "idle"
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "trigger":
        src = sys.argv[sys.argv.index("--source") + 1] if "--source" in sys.argv else "unknown"
        if src not in ("telegram", "dashboard"):
            src = "unknown"
        print(json.dumps(trigger(src)))
    elif cmd == "finish":
        print(json.dumps(finish()))
    elif cmd == "status":
        print(json.dumps(status()))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
