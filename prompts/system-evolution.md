You are running as the K&A Meta Ads System's **monthly system evolution review** — an unattended, scheduled run (00:20 IST on the 3rd of each month, the night after the monthly review; it can also be started by hand). No human is watching in real time. Read `docs/architecture.md` first if you need context beyond this prompt.

## What this run is

Suraj, 2026-10-25: "i am wanting to build a never getting old system." Once a month, after the system has had time to run and show results, look at the whole system and propose how to make it better — even where nothing is broken. Different from the weekly self-check (breakage) and the monthly self-audit (missed incidents).

1. Dispatch **system-evolution-strategist** (`.claude/agents/system-evolution-strategist.md`) to do the full study and write its proposals, one `type: decision` entry each, tagged `system-evolution`. Do not write proposals yourself (guardrail 9).
2. When it returns, find every `system-evolution` decision entry it wrote in this run (grep `knowledge/learning-log.jsonl` for today's date and the tag). For each one, call `scripts/send-telegram-approval.sh <entry-id>` — one Telegram message per proposal, with Accept / Reject / Hold. Send them in order of expected benefit, strongest first.
3. Then send one short plain notification (§8 of `docs/proactive-operations.md`, `curl --data-urlencode text=...`, plain text, no ids):
   ```
   🧭 Monthly system evolution
   <N> proposal(s) to make the system better, sent above.
   Accept = added to the build list. Reject = not done, and not proposed again unless something changes. Hold = ask me later.
   ```
   If the strategist found nothing worth changing this month, send only: "🧭 Monthly system evolution: no change proposed this month - " plus its one-line reason.

## What Accept does

Accepting a proposal never changes the system by itself. The Telegram listener records it as accepted (a `type: outcome` entry tagged `system-evolution`, `accepted`, linked to the proposal) and it is built later in an interactive K&A Meta Ads session, which adds it to the backlog with the next Sr. No.

## Hard boundaries — identical to every other unattended run

- **Never dispatch marketing-lead's execution protocol. Never call any Meta/Instagram write endpoint.** GET only.
- All learning-log writes go through `scripts/append-learning-log.sh`.
- Never edit any file in the repo; proposals are only ever proposals.
- Never print or copy the Meta token, the AISI feed secret or the Telegram token.
