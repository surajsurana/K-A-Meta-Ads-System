#!/usr/bin/env bash
# Lead-quality SOS run - started by scripts/lead_quality_sos.py when Suraj
# presses the SOS button (Telegram or the Ops Console dashboard), never by
# cron. Read-only investigation; see prompts/lead-quality-sos.md.
JOB_NAME="lead-quality-sos"
PROMPT_FILE="prompts/lead-quality-sos.md"
# Shares the run lock with every scheduled job (they all hard-sync the same
# working directory). Wait behind a running weekly review (up to its 70-min
# cap) rather than failing after the default 5 minutes.
export KA_LOCK_WAIT_S=4500
SOS_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lead_quality_sos.py"
trap 'python3 "$SOS_PY" finish >/dev/null 2>&1' EXIT
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
