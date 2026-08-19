#!/usr/bin/env bash
# Shared logic for the three run-*.sh wrapper scripts (heartbeat, weekly,
# monthly). Sourced, not executed directly. Kept as one shared file rather
# than triplicated inline logic, specifically so a fix/change only needs to
# happen once instead of three times independently drifting.
#
# A calling script must set JOB_NAME and PROMPT_FILE, then call:
#   ka_run_headless
# before this file is sourced (or right after), e.g.:
#   JOB_NAME="heartbeat"
#   PROMPT_FILE="prompts/daily-heartbeat.md"
#   source "$(dirname "${BASH_SOURCE[0]}")/_run-common.sh"
#   ka_run_headless

set -uo pipefail  # not -e: a mid-script failure must still reach the alert path

# cron runs with a minimal, non-login environment — nvm's PATH setup (node,
# npm, claude) only loads via .bashrc, which cron never sources. Load it
# explicitly here rather than assuming `claude` is just on PATH.
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

REPO_DIR="${KA_REPO_DIR:-$HOME/ka-meta-ads/repo}"
LOG_DIR="${KA_LOG_DIR:-$HOME/ka-meta-ads-logs}"          # sibling to the repo, never inside it
LOCK_FILE="${KA_LOCK_FILE:-$HOME/ka-meta-ads-logs/.run.lock}"

mkdir -p "$LOG_DIR"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
LOG_FILE="$LOG_DIR/${JOB_NAME}-${TS}.log"

ka_alert_failure() {
  local reason="$1"
  {
    echo "[run-${JOB_NAME}] FAILURE: $reason"
    echo "[run-${JOB_NAME}] See $LOG_FILE for details."
  } >> "$LOG_FILE"
  # Works even if the sync or the Claude run itself failed outright — this
  # path does not depend on the agent having run, only on the telegram
  # config file existing in the repo working directory (gitignored, placed
  # once during deployment, survives `git reset --hard` since it's untracked).
  if [ -f "$REPO_DIR/telegram_config.txt" ]; then
    # shellcheck disable=SC1090
    source "$REPO_DIR/telegram_config.txt"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
      curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="K&A Meta Ads ${JOB_NAME}: infrastructure failure — ${reason}. Check ${LOG_FILE} on the droplet." \
        >> "$LOG_FILE" 2>&1
    fi
  fi
}

ka_run_headless() {
  # Concurrency guard: only one headless run touches the shared working
  # directory at a time, across all three job types, not just same-job.
  exec 9>"$LOCK_FILE"
  if ! flock -w 300 9; then
    ka_alert_failure "could not acquire run lock within 5 minutes — another run may be stuck"
    exit 1
  fi

  cd "$REPO_DIR" || { ka_alert_failure "repo directory $REPO_DIR not found"; exit 1; }

  {
    echo "[run-${JOB_NAME}] starting $TS"
    # Hard sync to the exact pushed state. Safe specifically because a prior
    # run only ever ends with zero uncommitted/unpushed local state —
    # append-learning-log.sh fails loudly rather than leaving an unpushed
    # commit behind, so there is nothing for reset --hard to destroy here.
    git fetch origin main 2>&1
    git reset --hard origin/main 2>&1
  } >> "$LOG_FILE" 2>&1

  if ! git rev-parse --verify origin/main >/dev/null 2>&1; then
    ka_alert_failure "git sync failed — origin/main not reachable"
    flock -u 9
    exit 1
  fi

  # Non-interactive auth: CLAUDE_CODE_OAUTH_TOKEN, generated once via
  # `claude setup-token` on an already-authenticated interactive machine,
  # draws from the existing subscription rather than opening a separate
  # metered API bill. Read fresh from the gitignored token file each run,
  # same pattern as meta_token.txt.txt — never committed, never logged.
  if [ ! -f "$REPO_DIR/oauth_token_do_not_commit.txt.txt" ]; then
    ka_alert_failure "oauth_token_do_not_commit.txt.txt not found in $REPO_DIR — cannot authenticate"
    flock -u 9
    exit 1
  fi
  export CLAUDE_CODE_OAUTH_TOKEN
  CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '[:space:]' < "$REPO_DIR/oauth_token_do_not_commit.txt.txt")"

  # Corrected 2026-08-19 after a real first test: narrow Bash(curl ...)
  # allowlist patterns blocked ALL network calls, not just writes — a
  # shell-pattern allowlist can't reliably distinguish a GET from a POST
  # anyway, so it was never a real write-blocker, just a source of false
  # negatives. Bash is broad here; the actual safety control is the prompt
  # itself (never dispatch marketing-lead's execution protocol, never call
  # a Meta/Instagram write endpoint) — the same test run proved that
  # instruction holds even when the agent is completely blocked and
  # confused, which is the real evidence this approach is sound. Never uses
  # --bare: bare mode skips auto-discovery of .claude/agents, hooks, and MCP
  # servers, which would silently break subagent dispatch entirely, and it
  # also doesn't read CLAUDE_CODE_OAUTH_TOKEN at all.
  {
    claude -p "$(cat "$PROMPT_FILE")" \
      --permission-mode dontAsk \
      --allowedTools "Read,Grep,Glob,Bash" \
      2>&1
    CLAUDE_EXIT=$?
    echo "[run-${JOB_NAME}] claude exited $CLAUDE_EXIT"
  } >> "$LOG_FILE" 2>&1

  if [ "${CLAUDE_EXIT:-1}" -ne 0 ]; then
    ka_alert_failure "headless claude run exited non-zero ($CLAUDE_EXIT)"
  fi

  find "$LOG_DIR" -name "${JOB_NAME}-*.log" -mtime +30 -delete 2>/dev/null

  flock -u 9
  echo "[run-${JOB_NAME}] done" >> "$LOG_FILE"
}
