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

  # Flags are indicative — verify exact syntax against the installed CLI
  # version during the first manual test (docs/proactive-operations.md §10)
  # before trusting this unattended. Scoped to read-only Meta/Instagram
  # calls, the learning-log script, and the Telegram alert path — never a
  # Meta/Instagram write endpoint; enforced primarily by the prompt itself,
  # this tool scoping is defense-in-depth, not the sole control.
  {
    claude -p "$(cat "$PROMPT_FILE")" \
      --permission-mode dontAsk \
      --allowedTools "Read,Grep,Glob,Bash(cat *),Bash(curl -s -G *),Bash(curl -s -X POST https://api.telegram.org/*),Bash(scripts/append-learning-log.sh *),Bash(git *)" \
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
