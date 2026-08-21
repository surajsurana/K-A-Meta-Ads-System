# K&A Meta Ads System — Proactive Operations (Headless/Droplet Runtime)

Companion document to `docs/architecture.md` §3a/§3b — the full design for how the daily heartbeat, weekly review, and monthly strategic intelligence review actually run unattended. `architecture.md` stays the summary; this is the detailed reference for one subsystem, same relationship it has with `docs/learning-layer-design.md`.

**Status as of 2026-08-19: the prompts, wrapper scripts, and this repo itself exist and are built. Not yet deployed** — the droplet has not been inspected yet, GitHub push has not yet succeeded (see §2a), and no cron job has been registered. This document describes the target state and the concrete steps to reach it from here.

---

## 1. Why this shape, not the alternatives

Three mechanisms were evaluated before landing here (see conversation history for the full evaluation):
- **Cloud "routines"** (`CronCreate`/the `schedule` skill) — ruled out. They run against a fresh git clone in an isolated cloud sandbox with no access to local plaintext secrets and no clean way to enforce "stop before a live write, wait for real approval."
- **Claude Code Desktop's own scheduled tasks** — ruled out. Requires Desktop open continuously, which the user explicitly does not want to depend on.
- **Headless Claude Code CLI (`claude -p`), triggered by cron, on the existing DigitalOcean droplet** — the answer. Same engine as interactive VS Code sessions, real local file/subagent/secret access, already-proven infrastructure (StockTradingBot's cron, Petty Cash's systemd service both run on this droplet today).

GitHub Actions was considered and deliberately not used as the runtime — it would add a second secrets store and CI maintenance surface for a single-operator system whose own design philosophy explicitly favors "boring, low-maintenance, one person can run it part-time." Revisit only if this ever stops being single-operator/single-machine.

---

## 2. Architecture

```
VS Code (local, interactive)  ──git push──▶  GitHub (private repo — source of truth:
                                                docs/, .claude/agents/, knowledge/,
                                                scripts/, — NOT secrets, NOT logs)
                                                      │
                                          git fetch && git reset --hard origin/main
                                          (first step of every scheduled run — hard
                                           sync, guarantees no drift, ever)
                                                      ▼
                                    Droplet (existing: StockTradingBot + Petty Cash)
                                    K&A kept isolated: own directory, own cron
                                    entries, own log files, own process — inspected
                                    for capacity before anything is deployed (§5)
                                                      │
                                       cron → wrapper script → headless `claude -p`
                                       → scripts/append-learning-log.sh for any
                                         learning-log writes → Telegram notification
```

---

## 3. Secrets

Secrets never travel through GitHub, in either direction. Two files hold them today:
- `meta_token.txt.txt` — Meta Graph API System User token, plaintext.
- `.mcp.json` — currently has the Stitchflow bearer token inline.

**Plan:**
- `.gitignore` excludes both, added *before* `git init` ever runs — ordering matters, a secret that touches even one commit is in history even after a later deletion.
- A committed `.mcp.json.example` (placeholder token) documents the required shape without exposing the real value.
- The real `.mcp.json` and `meta_token.txt.txt` are placed on the droplet directly (scp/rsync), once, outside git entirely — matching whichever secret-handling convention the droplet already uses for StockTradingBot/Petty Cash, to be confirmed during droplet inspection (§5) rather than assumed.
- **A canonical offline copy of both secrets is kept somewhere outside both git and the droplet** (password manager or equivalent) — otherwise losing the droplet means losing the only copy of a live Meta System User token, not just a convenience.
- Run logs (see §7) are also gitignored — operational/debug artifacts, not architecture, same category as `Content/*.mp4`.

---

## 4. The learning-log write protocol

Full rationale in `docs/learning-layer-design.md` and `docs/architecture.md` §2 — summarized here because it's load-bearing for this design specifically: with two writer environments (interactive VS Code, unattended droplet cron), a naive append-and-push race could silently lose an entry. Every agent writes via `scripts/append-learning-log.sh`, which does fetch → rebase onto latest `origin` → append → commit → push, retrying on a rejected push (never force-pushing), and failing loudly — never guessing — if a genuine conflict occurs. A non-zero exit means the entry did not reach GitHub; that is a hard failure to surface, not something to log-and-continue past.

**Why `git reset --hard origin/main` before a run is still safe given this:** the reset only ever happens at the *start* of a run, before that run has made any local changes — there is nothing to lose at that point, provided the *previous* run always ended with zero uncommitted/unpushed state (i.e., every write during a run either pushed successfully or the run stopped and alerted rather than silently moving on with an unpushed commit sitting local). That invariant is what `append-learning-log.sh`'s hard-failure behavior protects.

