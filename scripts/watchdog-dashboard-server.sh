#!/usr/bin/env bash
# Keeps the K&A Ops Console dashboard's static file server alive on the
# droplet, same supervision pattern as watchdog-telegram-listener.sh: a
# per-minute cron check restarts it via pgrep if it's not running. The
# server itself is just `python3 -m http.server` - no framework, nothing to
# configure - serving ~/ka-meta-ads-dashboard (index.html + ad-thumbs/) on
# port 8090, confirmed reachable externally (no droplet firewall rule
# blocks it - verified live 2026-09-11, no sudo/root needed for any of this).
#
# Not cron-scheduled to regenerate content - this refreshes only when a
# session republishes ~/ka-meta-ads-dashboard/index.html by hand, same as
# the Claude-hosted artifact version. See docs/architecture.md for the
# artifact version's URL and how the two stay in sync.

DASH_DIR="$HOME/ka-meta-ads-dashboard"
LOG_DIR="$HOME/ka-meta-ads-logs"
LOG_FILE="$LOG_DIR/dashboard-server.log"
PORT=8090

mkdir -p "$LOG_DIR"

if ! pgrep -f "http.server $PORT" > /dev/null; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] dashboard server not running, restarting" >> "$LOG_FILE"
  cd "$DASH_DIR" || exit 1
  nohup python3 -m http.server "$PORT" --directory "$DASH_DIR" >> "$LOG_FILE" 2>&1 &
  disown
fi
