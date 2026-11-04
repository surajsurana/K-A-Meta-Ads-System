#!/usr/bin/env python3
"""Winners, fair-chance pauses and budget size, per country (added 2026-11-04).

Suraj, 2026-11-04: "we also need to find our winning ad, adset and campaign.
and it should be clearly visible on our dashboard", with a separate winner
per country, a 7-day check, a raise proposed for a winner (not funded from
the weakest ad set), and a pause only once an ad has had a fair chance.
The rules are in docs/architecture.md §3f. This script only measures;
campaign-strategist decides and every change is still Suraj's tap.

Each ad, ad set and campaign is judged against its OWN country's normal
(India vs USA/Canada vs other), never against the account as a whole.

Winner = all five pass, on the last 7 full days (14 if fewer than 5 serious
enquiries in 7):
  volume      >= 5 serious-or-better enquiries
  cost        cost per serious enquiry <= 80% of the country's 90-day normal
  junk        junk share <= the country's normal
  saturation  frequency < 2.5 and CPM up no more than 30% on the week before
  orders      >= 1 ad-traced order from enquiries in the last 30 days, or
              serious->order rate over 90 days at least the country's normal
Promising = cost and junk pass, volume or orders not yet.

Pause candidate (ads and ad sets, any kind): active >= 7 days, spent >= 3x
the country's normal cost per serious enquiry, and 0-1 serious enquiries or
cost per serious >= 1.5x normal. Before that it's "fair chance not used".

Budget size: ad set-weeks over the last 13 weeks grouped by average daily
spend, per country, to find where cost per serious enquiry stops improving.

Read-only: Meta GETs (about a dozen) and one GET to the AISI feed.
Writes ~/ka-meta-ads/winners/latest.json (outside git, aggregates only).

Usage:  python3 scripts/winners_report.py [--print]
"""

import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lead_quality_report as lq  # noqa: E402

OUT_DIR = os.path.join(lq.HOME_DIR, "winners")
LOG_FILE = os.path.join(lq.REPO, "knowledge", "learning-log.jsonl")
IST = lq.IST

MIN_SERIOUS = 5            # volume test, per window
COST_RATIO = 0.80          # winner: <= 80% of the country's normal
FREQ_MAX = 2.5
CPM_RISE_MAX = 0.30
PAUSE_DAYS = 7
PAUSE_SPEND_X = 3.0        # spend >= 3x normal cost per serious enquiry
PAUSE_COST_X = 1.5
RAISE_STEP = 0.20          # Meta resets learning above ~20% per ad set
MIN_SERIOUS_PER_WEEK = 5   # per-country minimum budget = this x normal / 7
NORMAL_DAYS = 90
# A serious share this far above the country's normal is a labelling problem,
# not a winner (caught 2026-11-04: Loomiere "Styling Appointment" ads, 77 of
# 81 enquiries labelled serious from the button's own pre-filled "book a
# visit" text, against ~7% normally). Held as "check labels" until AISI fixes it.
LABEL_SUSPECT_SHARE = 50.0
# A country normal built on fewer serious enquiries than this is too thin to
# judge against (USA/Canada had 1 on 2026-11-04): no cost test, no pauses.
MIN_NORMAL_SERIOUS = 10
LABEL_SUSPECT_X = 3.0
SPEND_BANDS = ((0, 300), (300, 600), (600, 1000), (1000, 1500), (1500, None))
RETARGETING_WORDS = ("engaged", "follower", "retarget", "remarket", "visitor", "warm")
COUNTRY_NAMES = {"GB": "UK", "AU": "Australia", "AE": "UAE", "NZ": "New Zealand", "SG": "Singapore"}


def market_of(targeting):
    geo = (targeting or {}).get("geo_locations") or {}
    cc = set(c.upper() for c in geo.get("countries") or [])
    for key in ("cities", "regions"):
        cc |= set((c.get("country") or "").upper() for c in geo.get(key) or [] if c.get("country"))
    if not cc or cc <= {"IN"}:
        return "India"
    if cc <= {"US", "CA"}:
        return "USA/Canada"
    return " + ".join(COUNTRY_NAMES.get(c, c) for c in sorted(cc))


