#!/usr/bin/env python3
"""Meta API usage check (added 2026-10-28, guardrail 17).

Suraj: "how many api calls are allowed vs how many we are at and also
predicted? so we never run out of api calls... always staying well under
limit should be our target when dealing with external platforms."

Meta reports usage as a percentage of the limit (rolling hour) in response
headers, not as a call count, so this tracks that percentage. The dashboard
(live_server.py) logs it from every call it makes; this script adds a probe
of one cheap call and reads the shared log.

  python3 scripts/meta_api_usage.py           # print status, exit 0 / 1 (>=50%) / 2 (>=75%)
  python3 scripts/meta_api_usage.py probe     # one cheap GET, record it, then print status
  python3 scripts/meta_api_usage.py probe --alert   # cron: also Telegram if >=75% (once per hour)

Run it before any heavy Meta pull. At 50% or more, defer anything that
isn't urgent; at 75% or more, make no Meta calls except an approved
execution.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.expanduser("~/ka-meta-ads/meta_api_usage.jsonl")
ALERT_STATE = os.path.expanduser("~/ka-meta-ads/meta_api_usage_alert.json")
CAUTION, STOP = 50, 75
AD_ACCOUNT = "act_686170454752172"


def headers_pct(headers):
    pct, regain, tier = 0.0, 0, ""
    for name in ("x-business-use-case-usage", "x-ad-account-usage", "x-app-usage"):
        raw = headers.get(name) if headers else None
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        items = [obj] if name != "x-business-use-case-usage" else [i for v in obj.values() for i in v]
        for it in items:
            for k in ("call_count", "total_cputime", "total_time", "acc_id_util_pct"):
                if isinstance(it.get(k), (int, float)):
                    pct = max(pct, float(it[k]))
            regain = max(regain, int(it.get("estimated_time_to_regain_access") or 0))
            tier = it.get("ads_api_access_tier") or tier
    return pct, regain, tier


def probe():
    with open(os.path.join(REPO, "meta_token.txt.txt"), encoding="utf-8") as f:
        token = f.read().strip()
    url = "https://graph.facebook.com/v21.0/{}?{}".format(AD_ACCOUNT, urllib.parse.urlencode({"fields": "id", "access_token": token}))
    limited = False
    try:
        resp = urllib.request.urlopen(url, timeout=20)
        hdrs = resp.headers
    except urllib.error.HTTPError as e:
        hdrs = e.headers
        try:
            limited = json.loads(e.read()).get("error", {}).get("code") in (4, 17, 32, 613, 80004)
        except ValueError:
            pass
    pct, regain, tier = headers_pct(hdrs)
    if limited:
        pct = max(pct, 100.0)
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pct": round(pct, 1),
           "regain_min": regain, "tier": tier, "limited": limited, "source": "probe"}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    prune()


def prune(keep_days=7):
    try:
        with open(LOG, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    kept = [l for l in lines if l.strip() and datetime.fromisoformat(json.loads(l)["ts"]) >= cutoff]
    if len(kept) < len(lines):
        with open(LOG, "w", encoding="utf-8") as f:
            f.writelines(kept)


def read_recent(hours=2):
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        with open(LOG, encoding="utf-8") as f:
            for l in f:
                try:
                    r = json.loads(l)
                    r["_t"] = datetime.fromisoformat(r["ts"])
                except (ValueError, KeyError):
                    continue
                if r["_t"] >= cutoff:
                    out.append(r)
    except OSError:
        pass
    return sorted(out, key=lambda r: r["_t"])


def status():
    rows = read_recent(2)
    if not rows:
        return {"now": None, "peak_1h": None, "projected_30m": None, "regain_min": 0, "tier": "", "level": "unknown"}
    now = rows[-1]
    hour = [r for r in rows if r["_t"] >= now["_t"] - timedelta(hours=1)]
    peak = max(r["pct"] for r in hour)
    # Straight-line projection 30 minutes ahead from the last 30 minutes.
    recent = [r for r in rows if r["_t"] >= now["_t"] - timedelta(minutes=30)]
    proj = now["pct"]
    if len(recent) >= 2:
        span = (recent[-1]["_t"] - recent[0]["_t"]).total_seconds() / 60
        if span >= 5:
            slope = (recent[-1]["pct"] - recent[0]["pct"]) / span
            proj = max(0.0, now["pct"] + slope * 30)
    worst = max(now["pct"], proj)
    level = "stop" if worst >= STOP else "caution" if worst >= CAUTION else "ok"
    return {"now": now["pct"], "peak_1h": peak, "projected_30m": round(proj, 1), "regain_min": now.get("regain_min", 0),
            "tier": now.get("tier", ""), "level": level, "as_of": now["ts"]}


def alert(st):
    if st["level"] != "stop":
        return
    try:
        last = json.load(open(ALERT_STATE)).get("ts", 0)
    except (OSError, ValueError):
        last = 0
    if time.time() - last < 3600:
        return
    cfg = {}
    with open(os.path.join(REPO, "telegram_config.txt"), encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    text = ("⚠️ Meta API usage high: {now:.0f}% now, {proj:.0f}% projected in 30 min (limit 100%). "
            "Scheduled runs and the dashboard are holding back non-urgent Meta calls until it falls.").format(
        now=st["now"] or 0, proj=st["projected_30m"] or 0)
    data = urllib.parse.urlencode({"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": text}).encode()
    urllib.request.urlopen("https://api.telegram.org/bot{}/sendMessage".format(cfg["TELEGRAM_BOT_TOKEN"]), data=data, timeout=20)
    json.dump({"ts": time.time()}, open(ALERT_STATE, "w"))


def main():
    args = sys.argv[1:]
    if args and args[0] == "probe":
        probe()
    st = status()
    if "--alert" in args:
        alert(st)
    if st["now"] is None:
        print("Meta API usage: no reading in the last 2 hours (run with 'probe').")
        sys.exit(0)
    print("Meta API usage: {now:.0f}% now, peak {peak:.0f}% in the last hour, {proj:.0f}% projected in 30 min "
          "(limit 100%; caution {c}%, stop {s}%) - {lvl}. Tier: {tier}.{regain}".format(
              now=st["now"], peak=st["peak_1h"], proj=st["projected_30m"], c=CAUTION, s=STOP, lvl=st["level"].upper(),
              tier=st["tier"] or "unknown",
              regain=" Blocked; access back in about {} min.".format(st["regain_min"]) if st["regain_min"] else ""))
    sys.exit(2 if st["level"] == "stop" else 1 if st["level"] == "caution" else 0)


if __name__ == "__main__":
    main()
