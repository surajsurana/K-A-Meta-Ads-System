#!/usr/bin/env python3
"""
K&A Meta Ads System — Telegram inline-button approval listener.

Long-polls Telegram's getUpdates for callback_query events (APPROVE/REJECT/
HOLD button taps) on plans sent by scripts/send-telegram-approval.sh, and is
the ONLY thing in this system that turns a Telegram interaction into a live
Meta write — and even then, only by dispatching the existing marketing-lead
execution protocol (headless `claude -p`), never by calling Meta directly
itself. See docs/proactive-operations.md SS9 for the full design rationale.

Stdlib only, deliberately - this is a security-relevant component and every
extra dependency is extra surface area. Runs as a single long-lived process,
supervised by kmetaads' own crontab (@reboot + a minute-by-minute watchdog),
not systemd - no root needed for either.

Usage:
  telegram_approval_listener.py                     # run the poll loop (default)
  telegram_approval_listener.py --record-sent --plan-id ID --chat-id ID --message-id ID
                                                      # record a newly-sent approval request (called by send-telegram-approval.sh)
"""

import fcntl
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.expanduser("~/ka-meta-ads")  # sibling to the repo, never inside it - runtime state, not project code
STATE_FILE = os.path.join(STATE_DIR, "telegram_approvals_state.json")
STATE_LOCK_FILE = os.path.join(STATE_DIR, ".telegram_approvals_state.lock")
LISTENER_LOCK_FILE = os.path.join(STATE_DIR, ".telegram_listener.lock")
LOG_DIR = os.path.expanduser("~/ka-meta-ads-logs")
LOG_FILE = os.path.join(LOG_DIR, "telegram-listener.log")
TELEGRAM_CONFIG = os.path.join(REPO_DIR, "telegram_config.txt")

STALE_AFTER_HOURS = 48  # a plan pending this long is treated as needing fresh eyes, not a stale rubber-stamp
POLL_TIMEOUT_S = 30     # Telegram long-poll timeout
EXEC_TIMEOUT_S = 300    # generous ceiling for a single-plan headless execution; a real one should be far faster

# Approve dispatches run on a background worker thread, never inline in the
# poll loop - found live 2026-08-24: with dispatch running synchronously, a
# SECOND button tap arriving while the first plan's ~60s dispatch was still
# in flight sat queued until the poll loop got back around to it, by which
# point Telegram had already invalidated that callback_query as too old.
# answerCallbackQuery then threw an uncaught HTTP 400, aborting the handler
# mid-flight and leaving state stuck at "claimed_approve" forever (claimed,
# but never finalized, never dispatched, never reported). One worker thread
# (not unlimited concurrency) - this droplet has 458MB RAM shared with
# StockTradingBot and Petty Cash, so dispatches still run one at a time,
# just off the poll loop's critical path so new taps get acknowledged
# immediately regardless of how long a previous dispatch takes.
DISPATCH_QUEUE = queue.Queue()

