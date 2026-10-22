#!/usr/bin/env python3
"""Enquiry-quality report per Meta ad / ad set / campaign.

Joins AI Sales Intelligence's ad-lead quality feed (one row per WhatsApp
enquiry, labelled junk / browsing / serious / converted, with Stitchflow
outcomes) to this account's ads and their spend, so decisions can be made
on cost per SERIOUS enquiry and per ORDER, not cost per conversation.
Backlog #5, built 2026-10-22.

Read-only: one GET to the AISI feed, Meta GETs only. Stdlib only, runs
on the droplet (that's where the feed secret lives:
~/ka-meta-ads/aisi_feed_secret.txt, chmod 600, never in git).

Privacy: the feed carries no names, phones or message text (anon_id is an
HMAC that only AISI can reverse). This script still writes NOTHING
per-enquiry into the repo - only aggregates, and only to
~/ka-meta-ads/lead-quality/ (outside git).

Usage:
  python3 scripts/lead_quality_report.py [--since YYYY-MM-DD] [--mature-only]
Prints a JSON summary to stdout and saves it as
~/ka-meta-ads/lead-quality/latest.json.
"""

import argparse
import collections
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("KA_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME_DIR = os.path.expanduser("~/ka-meta-ads")
SECRET_FILE = os.path.join(HOME_DIR, "aisi_feed_secret.txt")
OUT_DIR = os.path.join(HOME_DIR, "lead-quality")
FEED_URL = "https://stitchflow.in/api/ai-sales-intelligence/webhooks/ad-lead-quality/ka-internal"
GRAPH = "https://graph.facebook.com/v21.0"
ACCOUNT = "act_686170454752172"
LABELS = ("junk", "browsing", "serious", "converted")
# The feed is WhatsApp-only. Ad sets that send people to Instagram DMs or
# Messenger get few or no rows, so their spend would wrongly inflate the
# WhatsApp cost per serious enquiry (caught 2026-10-22: ~Rs1.29L of DM
# remarketing spend doubled the headline). They're kept out of the overall
# totals and reported on their own until DM leads are added (backlog #6).
NON_WHATSAPP_DESTINATIONS = ("INSTAGRAM_DIRECT", "MESSENGER")
# The WhatsApp attribution window the feed starts from (AISI's webhook log
# begins 2026-08-29; earlier rows have no source_id).
DEFAULT_SINCE = "2026-08-29"


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def http_json(url, headers=None, timeout=90):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def meta_get(path, params, token):
    params = dict(params, access_token=token)
    return http_json("{}/{}?{}".format(GRAPH, path, urllib.parse.urlencode(params)), timeout=60)


def meta_paged(path, params, token):
    data = meta_get(path, params, token)
    while True:
        for row in data.get("data", []):
            yield row
        nxt = (data.get("paging") or {}).get("next")
        if not nxt:
            return
        data = http_json(nxt, timeout=60)


def fetch_feed(since):
    secret = read_text(SECRET_FILE)
    url = "{}?{}".format(FEED_URL, urllib.parse.urlencode({"since": since}))
    return http_json(url, headers={"X-Feed-Secret": secret}, timeout=120)


def ad_lookup(token, ad_ids):
    """ad_id -> names/ids of the ad, its ad set and campaign. Batched GET."""
    out = {}
    ids = sorted(ad_ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        res = meta_get("", {
            "ids": ",".join(chunk),
            "fields": "name,effective_status,adset{id,name,destination_type},campaign{id,name}",
        }, token)
        for ad_id, a in res.items():
            out[ad_id] = {
                "ad_name": a.get("name", ""),
                "status": a.get("effective_status", ""),
                "adset_id": (a.get("adset") or {}).get("id", ""),
                "adset_name": (a.get("adset") or {}).get("name", ""),
                "destination_type": (a.get("adset") or {}).get("destination_type", ""),
                "campaign_name": (a.get("campaign") or {}).get("name", ""),
            }
    return out


def ad_spend(token, since, until):
    """ad_id -> spend (INR) over the window, from ad-level insights."""
    spend = {}
    rows = meta_paged("{}/insights".format(ACCOUNT), {
        "level": "ad",
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": "ad_id,spend",
        "limit": 500,
    }, token)
    for r in rows:
        spend[r["ad_id"]] = float(r.get("spend") or 0)
    return spend


def new_bucket():
    return {"enquiries": 0, "junk": 0, "browsing": 0, "serious": 0, "converted": 0,
            "prefill_only": 0, "appointments": 0,
            # Suraj, 2026-10-22: an enquiry staff never answered is still judged on
            # what the customer said and still counts FOR the ad - "the ad did its
            # job and the staff didn't". So the label counts above include these;
            # this separate bucket just shows how many good ones went unanswered.
            "not_answered": 0, "not_answered_serious_or_better": 0,
            # Orders: credited to the ad however late they come (AISI matches
            # orders on/after first contact, no upper time limit).
            "orders": 0, "order_value_inr": 0.0,
            "orders_new_customers": 0, "orders_returning_customers": 0,
            "spend_inr": 0.0}


def add_row(b, r):
    b["enquiries"] += 1
    if r.get("label") in LABELS:
        b[r["label"]] += 1
    # rules-v2 also has "prefill-only-unanswered" (labelled browsing, since an
    # unanswered tap can't be judged junk) - count both as prefill-only.
    if (r.get("reason_code") or "").startswith("prefill-only"):
        b["prefill_only"] += 1
    if r.get("staff_replied") is False:
        b["not_answered"] += 1
        if r.get("label") in ("serious", "converted"):
            b["not_answered_serious_or_better"] += 1
    if r.get("appointment_booked"):
        b["appointments"] += 1
    if r.get("has_order"):
        n = int(r.get("order_count") or 1)
        b["orders"] += n
        b["order_value_inr"] += float(r.get("order_value") or 0)
        returning = r.get("existing_customer_before_enquiry")
        if returning is True:
            b["orders_returning_customers"] += n
        elif returning is False:
            b["orders_new_customers"] += n


def finish(b):
    n = b["enquiries"] or 1
    good = b["serious"] + b["converted"]
    s = b["spend_inr"]
    b["junk_pct"] = round(100.0 * b["junk"] / n, 1)
    b["serious_or_better_pct"] = round(100.0 * good / n, 1)
    b["cost_per_enquiry"] = round(s / b["enquiries"], 0) if b["enquiries"] else None
    b["cost_per_serious_or_better"] = round(s / good, 0) if good else None
    b["cost_per_order"] = round(s / b["orders"], 0) if b["orders"] else None
    b["revenue_per_rupee"] = round(b["order_value_inr"] / s, 2) if s else None
    b["spend_inr"] = round(s, 0)
    b["order_value_inr"] = round(b["order_value_inr"], 0)
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--mature-only", action="store_true",
                    help="only enquiries past AISI's maturity window (default 30 days)")
    args = ap.parse_args()

    token = read_text(os.path.join(REPO, "meta_token.txt.txt"))
    feed = fetch_feed(args.since)
    rows = feed.get("rows", [])
    if args.mature_only:
        rows = [r for r in rows if r.get("mature")]
    ad_rows = [r for r in rows if r.get("attribution") == "source_id" and r.get("source_id")]
    other_rows = [r for r in rows if r not in ad_rows]

    today = datetime.now(timezone.utc)
    until = today.strftime("%Y-%m-%d")
    if args.mature_only:
        # Spend must cover the same weeks as the enquiries being judged, or
        # recent spend is divided by older enquiries only (bug caught by
        # campaign-strategist 2026-10-22: costs came out ~2x too high).
        until = (today - timedelta(days=int(feed.get("maturity_days") or 30))).strftime("%Y-%m-%d")
    ads = ad_lookup(token, {r["source_id"] for r in ad_rows})
    spend = ad_spend(token, args.since, until)

    by_ad = collections.defaultdict(new_bucket)
    by_adset = collections.defaultdict(new_bucket)
    by_campaign = collections.defaultdict(new_bucket)
    by_country = collections.defaultdict(new_bucket)
    overall_ads = new_bucket()        # WhatsApp-destination ads only
    non_whatsapp = new_bucket()       # DM/Messenger ad sets, shown separately
    def is_wa(ad_id):
        return ads.get(ad_id, {}).get("destination_type", "") not in NON_WHATSAPP_DESTINATIONS
    for r in ad_rows:
        a = ads.get(r["source_id"], {})
        for b in (by_ad[r["source_id"]], by_adset[a.get("adset_id") or "unknown"],
                  by_campaign[a.get("campaign_name") or "unknown"],
                  by_country[r.get("residence_country") or "unknown"],
                  overall_ads if is_wa(r["source_id"]) else non_whatsapp):
            add_row(b, r)
    # Spend: every ad that produced at least one enquiry, summed up the tree.
    # (Ads with spend but zero enquiries are reported separately below.)
    for ad_id, b in by_ad.items():
        s = spend.get(ad_id, 0.0)
        b["spend_inr"] = s
        a = ads.get(ad_id, {})
        by_adset[a.get("adset_id") or "unknown"]["spend_inr"] += s
        by_campaign[a.get("campaign_name") or "unknown"]["spend_inr"] += s
        (overall_ads if is_wa(ad_id) else non_whatsapp)["spend_inr"] += s

    non_ad = new_bucket()
    for r in other_rows:
        add_row(non_ad, r)

    adset_names = {a["adset_id"]: a["adset_name"] for a in ads.values()}
    adset_dest = {a["adset_id"]: a.get("destination_type", "") for a in ads.values()}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"since": args.since, "until": until, "mature_only": args.mature_only},
        "feed": {k: feed.get(k) for k in ("generated_at", "rules_version", "validated", "validation", "maturity_days", "count")},
        "caveat": ("Labels are spot-checked, not fully validated (see feed.validation: Suraj agreed "
                   "with 25/25 on a non-blind check, 2026-10-22); weight per-ad reads by sample size; "
                   "orders lag up to a day."),
        "ad_enquiries_overall": finish(overall_ads),
        "non_whatsapp_destination_ad_sets": dict(finish(non_whatsapp), note=(
            "Instagram-DM / Messenger ad sets: the feed can't see their DM enquiries, "
            "so cost per serious enquiry here is NOT meaningful (backlog #6).")),
        "non_ad_enquiries_baseline": finish(non_ad),
        "by_campaign": {k: finish(v) for k, v in sorted(by_campaign.items(), key=lambda kv: -kv[1]["enquiries"])},
        "by_adset": {"{} ({})".format(adset_names.get(k, "unknown"), k):
                     dict(finish(v), destination=adset_dest.get(k, ""))
                     for k, v in sorted(by_adset.items(), key=lambda kv: -kv[1]["enquiries"])},
        "by_ad": {"{} ({})".format(ads.get(k, {}).get("ad_name", "unknown"), k):
                  dict(finish(v), status=ads.get(k, {}).get("status", ""),
                       adset=ads.get(k, {}).get("adset_name", ""))
                  for k, v in sorted(by_ad.items(), key=lambda kv: -kv[1]["enquiries"])},
        "by_residence_country": {k: finish(v) for k, v in sorted(by_country.items(), key=lambda kv: -kv[1]["enquiries"])},
        "spend_on_ads_with_no_tracked_enquiry_inr": round(
            sum(s for ad_id, s in spend.items() if ad_id not in by_ad), 0),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
