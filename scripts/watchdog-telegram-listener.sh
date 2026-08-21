#!/usr/bin/env bash
# Restarts scripts/telegram_approval_listener.py if it isn't running.
# Called every minute from kmetaads' own crontab (alongside a @reboot entry
# for the immediate-start case) — a root-free watchdog pattern, not systemd,
# consistent with why this whole system uses plain user crontab (see
# docs/proactive-operations.md SS6). The listener's own singleton file lock
# (scripts/telegram_approval_listener.py, LISTENER_LOCK_FILE) is what
# actually prevents two copies running concurrently; this script only avoids
# spawning a redundant one needlessly.

set -uo pipefail

REPO_DIR="${KA_REPO_DIR:-$HOME/ka-meta-ads/repo}"
LOG_DIR="${KA_LOG_DIR:-$HOME/ka-meta-ads-logs}"
WATCHDOG_LOG="$LOG_DIR/telegram-watchdog.log"
mkdir -p "$LOG_DIR"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

if pgrep -f "telegram_approval_listener.py" >/dev/null 2>&1; then
  exit 0  # already running, nothing to do
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] listener not running, restarting" >> "$WATCHDOG_LOG"
cd "$REPO_DIR" || { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] repo dir $REPO_DIR missing, cannot restart" >> "$WATCHDOG_LOG"; exit 1; }
nohup python3 "$REPO_DIR/scripts/telegram_approval_listener.py" >> "$LOG_DIR/telegram-listener-stdout.log" 2>&1 &
disown
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] restarted, pid $!" >> "$WATCHDOG_LOG"