---

## 5. Droplet capacity/setup check (required before deployment, not assumed)

Before anything is installed or scheduled:
- Confirm CPU, RAM, disk headroom — a headless Claude Code CLI run is a lightweight process (API calls, no heavy local compute), but this needs verifying against the droplet's actual current load from StockTradingBot + Petty Cash, not assumed to be fine.
- Confirm whether Claude Code CLI is already installed/authenticated on the droplet. If not: **resolve the auth method first** — a headless process needs non-interactive auth (an `ANTHROPIC_API_KEY` env var is the standard path), which may mean setting up separate, metered, pay-per-token billing distinct from any interactive subscription already in use. This has a real cost dimension and is a decision point, not an assumption to make silently.
- K&A gets its own directory, its own cron entries, and its own log files — isolated from StockTradingBot/Petty Cash's processes and directories, not interleaved with them.
- Schedule K&A's cron jobs outside StockTradingBot's active market-hours windows (9:20am–3:30pm IST) as a courtesy on shared infrastructure — not a hard technical requirement, but no reason not to be a good neighbor.
- **This inspection and all droplet-side deployment work happens from a session dedicated to that droplet, not unilaterally from the K&A Marketing project session** — the droplet is a shared resource also running live trading and financial-bot infrastructure.

---

## 6. Scheduling

