#!/usr/bin/env python3
"""One-time Google sign-in for the K&A Google Ads / Merchant Center integration.

Run this ON YOUR OWN PC (not the droplet), once:
    py -3 scripts/google_oauth_setup.py path\\to\\client_secret_XXXX.json

It opens a browser, you sign in as the Google account that has access to the
Ads account (4661256801) and Merchant Center (5295316798), approve, and it
writes google_ads_credentials.json (gitignored - never commit it). That file
holds a long-lived refresh token = full control of the Ads account, so it is
copied to the droplet with restrictive permissions and used read-only until a
write path is built behind the Telegram-approval gate.

Stdlib only. The client secret and refresh token are never printed.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPES = [
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/content",
]
OUT_FILE = "google_ads_credentials.json"
GOOGLE_ADS_CUSTOMER_ID = "4661256801"
MERCHANT_CENTER_ID = "5295316798"


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: google_oauth_setup.py <client_secret.json>")
    with open(sys.argv[1], encoding="utf-8") as f:
        raw = json.load(f)
    client = raw.get("installed") or raw.get("web")
    if not client:
        sys.exit("That file isn't an OAuth client JSON (expected an 'installed' Desktop-app client).")

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                result["code"] = q["code"][0]
                msg = "Signed in. You can close this tab and go back to the terminal."
            else:
                result["error"] = q.get("error", ["unexpected response"])[0]
                msg = "Sign-in did not complete: " + result["error"]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = "http://127.0.0.1:{}".format(server.server_port)
    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    print("Opening your browser. Sign in as the Google account that manages the Ads account and Merchant Center.")
    print("If it doesn't open, paste this into a browser:\n" + url + "\n")
    threading.Thread(target=server.handle_request, daemon=True).start()
    webbrowser.open(url)
    for _ in range(600):
        if result:
            break
        threading.Event().wait(0.5)
    if "code" not in result:
        sys.exit("No authorisation received ({}).".format(result.get("error", "timed out")))

    body = urllib.parse.urlencode({
        "code": result["code"],
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(client.get("token_uri", "https://oauth2.googleapis.com/token"), data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tok = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit("Token exchange failed: {} {}".format(e.code, e.read().decode()[:300]))
    if "refresh_token" not in tok:
        sys.exit("Google returned no refresh token. Revoke the app at myaccount.google.com/permissions and run again.")

    creds = {
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "refresh_token": tok["refresh_token"],
        "scopes": SCOPES,
        "google_ads_customer_id": GOOGLE_ADS_CUSTOMER_ID,
        "merchant_center_id": MERCHANT_CENTER_ID,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2)
    try:
        os.chmod(OUT_FILE, 0o600)
    except OSError:
        pass
    print("Done. Wrote {} (keep it private, it is gitignored). Nothing secret was printed.".format(OUT_FILE))


if __name__ == "__main__":
    main()
