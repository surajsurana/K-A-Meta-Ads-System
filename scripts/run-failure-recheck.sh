#!/usr/bin/env bash
# Same-day failed-execution recheck — invoked by cron on the droplet. See
# docs/proactive-operations.md and prompts/failure-recheck.md.
JOB_NAME="failure-recheck"
PROMPT_FILE="prompts/failure-recheck.md"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
