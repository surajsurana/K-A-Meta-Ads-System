#!/usr/bin/env bash
# Monthly Strategic Intelligence Review — invoked by cron on the droplet. See docs/proactive-operations.md.
JOB_NAME="monthly-strategic-review"
PROMPT_FILE="prompts/monthly-strategic-review.md"
# Shares the run lock with every scheduled job (they all hard-sync the same
# working directory). Cron fires this only 15 minutes after weekly-review's
# own Monday slot, so whenever the 2nd of the month falls on a Monday the
# two jobs collide; wait behind a running weekly review (up to
# its 70-min cap) rather than failing after the default 5 minutes, same as
# lead-quality-sos.
export KA_LOCK_WAIT_S=4500
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_run-common.sh"
ka_run_headless