Plain cron (matching StockTradingBot's existing pattern, not a third scheduling mechanism). The prompts and wrapper scripts referenced below are real, already-written files in this repo, not descriptions of future work:

- **Daily heartbeat** — `scripts/run-heartbeat.sh`, running `prompts/daily-heartbeat.md`. One cron entry, outside market hours (9:20am–3:30pm IST, StockTradingBot's active window — courtesy on shared infrastructure, not a technical requirement).
- **Weekly full review** — `scripts/run-weekly-review.sh`, running `prompts/weekly-review.md`. One cron entry, weekly.
- **Monthly Strategic Intelligence Review** — `scripts/run-monthly-review.sh`, running `prompts/monthly-strategic-review.md`. One cron entry, monthly. (An early-escalation path for a genuinely significant platform change is handled by campaign-strategist's own judgment when dispatched during a daily/weekly run — it does not need its own trigger.)
- **Telegram approval listener** (added 2026-08-21, see §8a) — not a periodic job but a long-lived process: `@reboot python3 scripts/telegram_approval_listener.py` plus `* * * * * scripts/watchdog-telegram-listener.sh` (restarts it if `pgrep` finds it not running). Both entries added to kmetaads' own crontab alongside the three above, not a systemd unit — no root needed. The listener's own file lock (not the watchdog) is what prevents two instances running at once.

All three source a shared `scripts/_run-common.sh` rather than duplicating the same logic three times independently (a fix worth naming: triplicated inline logic is exactly the kind of thing that drifts when one copy gets patched and the others don't). Each run:

- **Acquires a shared `flock`-based lock before touching the working directory**, so two scheduled runs — or an overrunning one and its on-time successor — can never race against each other on the same checkout. Waits up to 5 minutes for the lock, then fails loud (see below) rather than silently skipping or corrupting the sync.
- **Hard-syncs** (`git fetch && git reset --hard origin/main`) before anything else. Safe specifically because a prior run only ever ends with zero uncommitted/unpushed local state (`append-learning-log.sh` fails loudly rather than leaving an unpushed commit behind) — there's nothing for the reset to destroy.
- Runs headlessly, **fresh session every time** — no `--continue`. Sidesteps the known "subagent `.md` edits don't hot-reload mid-session" gotcha entirely (a fresh session always loads current agent files) and keeps context/cost bounded instead of chaining months of sessions together. Cross-run memory is the learning log's job, not conversation continuity.
- `--permission-mode dontAsk --allowedTools "Read,Grep,Glob,Bash"` — broad Bash access, corrected after a real first test (2026-08-19) showed narrow `Bash(curl ...)` allowlist patterns blocked *all* network calls, not just writes. A shell-pattern allowlist can't reliably tell a GET from a POST anyway, so it was never a genuine write-blocker — the real safety control is the prompt itself, and that first test proved it holds even when the agent is completely blocked and confused (it explained the failure plainly rather than fabricating a result or attempting a write).
- **Logs to a dated file and captures the CLI's own exit code.** A non-zero exit — or a failed sync — triggers a direct Telegram alert from the wrapper script *itself*, independent of whether the Claude session ran at all. This closes a real gap the original design had: previously, the only notification path ran *inside* the agent's own session, meaning an infrastructure failure (bad sync, CLI crash, auth expiry) would have been completely silent. Log rotation keeps 30 days.
- The prompt itself remains the primary *behavioral* safety layer, same honesty this system already has about most guardrails being prompt-based: it explicitly names which agents to dispatch, explicitly never invokes marketing-lead's execution protocol, and explicitly states no Meta/Instagram write call happens under any circumstance. Tool-permission scoping is defense-in-depth, not the sole control.

---

## 7. Logs

**Kept in a sibling directory outside the git working tree entirely** (default `~/ka-meta-ads-logs/`, overridable via the `KA_LOG_DIR` env var — see `scripts/_run-common.sh`), not a gitignored folder inside the repo. This is a deliberate refinement over the original design: logs aren't project state, they're operational exhaust, and keeping them structurally outside the repo means they're immune to any future `git clean`/`reset` regardless of gitignore correctness, and there's no pattern to maintain. Basic 30-day rotation, built into the wrapper scripts. Fetched via SSH when actually debugging a run — acceptable to lose if the droplet is ever lost (see §9).

---

## 8. Notification

**Primary: Telegram.** `telegram_config.txt` (gitignored, local to the repo working directory — same pattern as `meta_token.txt.txt`; `telegram_config.txt.example` documents the required shape) holds real `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` values as of 2026-08-21. Three independent paths, all real and built:

1. **In-session, plain FYI** — the agent itself sends a notification via `curl` to the Telegram Bot API for things that don't need a decision (status, anomalies, quiet-day silence per below). **Use `curl --data-urlencode text="..."`, never `-d text="..."`** — confirmed live 2026-08-20 that plain `-d` treats a literal `&` in the message as a field separator (standard form-encoding behavior), which silently truncated a test message at the very first `&` in "K&A Meta Ads..." itself. This isn't cosmetic — real notification text (₹ amounts, campaign names, `&`/`=` characters) would be silently mangled the same way.
2. **In-session, interactive approval** (added 2026-08-21) — when an agent reaches a validated `type:decision` plan that's genuinely ready for the user's approval, it calls `scripts/send-telegram-approval.sh <plan-id>` instead of a plain notification. See §8a below.
3. **Wrapper-script-level failure alert** — sent directly by `scripts/_run-common.sh`, independent of whether the Claude session ran at all. If the sync fails or the headless CLI process itself exits non-zero, this path still fires, because it doesn't depend on the agent having gotten far enough to notify itself.

Reusing the bot infrastructure already proven on this droplet for Petty Cash (a new dedicated bot, or the same bot with a different chat, either works) is the recommended source for `TELEGRAM_BOT_TOKEN` — not the `PushNotification` tool available in hosted interactive sessions, which almost certainly has no path from a bare headless Linux CLI process.

A quiet day (nothing due, nothing anomalous) produces no notification and no log entry from the plain-FYI path — consistent with the learning log's existing "don't log what confirms nothing changed" principle, now extended to notifications. The failure-alert path is separate and unconditional — it fires on genuine infrastructure failure regardless of what the agent found or didn't find.

---

## 8a. Interactive Telegram approval (inline APPROVE / REJECT / HOLD buttons)

Lets the user resolve a pending plan by tapping a button on their phone instead of opening VS Code. Two new files, both stdlib/plain-bash, no new dependencies:

- **`scripts/send-telegram-approval.sh <plan-id>`** — called by an agent once a plan is genuinely ready for approval. Validates the plan exists in `knowledge/learning-log.jsonl` and is `type:decision` (refuses otherwise — never sends an approval request for a plan that doesn't exist or isn't actually a decision). Sends a short caption (subject + first 500 chars of the summary, not the full prose plan — Telegram on a phone is not where you read a full plan) with an inline keyboard: `✅ APPROVE` / `❌ REJECT` / `🕒 HOLD`, `callback_data` = `A:<plan-id>` / `R:<plan-id>` / `H:<plan-id>`. Records the pending state via the listener's `--record-sent` mode.
- **`scripts/telegram_approval_listener.py`** — a single long-lived process, long-polling `getUpdates` for `callback_query` events only (never treats a free-text message as a command — this is what makes "no arbitrary message can trigger an action" hold structurally, not just by convention). State lives in `~/ka-meta-ads/telegram_approvals_state.json`, outside the repo (same reasoning as logs, §7), one JSON object keyed by plan id, every read-modify-write wrapped in an `fcntl.flock` so a rapid double-tap can only ever act once (the second callback sees `status != "pending"` and is told "already <status>", nothing more happens). Requests older than 48h (`STALE_AFTER_HOURS`) are marked `expired` on the next tap and never executed. Every callback is checked against the `chat_id` recorded when the request was sent *and* the incoming message's own chat — either mismatch is refused with "Not authorized," and logged.
  - **REJECT** → `type:override` learning-log entry, message edited to show rejected, terminal (a later tap reports "already rejected," never executes).
  - **HOLD** → lightweight `type:observation` entry, message edited to show held, also terminal per-instance (matches the requirement that an already-acted-on button can't be reused).
  - **APPROVE** → does **not** call Meta itself. Dispatches a fresh headless `claude -p` session running marketing-lead's existing execution protocol (the same one already proven for interactive VS Code approvals) against the exact plan id: it re-reads the plan, re-checks it hasn't already been executed, re-validates the plan's own freshness pre-check against live state, and only then executes verbatim, verifies, and logs `type:change`. If the fresh check shows the plan's assumptions no longer hold, it reports `STALE_NOT_EXECUTED` and makes no Meta call — so a plan sitting in "pending" for hours doesn't get blindly rubber-stamped even inside the 48h window. Final outcome is edited/sent back to Telegram either way.
- **`scripts/watchdog-telegram-listener.sh`** — run every minute from kmetaads' crontab plus once at `@reboot`; restarts the listener only if `pgrep` finds it not running. The listener's own single-instance file lock (`LISTENER_LOCK_FILE`) is the actual guarantee against two copies running concurrently, not the watchdog's `pgrep` check, which is just an optimization to avoid spawning a redundant one.

Test plans use a `TEST-` prefixed id; `dispatch_execution()` special-cases that prefix to simulate success without dispatching a real `claude -p` session at all — belt-and-suspenders alongside using synthetic learning-log entries that don't reference any real ad object, so even a bug in the prefix check can't reach a live Meta/Instagram write.

**Real (non-`TEST-`) execution is gated off by default**, independent of the above: `dispatch_execution()` checks `TELEGRAM_APPROVAL_REAL_EXECUTION` in `telegram_config.txt` (`telegram_config.txt.example` documents it) and, while it's unset/false, records the tap and reports back "approved but not executed — gated" without dispatching any `claude -p` session at all for a real plan. This exists because the Telegram-approval path raises an open architectural question — see `docs/architecture.md` §3 guardrail 1's 2026-08-21 note — that hasn't been resolved yet: whether an independently-authenticated Telegram tap (verified by Telegram's own callback signature plus this project's `chat_id` check, both in code, never asserted by an agent) counts as the user's direct approval "landing in the conversation that executes it," the way guardrail 1 requires, or is exactly the kind of relayed-consent claim that guardrail exists to block regardless of how it's verified upstream. The switch is a deliberate code-level gate, not a prompt instruction to the dispatched session — it doesn't depend on model behavior to hold.

---

## 9. Recovery if the droplet fails

Because GitHub holds all code/docs/agent-definitions and the learning log is pushed back immediately after every write (§4), droplet loss is recoverable, not catastrophic:
1. Provision a new droplet (or reuse another).
2. Clone the private GitHub repo.
3. Place the offline-stored copies of `meta_token.txt.txt` and the real `.mcp.json` (§3).
4. Install and authenticate the Claude Code CLI (resolve the API-key/billing question per §5 if not already settled).
5. Re-register the cron jobs from §6, including the `@reboot` and per-minute watchdog entries for the Telegram approval listener (§8a) — the listener has no other supervisor.
6. Fire each job manually once (§10) before trusting the schedule again.

Run logs (§7) are the one thing genuinely lost on droplet failure — accepted, low-stakes, matching this system's existing "acceptable manual-improvisation risk at current scale" posture toward things like rollback tooling.

---

## 10. Before enabling any schedule

Every job gets fired manually, once, from the droplet, before its cron entry is ever left to fire unattended for real:
- Confirm the sync step actually pulls the latest `origin/main`.
- Confirm subagent dispatch works identically to interactive mode (project-scoped `.claude/agents/*.md` discovery, MCP access to Stitchflow).
- Confirm a learning-log write via `scripts/append-learning-log.sh` actually lands on GitHub.
- Confirm the Telegram notification actually arrives.
- Confirm no secret appears anywhere in the captured run log.

Only after a clean manual run does the cron entry get left enabled.
