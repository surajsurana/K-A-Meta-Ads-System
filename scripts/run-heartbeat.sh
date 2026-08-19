#!/usr/bin/env bash
# Daily heartbeat — invoked by cron on the droplet. See docs/proactive-operations.md.
JOB_NAME="heartbeat"
PROMPT_FILE="prompts/daily-heartbeat.md"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
