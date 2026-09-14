#!/usr/bin/env bash
# Keeps the K&A Ops Console dashboard's LIVE server alive on the droplet,
# same supervision pattern as watchdog-telegram-listener.sh: a per-minute
# cron check restarts it via pgrep if it's not running.
#
# This replaced the old plain `python3 -m http.server` static file server
# (2026-09-13, real user feedback — "this should be a live dashboard").
# live_server.py (~/ka-meta-ads-dashboard/live_server.py) is a small stdlib
# HTTP server that, on each request, serves a 15-minute-cached page built
# from live Meta Ads + Stitchflow pulls (real numbers, not a hand-edited
# snapshot) substituted into template.html. Static ad-thumbs/*.jpg are
# still served directly from disk by the same process. Port 8090,
# confirmed reachable externally (no droplet firewall rule blocks it,
# verified live 2026-09-11, no sudo/root needed for any of this).
#
# template.html and live_server.py itself only change when a session
# edits and redeploys them by hand — this watchdog just keeps the process
# up, it doesn't regenerate their content.

DASH_DIR="$HOME/ka-meta-ads-dashboard"
LOG_DIR="$HOME/ka-meta-ads-logs"
LOG_FILE="$LOG_DIR/dashboard-server.log"
PORT=8090

mkdir -p "$LOG_DIR"

# pkill/pgrep -f matches against the FULL command line of every process,
# including this watchdog script's own invocation if the pattern isn't
# careful — bracket one letter (e.g. "[l]ive_server.py") so the pattern
# never matches its own argv. Bit us once already (session incident,
# 2026-09-13): a plain `pkill -f "http.server 8090"` killed the very SSH
# session running it, since that session's own command line also
# contained the literal string "http.server 8090".
if ! pgrep -f "[l]ive_server.py" > /dev/null; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] dashboard live server not running, restarting" >> "$LOG_FILE"
  cd "$DASH_DIR" || exit 1
  nohup python3 live_server.py >> "$LOG_FILE" 2>&1 &
  disown
fi