CALLBACK_PREFIXES = {"A": "approve", "R": "reject", "H": "hold"}


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def _read_config_dict():
    if not os.path.exists(TELEGRAM_CONFIG):
        raise RuntimeError(f"{TELEGRAM_CONFIG} not found")
    cfg = {}
    with open(TELEGRAM_CONFIG) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def load_telegram_config():
    cfg = _read_config_dict()
    token = cfg.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = cfg.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("telegram_config.txt missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    return token, chat_id


def real_execution_enabled():
    """Code-level kill switch for live (non-TEST-) Meta execution via this
    listener, independent of whatever any dispatched LLM session might or
    might not decide on its own. Defaults OFF. This exists because dispatching
    a real plan currently runs into an unresolved architectural question - see
    docs/architecture.md SS3 guardrail 1's 2026-08-21 open-question note - and
    that question is Suraj's to resolve, not something to default to "on" and
    hope a prompt-level instruction is enough to stop. Flip by adding
    TELEGRAM_APPROVAL_REAL_EXECUTION=true to telegram_config.txt (no redeploy
    needed - re-read fresh on every APPROVE)."""
    return _read_config_dict().get("TELEGRAM_APPROVAL_REAL_EXECUTION", "").strip().lower() == "true"


def tg_api(token, method, params, timeout=35):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# --- State store: a single JSON file, locked for every read-modify-write.
# Deliberately a flat file, not sqlite - consistent with this project's own
# stated preference for boring flat files at this scale (see
# docs/learning-layer-design.md SS1's identical reasoning for the learning
# log itself), and the whole state store is at most a few hundred short
# records for the foreseeable future.

def _load_state_locked():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def _save_state_locked(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)  # atomic rename, never a half-written state file


def with_state_lock(fn):
    """Run fn(state) -> (new_state, result) under an exclusive file lock covering
    the entire read-modify-write. This is what makes claim-before-execute safe
    against a rapid double-tap producing two callback_query updates."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_LOCK_FILE, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            state = _load_state_locked()
            new_state, result = fn(state)
            if new_state is not None:
                _save_state_locked(new_state)
            return result
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def record_sent(plan_id, chat_id, message_id):
    def op(state):
        state[plan_id] = {
            "status": "pending",
            "chat_id": str(chat_id),
            "message_id": message_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        return state, None
    with_state_lock(op)
    log(f"recorded pending approval request: {plan_id} (message_id={message_id})")


def claim_for_action(plan_id, action, chat_id):
    """Atomically decide whether this callback should actually be acted on.
    Returns one of: 'claimed' (go ahead, act on it), 'unknown' (plan never
    sent via this system - reject), 'unauthorized' (wrong chat), 'already:<status>'
    (plan was already resolved - do nothing further, just report that back),
    'stale' (past the staleness window, not yet resolved - do not execute)."""
    def op(state):
        entry = state.get(plan_id)
        if entry is None:
            return None, "unknown"
        if str(entry["chat_id"]) != str(chat_id):
            return None, "unauthorized"
        if entry["status"] != "pending":
            # Already approved/rejected/held/executed/failed/expired earlier -
            # this is exactly the double-tap / approve-after-reject / dead-
            # button case. Report the existing outcome, change nothing.
            return None, f"already:{entry['status']}"
        sent_at = datetime.fromisoformat(entry["sent_at"])
        age_h = (datetime.now(timezone.utc) - sent_at).total_seconds() / 3600
        if age_h > STALE_AFTER_HOURS:
            entry["status"] = "expired"
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            state[plan_id] = entry
            return state, "stale"
        # Claim it now, before any slow work happens - this is the line that
        # makes a rapid second callback (processed right after this one,
        # since the poll loop is single-threaded) see status != "pending"
        # and refuse to act again.
        claimed_status = {"approve": "claimed_approve", "reject": "rejected", "hold": "held"}[action]
        entry["status"] = claimed_status
        entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
        state[plan_id] = entry
        return state, "claimed"
    return with_state_lock(op)


def finalize_status(plan_id, final_status, detail=""):
    def op(state):
        entry = state.get(plan_id)
        if entry is not None:
            entry["status"] = final_status
            entry["detail"] = detail
            state[plan_id] = entry
        return state, None
    with_state_lock(op)


def get_entry(plan_id):
    return with_state_lock(lambda state: (None, state.get(plan_id)))


# --- Learning log helpers (read-only checks here; writes always go through
# scripts/append-learning-log.sh, never direct file edits, same discipline
# as every agent in this system).

def append_learning_log(entry_dict):
    line = json.dumps(entry_dict, ensure_ascii=False)
    result = subprocess.run(
        [os.path.join(REPO_DIR, "scripts", "append-learning-log.sh"), line],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        log(f"WARNING: append-learning-log.sh failed for {entry_dict.get('id')}: {result.stdout} {result.stderr}")
    return result.returncode == 0


def new_id():
    return "KL-" + datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")


# --- The actual execution dispatch. Reuses marketing-lead's existing,
# already-proven execution protocol via a fresh headless session - this
# script never constructs or sends a Meta API call itself. See the module
# docstring / docs/proactive-operations.md SS9 for why.

def dispatch_execution(plan_id):
    if plan_id.startswith("TEST-"):
        log(f"TEST MODE: simulating execution for {plan_id}, no real dispatch")
        time.sleep(2)
        return True, "TEST MODE: simulated success, no live Meta call made, no real claude session dispatched."

    if not real_execution_enabled():
        log(f"real execution gated off (TELEGRAM_APPROVAL_REAL_EXECUTION not set true) - not dispatching for {plan_id}")
        return False, ("GATED: real execution via Telegram approval is currently disabled pending Suraj's decision on "
                        "the guardrail-1 open question (docs/architecture.md SS3). The tap was recorded as approved, "
                        "but no claude session was dispatched and no Meta/Instagram call was made. Approve this plan "
                        "the normal way in an interactive VS Code session, or enable TELEGRAM_APPROVAL_REAL_EXECUTION "
                        "in telegram_config.txt once that question is resolved.")

    prompt = f"""You are marketing-lead, executing a plan that was approved via an authenticated Telegram button tap (not a relayed claim - the tap came from the verified, authorized chat, and this dispatch only happens after that verification already passed).

Plan id: {plan_id}

Before executing:
1. Read this exact entry from knowledge/learning-log.jsonl (grep for the id).
2. Confirm it is a type=decision entry that hasn't already been executed (grep for any type=change entry with linked_to containing this id - if one exists, STOP, do not execute again, report ALREADY_EXECUTED).
3. Follow the plan's own stated execution steps exactly, including any fresh pre-check it specifies (e.g. re-GET the object immediately before writing, to confirm current live state still matches what the plan assumed).
4. If the fresh pre-check shows the plan's assumptions no longer hold (object state changed, already in the target state, a conflicting change happened since), STOP and do not execute - report STALE_NOT_EXECUTED with the specific reason.

If everything checks out:
5. Execute verbatim, exactly as the plan specifies - no redesign, no reinterpretation, no improvement.
6. Verify via a fresh GET per the plan's own verification steps.
7. Log a type=change entry via scripts/append-learning-log.sh, actor=marketing-lead, linked_to this plan id, matching the pattern used for every other execution in this system's history.
8. Report EXECUTED with a plain before/after summary.

Never call any Meta/Instagram write endpoint other than the exact one this specific plan specifies.

Report format - this matters, your response's first line is shown directly to the user on their phone via Telegram: the FIRST LINE of your entire response must be exactly one of EXECUTED / STALE_NOT_EXECUTED / ALREADY_EXECUTED / FAILED, immediately followed on the same line by a colon and one short plain-English sentence naming what was actually done or why not (no Meta object IDs, no jargon) - e.g. "EXECUTED: Added the Sage Green Lehenga ad to the India Insta Engaged ad set, built paused." Print nothing before that line - no "log entry committed" notices, no preamble. Elaborate with technical detail (before/after, verification) below it if useful, but that first line is what the user actually reads."""

    env = os.environ.copy()
    nvm_dir = os.path.expanduser("~/.nvm")
    oauth_file = os.path.join(REPO_DIR, "oauth_token_do_not_commit.txt.txt")
    if not os.path.exists(oauth_file):
        return False, "oauth_token_do_not_commit.txt.txt not found - cannot authenticate the execution dispatch"
    with open(oauth_file) as f:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = f.read().strip()

    # nvm's node/claude aren't on PATH in this process's minimal cron-derived
    # environment either - resolve the actual node bin dir the same way the
    # shell wrapper scripts do, rather than assuming.
    bash_cmd = (
        f'export NVM_DIR="{nvm_dir}"; '
        f'[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        f'cd "{REPO_DIR}" && claude -p "$1" --permission-mode dontAsk '
        f'--allowedTools "Read,Grep,Glob,Bash,mcp__stitchflow__*"'
    )
    try:
        result = subprocess.run(
            ["bash", "-c", bash_cmd, "--", prompt],
            cwd=REPO_DIR, env=env, capture_output=True, text=True, timeout=EXEC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"execution dispatch timed out after {EXEC_TIMEOUT_S}s"

    output = (result.stdout or "") + (result.stderr or "")
    log(f"execution dispatch for {plan_id} exited {result.returncode}. Output (first 2000 chars): {output[:2000]}")
    if result.returncode != 0:
        return False, f"claude exited {result.returncode}: {output[-500:]}"
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    return True, output[:3000]  # cap what we quote back to Telegram/the log


# --- Callback handling

def handle_callback_query(token, cq):
    cq_id = cq["id"]
    data = cq.get("data", "")
    from_id = cq["from"]["id"]
    message = cq.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    if ":" not in data or data.split(":", 1)[0] not in CALLBACK_PREFIXES:
        # Not a recognized approval callback - ack quietly, do nothing else.
        # This is the boundary that satisfies "no arbitrary message/command
        # can trigger a Meta action" - only these three exact, prefixed,
        # plan-id-bound callback_data values are ever interpreted at all.
        tg_api(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "Unrecognized action."})
        return

    prefix, plan_id = data.split(":", 1)
    action = CALLBACK_PREFIXES[prefix]

    # Authorization: only the chat_id this system's telegram_config.txt is
    # configured for may ever act on anything. Checked against the actual
    # message's chat (belt), and again inside claim_for_action against the
    # chat_id recorded when the request was originally sent (suspenders).
    _, configured_chat_id = load_telegram_config()
    if str(from_id) != str(configured_chat_id) or str(chat_id) != str(configured_chat_id):
        tg_api(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "Not authorized.", "show_alert": True})
        log(f"UNAUTHORIZED callback attempt on {plan_id} from user {from_id} / chat {chat_id}")
        return

    outcome = claim_for_action(plan_id, action, chat_id)

    if outcome == "unknown":
        tg_api(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "Unknown plan - not sent by this system."})
        return
    if outcome == "unauthorized":
        tg_api(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "Not authorized.", "show_alert": True})
        return
    if outcome.startswith("already:"):
        prior = outcome.split(":", 1)[1]
        tg_api(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Already {prior} - no action taken.", "show_alert": True})
        return
    if outcome == "stale":
        tg_api(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "This request expired - please re-validate before approving."})
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": f"⌛ Expired\n\nThis was pending too long ({STALE_AFTER_HOURS}h+) so nothing was done.\n\nPlease re-check it's still valid before approving manually.\n\nPlan ID: {plan_id}",
        })
        return

    # outcome == "claimed" - genuinely our first, valid, authorized touch on
    # this plan. Non-fatal: claim_for_action already committed the state
    # transition above, so a failure acking the tap (e.g. a stale
    # callback_query_id) must not prevent the actual reject/hold/approve
    # logic below from running - that was the exact 2026-08-24 failure mode
    # for the approve path, and reject/hold deserve the same protection.
    try:
        tg_api(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "Got it, processing..."})
    except Exception as e:
        log(f"non-fatal: failed to ack callback for {plan_id}: {e}")

    if action == "reject":
        append_learning_log({
            "id": new_id(), "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "actor": "human", "type": "override", "subject": plan_id,
            "summary": f"Declined via Telegram button. Plan {plan_id} rejected, no Meta/Instagram changes made.",
            "reasoning": "User tapped REJECT on the Telegram approval request.",
            "source": "human_told", "confidence": "high",
            "tags": ["telegram-approval", "rejected"], "linked_to": [plan_id],
        })
        finalize_status(plan_id, "rejected")
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": f"❌ Rejected\n\nNo changes were made.\n\nPlan ID: {plan_id}",
        })
        return

    if action == "hold":
        # Carries a `follow_up` (added 2026-08-24, user request) so the
        # weekly review's due-now sweep picks this back up automatically -
        # re-asks every week, with fresh buttons, until the user actually
        # approves or rejects it, rather than a hold silently sitting
        # forever with a dead button. Each re-hold creates its own new
        # observation entry with a fresh 7-day follow_up, so the cycle
        # naturally repeats without needing separate "still held" tracking -
        # the due-now sweep's own two-step check (is there a LATER
        # override/change entry for this plan_id?) is what recognizes an
        # approve/reject as the thing that finally stops the cycle.
        follow_up_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
        append_learning_log({
            "id": new_id(), "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "actor": "human", "type": "observation", "subject": plan_id,
            "summary": f"Held via Telegram button for later review. Plan {plan_id} preserved, unresolved, no Meta/Instagram changes made.",
            "source": "human_told", "confidence": "high",
            "tags": ["telegram-approval", "held"], "linked_to": [plan_id],
            "follow_up": f"re-check {follow_up_date}: if plan {plan_id} is still not approved/rejected (no later type:override or type:change linked to it), resend via scripts/send-telegram-approval.sh at that week's review and list it under the digest's Hold items section",
        })
        finalize_status(plan_id, "held")
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": f"🕒 On hold\n\nSaved for later review. No changes were made.\n\nYou'll be asked again in next week's report.\n\nPlan ID: {plan_id}",
        })
        return

    # action == "approve" - enqueue for the background worker FIRST, before
    # any further Telegram API calls, and unconditionally. claim_for_action
    # already committed "claimed_approve" above; if the ack/edit calls below
    # were allowed to run first and one of them threw (the exact stale-
    # callback scenario that caused the 2026-08-24 incident), the plan would
    # never reach the queue at all and would be stuck exactly like before -
    # enqueueing first guarantees the dispatch always happens once claimed,
    # regardless of what the UI-feedback calls below do.
    DISPATCH_QUEUE.put((token, plan_id, chat_id, message_id))
    try:
        tg_api(token, "editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": f"⏳ Approved — queued, working on it...\n\nPlan ID: {plan_id}",
        })
    except Exception as e:
        log(f"non-fatal: failed to edit 'queued' message for {plan_id}: {e}")