def kind_of(name):
    n = (name or "").lower()
    if "geo test" in n:
        return "geo test"
    if any(w in n for w in RETARGETING_WORDS):
        return "retargeting"
    return "regular"


def insights(token, level, since, until, fields, extra=None):
    params = {"level": level, "time_range": json.dumps({"since": since, "until": until}),
              "fields": fields, "limit": 500}
    params.update(extra or {})
    return list(lq.meta_paged("{}/insights".format(lq.ACCOUNT), params, token))


def by_id(rows, key):
    out = {}
    for r in rows:
        out[r[key]] = {"spend": float(r.get("spend") or 0), "freq": float(r.get("frequency") or 0) or None,
                       "cpm": float(r.get("cpm") or 0) or None}
    return out


def days_ago(d):
    return (datetime.now(IST).date() - timedelta(days=d)).isoformat()


def enquiry_date(r):
    ts = lq.parse_ts(r.get("first_message_at"))
    return ts.astimezone(IST).date().isoformat() if ts else None


def counts(rows):
    c = {"enquiries": 0, "serious": 0, "junk": 0, "orders": 0}
    for r in rows:
        c["enquiries"] += 1
        if r.get("label") in ("serious", "converted"):
            c["serious"] += 1
        elif r.get("label") == "junk":
            c["junk"] += 1
        if r.get("has_order"):
            c["orders"] += int(r.get("order_count") or 1)
    return c


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def per(a, b):
    return round(a / b) if b else None


