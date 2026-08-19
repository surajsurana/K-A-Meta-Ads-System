#!/usr/bin/env bash
# Monthly Strategic Intelligence Review — invoked by cron on the droplet. See docs/proactive-operations.md.
JOB_NAME="monthly-strategic-review"
PROMPT_FILE="prompts/monthly-strategic-review.md"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