def process_approve_dispatch(token, plan_id, chat_id, message_id):
    """Runs on the background worker thread. Wrapped end-to-end in try/except
    so that ANY failure - including a Telegram API error, not just a
    dispatch_execution() problem - still reaches finalize_status() and
    notifies the user, rather than leaving state stuck at "claimed_approve"
    forever (the exact 2026-08-24 failure mode this function replaces)."""
    try:
        success, detail = dispatch_execution(plan_id)
        if not success and detail.startswith("GATED:"):
            final_status = "gated"
        else:
            # Check the compound keywords FIRST: "ALREADY_EXECUTED" and
            # "STALE_NOT_EXECUTED" both contain "EXECUTED" as a raw substring,
            # so a naive `"EXECUTED" in detail` check (even restricted to the
            # first line) mislabels a correctly-declined stale/already-done
            # plan as a genuine execution - confirmed live 2026-08-21 during
            # real-dispatch testing: the model's ALREADY_EXECUTED response
            # didn't even lead with the bare keyword on line 1 as instructed,
            # it explained first - so this also can't assume strict first-line
            # compliance and checks the whole output. False-labeling something
            # "Executed" when it wasn't is the dangerous direction to get
            # wrong; false-labeling a real execution as "not_executed" is only
            # cosmetic (the learning-log type:change entry is the real record
            # either way), so ambiguous/non-compliant output resolves to the
            # safe (non-executed) label, never the reverse.
            detail_upper = detail.upper()
            if "ALREADY_EXECUTED" in detail_upper or "STALE_NOT_EXECUTED" in detail_upper:
                final_status = "not_executed"
            elif success and "EXECUTED" in detail_upper:
                final_status = "executed"
            elif not success:
                final_status = "failed"
            else:
                final_status = "not_executed"
        finalize_status(plan_id, final_status, detail[:500])

        if final_status == "executed":
            icon, label = "✅", "Executed"
        elif final_status == "gated":
            icon, label = "🔒", "Approved, but live execution is currently disabled (see detail)"
        elif final_status == "not_executed":
            icon, label = "⚠️", "Approved but NOT executed (stale/already-done - see detail)"
        else:
            icon, label = "❗", "Execution FAILED"

        # Plain text, no parse_mode: first_line comes from a real claude
        # session's own output (ad IDs, field names like adset_id) or the
        # GATED message, either of which can contain _ / * / ` that break
        # Telegram's Markdown parser outright - same class of bug fixed in
        # send-telegram-approval.sh after a live 400 during testing (2026-08-21).
        first_line = detail.strip().splitlines()[0] if detail.strip() else "(no output)"
        # The dispatch prompt asks for "KEYWORD: plain sentence" as line 1 -
        # strip the keyword prefix for display since the icon/label above
        # already says whether it executed (added 2026-08-23, user feedback:
        # keep messages short and plain, don't repeat the same status twice).
        display_line = first_line
        for kw in ("EXECUTED:", "STALE_NOT_EXECUTED:", "ALREADY_EXECUTED:", "FAILED:", "GATED:"):
            if display_line.upper().startswith(kw):
                display_line = display_line[len(kw):].strip()
                break
        tg_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": f"{icon} {label}\n\n{display_line}\n\nPlan ID: {plan_id}",
        })
    except Exception:
        err = traceback.format_exc()
        log(f"ERROR in process_approve_dispatch for {plan_id}:\n{err}")
        # finalize_status must fire regardless of what failed above (a
        # dispatch_execution bug, a Telegram API error mid-flow, anything) -
        # this is the specific guarantee that prevents the plan from being
        # stuck at "claimed_approve" forever with no way for a later tap to
        # even see a clear "already ..." message explaining what happened.
        finalize_status(plan_id, "failed", f"Unhandled error in dispatch worker: {err[-400:]}")
        try:
            tg_api(token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"❗ Something went wrong processing this\n\nNo confirmation of what happened - please check with the operator before assuming anything did or didn't execute.\n\nPlan ID: {plan_id}",
            })
        except Exception:
            pass  # best-effort notification; state is already finalized above regardless


