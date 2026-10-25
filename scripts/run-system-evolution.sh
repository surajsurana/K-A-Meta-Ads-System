#!/usr/bin/env bash
# Monthly system evolution review - invoked by cron on the droplet (00:20 IST
# on the 3rd) or by hand. See prompts/system-evolution.md.
JOB_NAME="system-evolution"
PROMPT_FILE="prompts/system-evolution.md"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
