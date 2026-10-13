#!/usr/bin/env bash
# Weekly pipeline self-check — invoked by cron on the droplet. See docs/proactive-operations.md.
JOB_NAME="weekly-selfcheck"
PROMPT_FILE="prompts/weekly-system-selfcheck.md"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