def dispatch_worker_loop():
    """Runs forever on a background thread, processing approved plans one at
    a time (bounded concurrency - see DISPATCH_QUEUE comment) so the main
    poll loop is never blocked waiting for a dispatch to finish."""
    while True:
        token, plan_id, chat_id, message_id = DISPATCH_QUEUE.get()
        try:
            process_approve_dispatch(token, plan_id, chat_id, message_id)
        finally:
            DISPATCH_QUEUE.task_done()


# --- Main poll loop, with single-instance locking so @reboot + the
# watchdog can never end up running two listeners concurrently.

def acquire_singleton_lock():
    os.makedirs(STATE_DIR, exist_ok=True)
    lockf = open(LISTENER_LOCK_FILE, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another listener instance already holds the lock - exiting")
        sys.exit(0)
    lockf.write(str(os.getpid()))
    lockf.flush()
    return lockf  # keep a reference alive for the process lifetime


def main_loop():
    _lock_handle = acquire_singleton_lock()  # noqa: F841 - held for process lifetime
    token, _ = load_telegram_config()
    threading.Thread(target=dispatch_worker_loop, daemon=True, name="dispatch-worker").start()
    log(f"listener started, pid={os.getpid()}")
    offset = None
    while True:
        try:
            params = {"timeout": POLL_TIMEOUT_S}
            if offset is not None:
                params["offset"] = offset
            resp = tg_api(token, "getUpdates", params, timeout=POLL_TIMEOUT_S + 10)
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                cq = update.get("callback_query")
                if cq:
                    try:
                        handle_callback_query(token, cq)
                    except Exception as e:
                        log(f"ERROR handling callback: {e}")
        except (urllib.error.URLError, TimeoutError) as e:
            log(f"poll error (will retry): {e}")
            time.sleep(5)
        except Exception as e:
            log(f"unexpected error in poll loop (will retry): {e}")
            time.sleep(5)


if __name__ == "__main__":
    if "--record-sent" in sys.argv:
        def arg(name):
            i = sys.argv.index(name)
            return sys.argv[i + 1]
        record_sent(arg("--plan-id"), arg("--chat-id"), arg("--message-id"))
    else:
        main_loop()
