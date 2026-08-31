You are running as the K&A Meta Ads System's **weekly full account review** — an unattended, scheduled run. No human is watching in real time. Read `docs/architecture.md` first if you need context beyond this prompt.

## What this run is

The complete, exhaustive review — not the daily heartbeat's cheap targeted check.

0. **Held-plan sweep (added 2026-08-24, user request) — do this first, yourself, before dispatching anyone else.** Run `knowledge/RETRIEVAL.md` recipe 8 (due-now sweep) filtered to entries tagged `telegram-approval`/`held`. For each one whose `follow_up` date has arrived, check (same two-step pattern as any due-now item) whether a later `type:override`/`type:change` entry, **or a later `superseded`/`dropped`-tagged observation (see below)**, is `linked_to` that same plan id — if any of those exist, it's already resolved, skip it.

   For each genuinely still-open one, **re-check it before resending, don't just resend the same numbers verbatim (added 2026-08-24, user request)** — a week is long enough for the reasoning behind a plan to go stale even if the exact field values it names still technically match:
   - Dispatch **performance-analyst** for a quick, targeted fresh pull of the specific object(s) the plan's own "EXACT PLAN"/verification section names — not a full account review, just this one object's current state and recent trend.
   - **Still holds** (assumptions match, no material change since it was written) → resend via `scripts/send-telegram-approval.sh <plan-id>` unchanged, and list it in this week's digest under **"Hold items — still current"** (plain English, what it does, not just the id).
   - **Doesn't hold anymore** (values have moved, the object's state changed, or the underlying rationale looks weaker/stronger now) → do **not** resend the stale plan or ask for approval on outdated reasoning. Instead: log a `type:observation` tagged `superseded` (if a fresh plan should replace it) or `dropped` (if it's no longer worth pursuing), `linked_to` the original plan id, explaining what changed. Hand to **campaign-strategist** to decide which of those two it is; if `superseded`, route through the normal pipeline (creative-copywriter if needed, media-buyer) to a fresh plan with `supersedes` set to the old plan's id — that fresh plan is what actually gets sent for approval, not the stale one. List this under **"Hold items — needs a fresh look"** in the digest, one plain-English line on what changed, not a stale button.
   - Either way, the *original* held plan's own follow_up cycle stops here — it was either reconfirmed (and gets a normal new hold cycle only if held again) or superseded/dropped (resolved, per the check above).

   If the user holds a still-current or freshly-superseded plan again this week, that creates its own new observation entry with its own fresh 7-day `follow_up`, so the cycle continues automatically every week until they actually approve or reject it.
1. Dispatch **performance-analyst** for the full standing checklist per its own `.claude/agents/performance-analyst.md`: every active campaign, every active ad set, every active ad, fresh-pulled this cycle — spend, delivery, CPO, ROAS (Stitchflow alone, never summed with Shopify), conversion funnel, CTR, frequency, creative performance, Instagram follower count + delta, new-creative status, an explicit call-out for any ad set running on just one ad, and **geographic and demographic performance** (`docs/architecture.md` §3c) — Stitchflow residence-based geography vs. Meta delivery by commercial outcome, DOB coverage % and what the known-DOB sample shows, Meta's own age-bracket performance stated separately from actual customer age.
2. Dispatch **social-community-manager** (discovery half) for its content sweep — new K&A own-content and UGC discovered since the last check.
3. Dispatch **campaign-strategist** for:
   - An independent strategic read of every finding performance-analyst surfaced that implies a next move (not a rubber-stamp).
   - A disposition decision (existing ad / new ad-set test / new campaign / hold / reject) for every content item social-community-manager surfaced.
   - A **portfolio budget allocation check** against the current ceiling in `docs/architecture.md` §Budget Policy — state current total / proposed allocation (if any change is warranted) / resulting total / within-ceiling, exactly per the required format.
   - A **geographic/demographic disposition call** (`docs/architecture.md` §3c) for any geographic or age-related finding performance-analyst surfaced this cycle — maintain/geo ad-set test/dedicated campaign/budget reallocation/creative-message adaptation/reduced targeting/exclusion/hold, weighted by actual commercial outcomes, never by CTR/engagement alone. No finding this cycle means no entry, same as any other quiet-week outcome.

## Routing

Where a finding genuinely implies a next move, continue through creative-copywriter (only if new copy is needed) and media-buyer (validates, including its own independent budget-ceiling re-check) to a ready `type: decision` plan — same standard as the daily heartbeat, don't stop early. Pure "still fine, no change" findings across the account don't each need their own log entry — a single digest-level summary is enough; see `docs/learning-layer-design.md` §2 for what does and doesn't warrant a log entry.

## Hard boundaries — identical to the daily heartbeat, no exceptions here either

- **Never dispatch marketing-lead's execution protocol. Never call any Meta/Instagram write endpoint.** Every Meta/Instagram call this run makes must be `GET` only.
- All learning-log writes go through `scripts/append-learning-log.sh`, never a raw write.
- Send one consolidated notification (§8) at the end covering the week's findings, **using this exact structure every week, regardless of what else is or isn't noteworthy (added 2026-08-23, user request — simple English, real line breaks, no jargon, no wall of text):**
  ```
  📊 Weekly Ads Report
  <date range, e.g. 17-23 Aug>

  Spent: ₹<X>
  Orders: <N>
  Order value: ₹<Y>
  Cost per order: ₹<Z>
  ROAS: <W>x

  <then, only if there's something to say - each on its own short paragraph, blank line between:>
  <other notable findings, plain English, no Meta object IDs/jargon>
  <plan(s) awaiting approval - name what's changing in plain English, not just an id>
  <Hold items - still current: any re-checked plan resurfaced unchanged by step 0, plain English, one line each>
  <Hold items - needs a fresh look: any superseded/dropped item from step 0, plain English on what changed, one line each>
  ```
  Spend/orders/order value/cost-per-order/ROAS come straight from performance-analyst's standing checklist (§1 above — blended, Stitchflow-alone) and are **never omitted silently**: if Stitchflow access failed and a number genuinely couldn't be computed, write "unavailable this week - Stitchflow access failed, see KL-... for details" in its place, don't just drop the line. Plain text, no Markdown formatting characters relied on for structure (asterisks/underscores can break Telegram's parser on real plan text containing underscored field names - use line breaks and plain words instead, per §8). For each `type: decision` plan actually ready for approval, additionally call `scripts/send-telegram-approval.sh <plan-id>` (§8a) so it can be approved/rejected/held directly from Telegram — one call per plan, not folded into the digest notification.

  **Non-India active ad sets get their own named line every week, never silently folded in (added 2026-08-31, real gap — USA and Canada ad sets were live and spending for weeks with zero mention in the digest).** The Spent/Orders/CPO/ROAS block above is Stitchflow-sourced and Stitchflow is India-centric, so it structurally cannot surface non-India activity — that block being "fine" says nothing about whether e.g. `USA Engaged 365D & Followers` or `Canada Engaged 365D & Followers` are healthy. Any ad set targeting outside India that is `effective_status: ACTIVE` this cycle must get one plain-English line in the digest every week (not just when something changed): name, weekly spend, and the one or two result metrics that actually apply to it (e.g. messaging conversations started, link clicks, follower growth — whatever the ad set is actually optimizing for; say plainly if it has zero attributed purchases rather than omitting the line). This is a reporting completeness rule, not a new geographic strategy decision — no disposition call is implied just by including the line.

  **Never send a "Portfolio budget allocation" entry (from either campaign-strategist's §Budget Policy check or media-buyer's own independent re-verification, guardrail 10) through `send-telegram-approval.sh` (added 2026-08-24, real user confusion)** — it's a consolidated summary of the *other* plans' combined effect, not an independently executable action of its own; sending it with its own APPROVE/REJECT/HOLD buttons is contradictory (there's nothing distinct for marketing-lead to execute if tapped, and it doesn't make sense to "approve" a total that only exists if some subset of the real plans get approved). Fold its content into the digest notification's other-findings section in plain English instead (e.g. "If all N plans above are approved, total spend becomes ₹X/day, within the ₹Y ceiling") — informational only, no buttons.
