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
      # --data-urlencode, not -d: plain -d treats a literal & in the value as
      # a field separator (application/x-www-form-urlencoded semantics) -
      # confirmed live 2026-08-20, "K&A Meta Ads..." was silently truncated
      # to just "K" because of the & in the brand name itself. This isn't a
      # cosmetic bug — it would silently mangle every real notification.
      curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode chat_id="${TELEGRAM_CHAT_ID}" \
        --data-urlencode text="K&A Meta Ads ${JOB_NAME}: infrastructure failure — ${reason}. Check ${LOG_FILE} on the droplet." \
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
  # confused, which is the real evidence this approach is sound.
  #
  # Corrected again 2026-08-20 after two consecutive real weekly-review
  # runs: every Stitchflow MCP tool call was denied with "Claude Code is
  # running in do not ask mode" because mcp__stitchflow__* was never in
  # --allowedTools at all — same root cause as the Bash issue, different
  # tool category. dontAsk mode auto-denies anything not explicitly
  # listed; it doesn't fail open. Fixed and verified directly against the
  # live MCP server (real data returned) before committing this.
  #
  # Added 2026-08-20, before the first monthly-review test rather than
  # after a failure: campaign-strategist's strategic-intelligence duty
  # (competitor research via Meta Ad Library, platform-change research)
  # requires WebSearch, per its own tool grant in .claude/agents/
  # campaign-strategist.md. Same class of gap as the Bash/Stitchflow fixes
  # — caught by inspection this time, not by a real run failing first.
  #
  # Write is deliberately NOT in this list. Every learning-log write goes
  # through scripts/append-learning-log.sh (a Bash call, already allowed);
  # leaving plain Write off headless runs reinforces that discipline at
  # the tool-permission layer too, not just as a written instruction.
  #
  # Known remaining gap, not fixed here: the Shopify MCP server creative-
  # copywriter uses is registered at a broader claude.ai-account level,
  # not in this project's .mcp.json, and isn't available on the droplet at
  # all. Only matters if a headless run's plan needs new ad copy — if that
  # happens, creative-copywriter will hit the same class of denial and
  # should report it plainly, same as Stitchflow did, rather than guessing.
  #
  # Never uses --bare: bare mode skips auto-discovery of .claude/agents, hooks, and MCP
  # servers, which would silently break subagent dispatch entirely, and it
  # also doesn't read CLAUDE_CODE_OAUTH_TOKEN at all.
  #
  # CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS + outer `timeout`: found live
  # 2026-08-24 - the CLI's default 600s (10min) background-task wait ceiling
  # terminated a weekly review mid-run, right after media-buyer finished
  # validating 6 real plans but before the session reached the notification
  # step - no digest, no approval buttons were sent, and the wrapper's own
  # exit-code check didn't catch it either (claude still exited 0, since
  # "background tasks terminated" isn't itself a process failure). Raised
  # to 1800000/2400s that day. Found live AGAIN 2026-08-31: the 2400s outer
  # timeout itself was now too short - a real weekly review with the current
  # scope (geo/demographic analysis, the held-plan light-recheck sweep,
  # UGC/content review, campaign-strategist's full disposition pass) did
  # ~45 minutes of genuine, valuable work (23 real learning-log entries,
  # including 2 live executions) before being killed mid-stream, almost
  # certainly before it reached the guaranteed weekly-digest notification -
  # not a hang, just legitimately more work than the cap allowed for.
  #
  # Split per job rather than one shared number: heartbeat is deliberately
  # meant to stay cheap (daily, narrow scope) so keeps a modest ceiling: a
  # heartbeat that's actually taking too long is more likely stuck than
  # doing real proportionate work, and daily cadence means a stuck one
  # would otherwise hold the shared lock 24x longer than intended. Weekly/
  # monthly get generous headroom given how much real work they now do,
  # and their low frequency (weekly/monthly, not daily) means a longer cap
  # doesn't compound into meaningfully more total blocked-time risk.
  #
  # Heartbeat's own cap raised 20min -> 30min 2026-09-05, same class of
  # finding as the two above, not a hang: the account's learning log has
  # grown enough (253 entries at the time) that the due-now sweep alone was
  # taking real time, pushing a genuinely-completing, legitimate run right
  # up against 20 minutes - it got killed by this exact timeout twice in
  # one week (2026-09-02, 2026-09-05) despite finishing cleanly in ~14min
  # when given room to run. The due-now sweep itself was also fixed same
  # day (knowledge/open-followups.json - see scripts/update-open-
  # followups.py) to stop re-scanning the whole growing log every day, so
  # this raise is a margin-of-safety on top of that fix, not a substitute
  # for it - the two together are what actually keep this cheap long-term.
  case "$JOB_NAME" in
    heartbeat)
      BG_WAIT_CEILING_MS=600000    # 10 min - back to the CLI's own default; heartbeat should never need the raised ceiling
      OUTER_TIMEOUT_S=1800         # 30 min hard cap (was 20min - see note above)
      ;;
    *)
      BG_WAIT_CEILING_MS=3000000   # 50 min
      OUTER_TIMEOUT_S=4200         # 70 min hard cap - NOT unlimited, so an actually-stuck run still eventually releases
                                    # the shared flock lock rather than silently starving every other scheduled run forever
      ;;
  esac
  export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="$BG_WAIT_CEILING_MS"
  {
    # stdbuf -oL -eL: force line-buffered stdout/stderr instead of the fully-
    # buffered default when output isn't a TTY. Found live 2026-08-31 - the
    # log file for a run that got killed by the outer `timeout` showed
    # nothing at all between the sync and the failure line, even though real
    # work (23 learning-log entries, 2 live executions) had genuinely
    # happened - the session's own narrative output was sitting in an
    # unflushed buffer when it got killed, so a human reading the log had no
    # way to see how far it actually got. Doesn't change what work happens,
    # only makes a future killed run's partial log actually readable.
    stdbuf -oL -eL timeout "$OUTER_TIMEOUT_S" claude -p "$(cat "$PROMPT_FILE")" \
      --permission-mode dontAsk \
      --allowedTools "Read,Grep,Glob,Bash,WebSearch,mcp__stitchflow__*" \
      2>&1
    CLAUDE_EXIT=$?
    echo "[run-${JOB_NAME}] claude exited $CLAUDE_EXIT"
  } >> "$LOG_FILE" 2>&1

  if [ "${CLAUDE_EXIT:-1}" -ne 0 ]; then
    ka_alert_failure "headless claude run exited non-zero ($CLAUDE_EXIT)"
  elif grep -q "Background tasks still running after" "$LOG_FILE" 2>/dev/null; then
    # Belt-and-suspenders for the exact 2026-08-24 failure mode: the CLI can
    # still exit 0 after force-terminating unfinished background dispatches
    # mid-run (e.g. media-buyer validating plans) - that is NOT a successful
    # run even though the exit code alone says otherwise, and the run's own
    # final printed text can easily look plausible enough that a human
    # skimming the log wouldn't catch it. Alert explicitly rather than
    # trusting exit 0.
    ka_alert_failure "run was cut off mid-task (hit the background-task wait ceiling) - it may have exited 0 but did NOT necessarily finish or notify. Check $LOG_FILE."
  fi

  find "$LOG_DIR" -name "${JOB_NAME}-*.log" -mtime +30 -delete 2>/dev/null

  flock -u 9
  echo "[run-${JOB_NAME}] done" >> "$LOG_FILE"
}
