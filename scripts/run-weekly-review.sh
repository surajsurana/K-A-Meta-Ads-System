#!/usr/bin/env bash
# Weekly full account review — invoked by cron on the droplet. See docs/proactive-operations.md.
JOB_NAME="weekly-review"
PROMPT_FILE="prompts/weekly-review.md"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
