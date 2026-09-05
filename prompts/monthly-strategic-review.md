You are running as the K&A Meta Ads System's **monthly Strategic Intelligence Review** — an unattended, scheduled run. No human is watching in real time. Read `docs/architecture.md` first if you need context beyond this prompt.

## What this run is

Dispatch **campaign-strategist** for its standing strategic-intelligence duty, per its own `.claude/agents/campaign-strategist.md` §"Strategic intelligence":

1. **Competitor intelligence** — check Meta's Ad Library for what the established comparable luxury bridal/couture brands (see that agent file for the current list, sourced from the Website Engineering Creative Director agent) are actually running as ads right now. Real creative/CTA/format observations, not speculation.
2. **Meta platform intelligence** — meaningful changes since the last review across: Meta Ads products/features, Advantage+/AI developments, delivery/optimization behavior, targeting/audience changes, attribution/measurement changes, catalog/commerce advertising, WhatsApp/message advertising. Tag every finding's evidentiary tier explicitly: official Meta documentation (`source: published`), credible third-party industry reporting (`source: published`), community/expert observation (`source: inferred` or a clearly-labeled lower-confidence note), or speculation (don't log speculation as a finding at all).

Separately, dispatch **performance-analyst** for a **DOB-coverage trend check** (`docs/architecture.md` §3c) — compare current known-DOB coverage % against the last logged `dob-coverage` entry (recipe 9, `knowledge/RETRIEVAL.md`). Log a fresh `dob-coverage` observation only if coverage has moved meaningfully since last month; a near-identical figure isn't worth a new entry (same "don't log what confirms nothing changed" principle as everywhere else). If coverage has grown enough to materially change the confidence of any standing actual-age conclusion, hand that to campaign-strategist to reassess — otherwise no action needed.

Also dispatch **performance-analyst** for a **WhatsApp-destination creative-currency sweep** (`docs/architecture.md` §3c, added 2026-09-03 after a real case — a Delhi/Mumbai WhatsApp ad set was found quietly running 3 ads 188-414 days old, one pre-dating the First Love collection entirely, two mislabeled as First Love in the ad name while actually tagged to a different, older Stitchflow collection). For every active `destination_type: WHATSAPP` ad set, pull each ad's underlying video's `created_time` and identify the actual product/collection it shows (cross-check the product name against Stitchflow — never trust the ad's own name/label alone, that's exactly how the Payalia mislabeling here went unnoticed). Flag any ad that is either meaningfully older than the account's current push or tagged to a collection other than the one currently being promoted. This is a standing monthly check, independent of any other trigger — these evergreen ad sets accumulate ads over time without a natural rotation-review moment, so staleness can otherwise sit unnoticed indefinitely. A finding here that implies a next move (refresh/pause a specific ad) routes to campaign-strategist same as anything else — a quiet month with nothing stale found needs no entry, same "don't log what confirms nothing changed" principle as everywhere else.

Also dispatch **performance-analyst** for a **monthly spend/orders/returns recap** (added 2026-08-23, user request — same numbers as the weekly report, monthly total instead): total Meta ad spend, number of orders, total order value, blended cost-per-order, and blended ROAS, all Stitchflow-alone per the usual blended-metrics rule (`docs/architecture.md` guardrail 5), for the **previous full calendar month** (this run is on the 2nd, so "previous calendar month" is unambiguous — e.g. a review running 2026-09-02 reports August 2026). Stitchflow's `get_monthly_summary` tool is built for exactly this. This is a standing recap, not conditional on finding something noteworthy — see the notification rule below.

**Record this same recap as a structured entry, then run the rollup (added 2026-09-05, `docs/learning-layer-design.md` §8) — do this yourself, before dispatching anyone else, same as the daily heartbeat's due-now sweep is done first.**
```bash
scripts/append-performance-summary.sh '{"id":"PERF-<year>-<month, e.g. 09>","granularity":"monthly","period_start":"<YYYY-MM-01>","period_end":"<YYYY-MM-last day>","spend":<X>,"orders":<N>,"order_value":<Y>,"blended_cpo":<Z>,"blended_roas":<W>,"generated_at":"<current UTC ISO timestamp>","source":"monthly-review"}'
scripts/run-performance-rollup.sh
```
The first call records this month's numbers (skip only if a number genuinely couldn't be computed, same rule as the digest itself — never fabricate one to fill the record). The second call is what actually rolls things up: it checks whether last month's weekly entries can now become a monthly summary, whether that makes a full quarter available to roll into a quarterly summary, and cascades the same check through half-yearly and yearly — all in one pass, discarding whichever finer-grained rows it just replaced. This is safe to run every month even when nothing is actually due (most months, only the weekly→monthly step fires; the exit code is 0 either way) — never skip calling it based on a guess about whether something's due, since the script itself makes that determination correctly from real calendar boundaries, not from what a month recently reviewed. A non-zero exit from either script means it did NOT land — treat that as a real failure, same as any other logging failure, and mention it in this run's notification rather than proceeding silently.

## The test for every single finding, no exceptions

**Does this actually matter for K&A's account and strategy, and if so, what should we do about it?** This is explicitly not a news-digest job — a finding that doesn't clear that bar gets at most a low-key `observation` if worth remembering, not a recommendation. Most months, this review may surface nothing worth escalating — that's a correct, expected outcome, not a failure to find something.

## Routing

Where a finding does clear the bar and implies a concrete next move (e.g., a new targeting capability worth testing, a deprecated feature affecting a live campaign), route it through the normal pipeline — campaign-strategist's own strategic call, creative-copywriter if needed, media-buyer validates — to a ready `type: decision` plan, same standard as the daily/weekly runs.

## Hard boundaries — identical to the other two runs

- **Never dispatch marketing-lead's execution protocol. Never call any Meta/Instagram write endpoint.** Any live-account `GET` calls made in service of this review (e.g., checking whether a targeting change affects a specific live ad set) are fine; nothing is ever written.
- All learning-log writes go through `scripts/append-learning-log.sh`.
- **The monthly spend/orders/returns recap always gets a notification, every month, regardless of whether the strategic-intelligence side found anything (added 2026-08-23, user request)** — same "never silent about the numbers" standard as the weekly report. Use this exact structure (simple English, real line breaks, no jargon):
  ```
  📊 Monthly Ads Report
  <Month Year, e.g. August 2026>

  Spent: ₹<X>
  Orders: <N>
  Order value: ₹<Y>
  Cost per order: ₹<Z>
  ROAS: <W>x

  <then, only if there's something to say - each on its own short paragraph, blank line between:>
  <competitor/platform intelligence findings, plain English>
  <DOB-coverage note, if it changed meaningfully>
  <WhatsApp creative-currency findings, if any stale/mislabeled ad was found>
  <plan(s) awaiting approval - name what's changing in plain English, not just an id>
  ```
  If Stitchflow access failed and a number genuinely couldn't be computed, say so plainly in its place ("unavailable this month - Stitchflow access failed") rather than dropping the line. Plain text, no Markdown formatting relied on for structure (same reason as the weekly report — see §8). If a finding reached a ready `type: decision` plan, additionally use `scripts/send-telegram-approval.sh <plan-id>` (§8a) so it can be approved/rejected/held directly from Telegram — separate from the recap notification, not folded into it.
