#!/usr/bin/env python3
"""Read-only connectivity check for the Google Ads + Merchant Center credentials.

    python3 scripts/google_check.py [path/to/google_ads_credentials.json]

Only reads: lists accessible Ads customers, lists campaigns with status and
budget, and confirms Merchant Center access. Makes no changes to any account.
Prints Google's own error text on failure (never credentials), because the
API access rules changed in Sept 2026 and the exact failure tells us what
setup step is still missing (e.g. a developer token).
Stdlib only.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

ADS_VERSION = "v23"  # v22 stops working 2026-10-07 per Google's schedule; bump if v23 is retired


def call(url, token, dev_token="", login_customer="", body=None):
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if dev_token:
        headers["developer-token"] = dev_token
    if login_customer:
        headers["login-customer-id"] = login_customer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "google_ads_credentials.json"
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    tok_req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "client_id": c["client_id"], "client_secret": c["client_secret"],
            "refresh_token": c["refresh_token"], "grant_type": "refresh_token",
        }).encode(),
    )
    try:
        with urllib.request.urlopen(tok_req, timeout=30) as r:
            token = json.loads(r.read().decode())["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit("Could not get an access token: {} {}".format(e.code, e.read().decode()[:300]))
    print("Access token: OK")

    dev = c.get("developer_token", "")
    login = c.get("login_customer_id", "")
    cust = c["google_ads_customer_id"]
    base = "https://googleads.googleapis.com/{}".format(ADS_VERSION)

    print("\n== Google Ads ==")
    st, out = call(base + "/customers:listAccessibleCustomers", token, dev, login)
    print("listAccessibleCustomers:", st, out if st != 200 else out.get("resourceNames"))
    gaql = ("SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
            "campaign_budget.amount_micros FROM campaign WHERE campaign.status != 'REMOVED'")
    st, out = call(base + "/customers/{}/googleAds:search".format(cust), token, dev, login, {"query": gaql})
    if st == 200:
        rows = out.get("results", [])
        print("campaigns:", len(rows))
        for r in rows:
            cp = r.get("campaign", {})
            bud = int(r.get("campaignBudget", {}).get("amountMicros", 0)) / 1e6
            print("  {} | {} | {} | {} | budget/day {:.2f}".format(
                cp.get("id"), cp.get("name"), cp.get("status"), cp.get("advertisingChannelType"), bud))
    else:
        print("campaign query:", st, out)

    print("\n== Merchant Center ==")
    mid = c["merchant_center_id"]
    st, out = call("https://merchantapi.googleapis.com/accounts/v1/accounts/{}".format(mid), token)
    print("account:", st, out if st != 200 else out.get("accountName"))
    st, out = call("https://merchantapi.googleapis.com/products/v1/accounts/{}/products?pageSize=1".format(mid), token)
    print("products read:", st, "OK" if st == 200 else out)


if __name__ == "__main__":
    main()