def raise_status():
    """Latest winner-raise entry per Meta id, from the learning log."""
    out = {}
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                if "winner-raise" not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if "winner-raise" not in (e.get("tags") or []):
                    continue
                state = "raised" if e.get("type") == "change" else (
                    "raise proposed" if e.get("type") == "decision" else None)
                if not state:
                    continue
                for mid in set(re.findall(r"\b\d{12,20}\b", json.dumps(e))):
                    out[mid] = {"state": state, "date": e.get("date"), "id": e.get("id")}
    except OSError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="also print the JSON")
    args = ap.parse_args()

    token = lq.read_text(os.path.join(lq.REPO, "meta_token.txt.txt"))
    yday = days_ago(1)
    w7, w14, w30 = days_ago(7), days_ago(14), days_ago(30)
    # The feed starts 2026-08-29; spend before that has no enquiries to
    # divide by, so the normal never reaches back further than the feed.
    w90 = max(days_ago(NORMAL_DAYS), lq.DEFAULT_SINCE)
    prev7_start, prev7_end = days_ago(14), days_ago(8)

    adsets = {a["id"]: a for a in lq.meta_paged("{}/adsets".format(lq.ACCOUNT), {
        "fields": "id,name,campaign_id,effective_status,daily_budget,targeting,destination_type,created_time",
        "limit": 200}, token)}
    campaigns = {c["id"]: c for c in lq.meta_paged("{}/campaigns".format(lq.ACCOUNT), {
        "fields": "id,name,effective_status,daily_budget", "limit": 200}, token)}
    ads = {a["id"]: a for a in lq.meta_paged("{}/ads".format(lq.ACCOUNT), {
        "fields": "id,name,adset_id,campaign_id,effective_status,created_time", "limit": 500}, token)}

    f = "ad_id,adset_id,campaign_id,spend,frequency,cpm"
    cur = {lvl: insights(token, lvl, w7, yday, f) for lvl in ("ad", "adset", "campaign")}
    prev = {lvl: insights(token, lvl, prev7_start, prev7_end, f) for lvl in ("ad", "adset", "campaign")}
    ad14 = by_id(insights(token, "ad", w14, yday, "ad_id,spend"), "ad_id")
    ad90 = by_id(insights(token, "ad", w90, yday, "ad_id,spend"), "ad_id")
    weekly_ads = insights(token, "ad", days_ago(91), yday, "ad_id,adset_id,spend",
                          {"time_increment": 7})

    feed = lq.fetch_feed(w90)
    now = datetime.now(timezone.utc)
    rows = [r for r in feed.get("rows", []) if r.get("attribution") == "source_id"
            and r.get("source_id") in ads and not lq.is_pending(r, now)]
    for r in rows:
        r["_date"] = enquiry_date(r)
        r["_adset"] = ads[r["source_id"]].get("adset_id")
        r["_campaign"] = ads[r["source_id"]].get("campaign_id")
    rows = [r for r in rows if r["_date"] and r["_date"] <= yday]

    def adset_market(asid):
        return market_of((adsets.get(asid) or {}).get("targeting"))

    def is_dm(asid):
        return (adsets.get(asid) or {}).get("destination_type") in lq.NON_WHATSAPP_DESTINATIONS

    # Country normals: 90 days, WhatsApp-destination ads only (the feed can't
    # see DM enquiries, backlog #6), leaving out ads whose labels look inflated
    # (see LABEL_SUSPECT_SHARE) so they don't drag every other ad's benchmark down.
    per_ad = collections.defaultdict(list)
    for r in rows:
        per_ad[r["source_id"]].append(r)
    suspect_ads = set()
    for ad_id, rs in per_ad.items():
        c = counts(rs)
        if c["serious"] >= MIN_SERIOUS and pct(c["serious"], c["enquiries"]) >= LABEL_SUSPECT_SHARE:
            suspect_ads.add(ad_id)
    normals = {}
    m_spend = collections.Counter()
    for ad_id, s in ad90.items():
        asid = (ads.get(ad_id) or {}).get("adset_id")
        if asid and not is_dm(asid) and ad_id not in suspect_ads:
            m_spend[adset_market(asid)] += s["spend"]
    m_rows = collections.defaultdict(list)
    for r in rows:
        if not is_dm(r["_adset"]) and r["source_id"] not in suspect_ads:
            m_rows[adset_market(r["_adset"])].append(r)
    for m in set(m_spend) | set(m_rows):
        c = counts(m_rows[m])
        cps = per(m_spend[m], c["serious"])
        normals[m] = {"spend_90d": round(m_spend[m]), "enquiries_90d": c["enquiries"], "serious_90d": c["serious"],
                      "cost_per_serious": cps, "junk_pct": pct(c["junk"], c["enquiries"]),
                      "order_rate_pct": pct(c["orders"], c["serious"]),
                      "serious_pct": pct(c["serious"], c["enquiries"]),
                      "min_daily_budget": round(cps * MIN_SERIOUS_PER_WEEK / 7, -1) if cps else None,
                      "thin": c["serious"] < MIN_NORMAL_SERIOUS}

    status = raise_status()
    spend7_by_ad = {r["ad_id"]: float(r.get("spend") or 0) for r in cur["ad"]}

    def entity(level, eid, name, asid_list, rws, spend_cur, spend_14, spend_90, ins_cur, ins_prev, market, extra):
        n = normals.get(market) or {}
        normal = None if n.get("thin") else n.get("cost_per_serious")
        c7 = counts([r for r in rws if r["_date"] >= w7])
        c14 = counts([r for r in rws if r["_date"] >= w14])
        c30 = counts([r for r in rws if r["_date"] >= w30])
        c90 = counts(rws)
        use14 = c7["serious"] < MIN_SERIOUS
        c, spend, window = (c14, spend_14, 14) if use14 else (c7, spend_cur, 7)
        cps = per(spend, c["serious"])
        junk = pct(c["junk"], c["enquiries"])
        freq = (ins_cur or {}).get("freq")
        cpm, cpm_prev = (ins_cur or {}).get("cpm"), (ins_prev or {}).get("cpm")
        cpm_change = round(100.0 * (cpm - cpm_prev) / cpm_prev) if cpm and cpm_prev else None
        rate90 = pct(c90["orders"], c90["serious"])
        dm = all(is_dm(a) for a in asid_list) if asid_list else False
        serious_pct = pct(c["serious"], c["enquiries"])
        suspect = bool(serious_pct and n.get("serious_pct") and c["serious"] >= MIN_SERIOUS
                       and serious_pct >= LABEL_SUSPECT_SHARE and serious_pct >= n["serious_pct"] * LABEL_SUSPECT_X)
        tests = {
            "volume": {"value": c["serious"], "need": ">= {}".format(MIN_SERIOUS), "pass": c["serious"] >= MIN_SERIOUS},
            "cost": {"value": cps, "need": "<= {}".format(round(normal * COST_RATIO)) if normal else None,
                     "vs_normal_pct": round(100.0 * cps / normal - 100) if cps and normal else None,
                     "pass": bool(cps and normal and cps <= normal * COST_RATIO)},
            "junk": {"value": junk, "need": "<= {}".format(n.get("junk_pct")),
                     "pass": junk is not None and n.get("junk_pct") is not None and junk <= n["junk_pct"]},
            "saturation": {"freq": freq, "cpm_change_pct": cpm_change,
                           "need": "freq < {} · CPM <= +{}%".format(FREQ_MAX, int(CPM_RISE_MAX * 100)),
                           "pass": (freq is None or freq < FREQ_MAX) and (cpm_change is None or cpm_change <= CPM_RISE_MAX * 100)},
            "orders": {"orders_30d": c30["orders"], "order_rate_90d": rate90, "need_rate": n.get("order_rate_pct"),
                       "pass": c30["orders"] >= 1 or bool(c90["serious"] >= MIN_SERIOUS and rate90 is not None
                                                          and n.get("order_rate_pct") is not None and rate90 >= n["order_rate_pct"])},
        }
        if dm:
            verdict = "not measurable (DM)"
        elif suspect:
            verdict = "check labels"
        elif all(t["pass"] for t in tests.values()):
            verdict = "winner"
        elif tests["cost"]["pass"] and tests["junk"]["pass"]:
            verdict = "promising"
        elif not tests["volume"]["pass"] and not (normal and spend >= normal * MIN_SERIOUS):
            # Too little spent to have reached the volume at a normal cost.
            verdict = "too early"
        else:
            verdict = "no"
        out = {"level": level, "id": eid, "name": name, "market": market, "window_days": window,
               "spend": round(spend), "enquiries": c["enquiries"], "serious": c["serious"], "serious_pct": serious_pct,
               "cost_per_serious": cps, "junk_pct": junk, "orders_30d": c30["orders"],
               "frequency": round(freq, 2) if freq else None, "cpm_change_pct": cpm_change,
               "tests": tests, "verdict": verdict, "spend_90d": round(spend_90),
               "serious_90d": c90["serious"], "status": status.get(eid)}
        out.update(extra)
        return out

    rows_by = {"ad": collections.defaultdict(list), "adset": collections.defaultdict(list),
               "campaign": collections.defaultdict(list)}
    for r in rows:
        rows_by["ad"][r["source_id"]].append(r)
        rows_by["adset"][r["_adset"]].append(r)
        rows_by["campaign"][(r["_campaign"], adset_market(r["_adset"]))].append(r)
    ins = {lvl: {r[lvl + "_id"]: r for r in cur[lvl]} for lvl in cur}
    ins_prev = {lvl: {r[lvl + "_id"]: r for r in prev[lvl]} for lvl in prev}

    def ins_of(rec):
        return {"freq": float(rec.get("frequency") or 0) or None, "cpm": float(rec.get("cpm") or 0) or None} if rec else None

    results = {"ad": [], "adset": [], "campaign": []}

    for asid, a in adsets.items():
        if a.get("effective_status") != "ACTIVE":
            continue
        ad_ids = [i for i, x in ads.items() if x.get("adset_id") == asid]
        s = lambda src: sum(src.get(i, {}).get("spend", 0) for i in ad_ids)  # noqa: E731
        budget = float(a.get("daily_budget") or 0) / 100 or None
        camp = campaigns.get(a.get("campaign_id")) or {}
        cbo = float(camp.get("daily_budget") or 0) / 100 or None
        e = entity("adset", asid, a.get("name"), [asid], rows_by["adset"][asid],
                   sum(spend7_by_ad.get(i, 0) for i in ad_ids), s(ad14), s(ad90),
                   ins_of(ins["adset"].get(asid)), ins_of(ins_prev["adset"].get(asid)), adset_market(asid),
                   {"kind": kind_of(a.get("name")), "campaign": camp.get("name"),
                    "daily_budget": budget, "campaign_budget": cbo, "created": (a.get("created_time") or "")[:10]})
        mn = (normals.get(e["market"]) or {}).get("min_daily_budget")
        cur_budget = budget or cbo
        if e["verdict"] == "winner" and cur_budget and not (e.get("status") or {}).get("date", "") > days_ago(7):
            e["proposal"] = {"budget_level": "ad set" if budget else "campaign", "from": round(cur_budget),
                             "to": int(round(cur_budget * (1 + RAISE_STEP), -1))}
        e["below_minimum"] = bool(mn and budget and budget < mn and e["kind"] == "regular")
        results["adset"].append(e)

    for ad_id, a in ads.items():
        if a.get("effective_status") != "ACTIVE":
            continue
        asid = a.get("adset_id")
        e = entity("ad", ad_id, a.get("name"), [asid], rows_by["ad"][ad_id],
                   spend7_by_ad.get(ad_id, 0), ad14.get(ad_id, {}).get("spend", 0), ad90.get(ad_id, {}).get("spend", 0),
                   ins_of(ins["ad"].get(ad_id)), ins_of(ins_prev["ad"].get(ad_id)), adset_market(asid),
                   {"kind": kind_of((adsets.get(asid) or {}).get("name")), "adset": (adsets.get(asid) or {}).get("name"),
                    "adset_id": asid, "created": (a.get("created_time") or "")[:10]})
        sibling_spend = sum(v for i, v in spend7_by_ad.items() if (ads.get(i) or {}).get("adset_id") == asid)
        e["share_of_adset_spend_pct"] = pct(spend7_by_ad.get(ad_id, 0), sibling_spend)
        results["ad"].append(e)

    for cid, c in campaigns.items():
        if c.get("effective_status") != "ACTIVE":
            continue
        as_ids = [i for i, x in adsets.items() if x.get("campaign_id") == cid and x.get("effective_status") == "ACTIVE"]
        for m in sorted(set(adset_market(i) for i in as_ids)):
            m_as = [i for i in as_ids if adset_market(i) == m]
            m_ads = [i for i, x in ads.items() if x.get("adset_id") in m_as]
            s = lambda src: sum(src.get(i, {}).get("spend", 0) for i in m_ads)  # noqa: E731
            single = len(set(adset_market(i) for i in as_ids)) == 1
            e = entity("campaign", cid, c.get("name"), m_as, rows_by["campaign"][(cid, m)],
                       sum(spend7_by_ad.get(i, 0) for i in m_ads), s(ad14), s(ad90),
                       ins_of(ins["campaign"].get(cid)) if single else None,
                       ins_of(ins_prev["campaign"].get(cid)) if single else None, m,
                       {"adsets": len(m_as), "campaign_budget": float(c.get("daily_budget") or 0) / 100 or None})
            results["campaign"].append(e)

    # Fair-chance pause check, ads and ad sets.
    today = datetime.now(IST).date()
    for lvl in ("ad", "adset"):
        for e in results[lvl]:
            n = normals.get(e["market"]) or {}
            normal = n.get("cost_per_serious")
            if e["verdict"] == "not measurable (DM)" or not e.get("created"):
                continue
            if n.get("thin") or not normal:
                e["fair_chance"] = {"verdict": "normal too thin"}
                continue
            age = (today - datetime.strptime(e["created"], "%Y-%m-%d").date()).days
            need = round(normal * PAUSE_SPEND_X)
            cps90 = per(e["spend_90d"], e["serious_90d"])
            weak = e["serious_90d"] <= 1 or bool(cps90 and cps90 >= normal * PAUSE_COST_X)
            chance = age >= PAUSE_DAYS and e["spend_90d"] >= need
            e["fair_chance"] = {"days_active": age, "need_days": PAUSE_DAYS, "spent": e["spend_90d"],
                                "need_spend": need, "serious": e["serious_90d"], "cost_per_serious": cps90,
                                "weak": weak, "chance_used": chance,
                                "verdict": "pause candidate" if weak and chance else (
                                    "fair chance not used" if weak else "ok")}

    # Budget size vs results, per country.
    # Ad-level weeks summed to the ad set, without the suspect-label ads.
    wk_spend = collections.Counter()
    for w in weekly_ads:
        if w.get("ad_id") not in suspect_ads:
            wk_spend[(w.get("adset_id"), w["date_start"], w["date_stop"])] += float(w.get("spend") or 0)
    weekly = [{"adset_id": k[0], "date_start": k[1], "date_stop": k[2], "spend": v} for k, v in wk_spend.items()]
    wk_rows = collections.defaultdict(list)
    for r in rows:
        if r["source_id"] not in suspect_ads:
            wk_rows[r["_adset"]].append(r)
    bands = collections.defaultdict(lambda: collections.defaultdict(lambda: {"adset_weeks": 0, "spend": 0.0,
                                                                              "enquiries": 0, "serious": 0, "junk": 0}))
    for w in weekly:
        asid = w.get("adset_id")
        if not asid or asid not in adsets or is_dm(asid):
            continue
        start, stop = w["date_start"], w["date_stop"]
        ndays = (datetime.strptime(stop, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1
        spend = float(w.get("spend") or 0)
        if spend <= 0 or ndays < 7 or start < w90:
            continue
        avg = spend / ndays
        label = next("{}-{}".format(lo, hi) if hi else "{}+".format(lo) for lo, hi in SPEND_BANDS if avg >= lo and (hi is None or avg < hi))
        c = counts([r for r in wk_rows[asid] if start <= r["_date"] <= stop])
        b = bands[adset_market(asid)][label]
        b["adset_weeks"] += 1
        b["spend"] += spend
        for k in ("enquiries", "serious", "junk"):
            b[k] += c[k]
    budget_size = {}
    for m, bb in bands.items():
        budget_size[m] = []
        for lo, hi in SPEND_BANDS:
            label = "{}-{}".format(lo, hi) if hi else "{}+".format(lo)
            if label in bb:
                b = bb[label]
                budget_size[m].append({"band": label, "adset_weeks": b["adset_weeks"], "spend": round(b["spend"]),
                                       "serious": b["serious"], "cost_per_serious": per(b["spend"], b["serious"]),
                                       "junk_pct": pct(b["junk"], b["enquiries"])})

    report = {
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {"last7": [w7, yday], "last14": [w14, yday], "normal_since": w90},
        "rules": {"min_serious": MIN_SERIOUS, "cost_ratio": COST_RATIO, "freq_max": FREQ_MAX,
                  "cpm_rise_max_pct": int(CPM_RISE_MAX * 100), "pause_days": PAUSE_DAYS,
                  "pause_spend_x": PAUSE_SPEND_X, "pause_cost_x": PAUSE_COST_X, "raise_step_pct": int(RAISE_STEP * 100),
                  "min_serious_per_week": MIN_SERIOUS_PER_WEEK, "min_normal_serious": MIN_NORMAL_SERIOUS,
                  "label_suspect_share": LABEL_SUSPECT_SHARE, "label_suspect_x": LABEL_SUSPECT_X},
        "feed": {k: feed.get(k) for k in ("generated_at", "rules_version", "validated", "count")},
        "normals": normals,
        "left_out_of_normals": sorted((ads.get(i) or {}).get("name", i) for i in suspect_ads),
        "campaigns": results["campaign"], "adsets": results["adset"], "ads": results["ad"],
        "budget_size": budget_size,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "latest.json")
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)
    if args.print:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        tally = collections.Counter((e["level"], e["market"], e["verdict"]) for lvl in results.values() for e in lvl)
        print("winners_report: wrote {} ({})".format(path, ", ".join("{} {} {}: {}".format(*k, v) for k, v in sorted(tally.items()))))


if __name__ == "__main__":
    main()
