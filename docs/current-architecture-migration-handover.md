# K&A Marketing — Current Architecture Migration Handover

**Purpose of this document:** a complete, as-built snapshot of the K&A by Karishma and Ashita Meta Ads multi-agent system, captured for migrating the working environment from Claude Code (this CLI-based setup) to VS Code, without losing any existing functionality, decisions, learnings, or guardrails.

**Status:** this is a documentation snapshot only. Nothing in the existing system was modified to produce this document. It describes the system exactly as it works today, **2026-08-18**.

**Scope note:** this project directory also contains two unrelated agent systems in sibling folders — `SEO & Discoverability/` (an SEO/content team) and `Website Engineering/` (a Shopify theme-development team). Both have their own independent `.claude/agents/`, `AGENTS.md`/`CLAUDE.md`, and git repos, and are **out of scope for this document** — this handover covers only the Meta Ads marketing system rooted at the project root (`.claude/agents/`, `docs/`, `knowledge/`).

---

## 1. Complete project architecture and folder structure

```
K&A Marketing/                              ← project root, no git repo at this level
├── .claude/
│   ├── agents/                             ← THE agent definitions (Markdown + YAML frontmatter)
│   │   ├── marketing-lead.md               ← orchestrator + sole execution proxy
│   │   ├── campaign-strategist.md          ← analysis agent
│   │   ├── performance-analyst.md          ← analysis agent
│   │   ├── creative-copywriter.md          ← analysis agent
│   │   ├── media-buyer.md                  ← execution agent (validates only)
│   │   └── social-community-manager.md     ← hybrid: analysis half + execution half
│   └── settings.local.json                 ← locally-approved tool permissions (Bash allowlist, MCP tool grants)
├── .codex/
│   ├── agents/                             ← .toml mirrors of the same 6 agents, for Codex-CLI compatibility
│   └── config.toml
├── .mcp.json                               ← MCP server registration (Stitchflow only, see §8)
├── meta_token.txt.txt                      ← Meta Graph API System User access token (plaintext file, gitignored in spirit though no git repo here)
├── docs/
│   ├── architecture.md                     ← SINGLE SOURCE OF TRUTH, living doc, append-only change log at the end
│   ├── learning-layer-design.md            ← companion deep-design doc for the learning log subsystem
│   ├── brand-brief.md                      ← shared brand reference, read by every agent before producing copy/creative/positioning
│   ├── agent-architecture.md               ← RETIRED, pointer-only stub → architecture.md
│   ├── agent-architecture-v2-review.md     ← RETIRED, pointer-only stub → architecture.md
│   └── charts/
│       └── ka_meta_ads_org_chart.html      ← printable A4 org chart artifact (white background, agent hierarchy)
├── knowledge/
│   ├── learning-log.jsonl                  ← append-only shared memory, one JSON object per line (63 entries as of 2026-08-18)
│   └── RETRIEVAL.md                        ← 7 named grep recipes against the learning log
├── Content/                                ← raw video assets (reels/mp4s) used as source creative for ad builds
├── KA_Meta_Ads_Creative_Deck.pdf           ← reference creative deck
├── _build_backup.json                      ← miscellaneous working file
└── scratch_awareness_daily.json            ← miscellaneous working file
```

**Key structural fact:** there is no application code, no build system, no server. The entire "system" is: (a) Markdown files defining agent instructions/personas, read by the Claude Code CLI's subagent mechanism, (b) a JSONL text file acting as shared memory, (c) direct HTTP calls (via `curl`/Python) to the Meta Graph API and MCP servers for Stitchflow/Shopify. There is no persistent server process; every "run" is a fresh CLI session or agent dispatch that reads these files fresh.

---

## 2. Every agent — roster and exact responsibilities

Defined as Markdown files with YAML frontmatter (`name`, `description`, `tools`) under `.claude/agents/`. Split into two categories by a **platform-level fact**, not a design choice (see §6): whether the agent's job ever requires a live, irreversible write to an external system.

### 2.1 marketing-lead — Orchestrator & sole execution proxy
**File:** `.claude/agents/marketing-lead.md`
**Tools:** Read, Write, Grep, Glob, Bash

Two distinct jobs, kept deliberately separate:
1. **Orchestration** — triages ambiguous/broad requests ("check how ads are doing," "plan next month's campaigns"), delegates to the correct specialist(s), synthesizes their output into one coherent answer. **Never performs a specialist's analysis or planning work itself** — this was a real failure mode caught and fixed once (see §14).
2. **Execution proxy** — the *only* agent that ever calls a Meta/Instagram write endpoint. Not a trust hierarchy — a platform fact: a subagent can never treat a relayed message as the user's own direct consent for a live action, and every message any other agent receives is necessarily relayed. marketing-lead (in practice, the top-level session acting in this role) is the only place the user's own messages land directly.

**Execution protocol (its unique responsibility), exactly:**
1. Receive the validated plan from media-buyer or social-community-manager's execution half.
2. Present it to the user plainly, get direct specific approval of *that exact plan* — not a general "sounds good."
3. Execute verbatim — no redesign, no reinterpretation, no "improvement." If it thinks the plan is wrong, it says so and asks, never silently changes it.
4. Verify via `GET` after the change.
5. Log a `type: change` entry to the learning log, `linked_to` the plan it executed — not optional.
6. Report before/after state plainly to the user.

Meta access: direct `Bash` + `curl`, token from `meta_token.txt.txt`, ad account `act_686170454752172`. Verifies token validity (`GET /debug_token`) before executing anything.

### 2.2 campaign-strategist — Strategy/targeting/budget planning (analysis agent)
**File:** `.claude/agents/campaign-strategist.md`
**Tools:** Read, Write, Grep, Glob, Bash, WebSearch

Owns:
- Campaign/ad set structure (objective, targeting logic, budget split) for both funnels: **lead-gen/enquiry** (Lead Ads forms, WhatsApp click-to-message, IG DM — primary funnel for a high-ticket, appointment-driven purchase) and **website conversion** (direct checkout, best for retargeting/lookalikes, not cold broad targeting).
- Audience strategy: interest/lookalike/retargeting segmentation, exclusions, creative-theme mapping.
- Budget allocation logic, test-vs-scale splits.
- Diagnosing funnel problems from performance-analyst's data.
- **Standing recipient of two handoffs, every time, not case-by-case** (added 2026-08-18): every performance-analyst finding that implies a next move, and every social-community-manager UGC/new-content discovery. Must form an independent read of the data, not rubber-stamp what's handed to it — a real case showed campaign-strategist reaching a materially better-reasoned conclusion than performance-analyst's own recommendation on identical data.

Does not: touch the live ad account (media-buyer/marketing-lead's job); write final ad copy (creative-copywriter's job, though it specifies funnel/audience/angle for the brief).

Never invented numbers — flags assumptions explicitly ("assuming ₹X/day — confirm") rather than guessing.

### 2.3 performance-analyst — Reporting & true attribution (analysis agent, read-only)
**File:** `.claude/agents/performance-analyst.md`
**Tools:** Read, Write, Grep, Glob, Bash, plus Shopify MCP tools (`list-orders`, `run-analytics-query`) and the full Stitchflow MCP tool set (order/customer/revenue/sales tools — see §8 for the complete list)

Owns:
- Pulling Meta `/insights` data at campaign/ad set/ad level: spend, impressions, clicks, CTR, CPM, frequency, results, cost per result, ROAS where tracked.
- **True sales attribution via Stitchflow, not just Shopify** — most high-value orders (custom bridal, WhatsApp/DM, in-studio, international) never touch Shopify checkout. Never concludes a campaign "isn't converting" without checking Stitchflow for matching customer/order records first.
- **Exhaustive account-wide coverage on any broad "performance analysis" request** — every active campaign, every active ad set, every active ad, fresh data each cycle (no reusing stale cached reads), explicit stated action or explicit no-action for every entity. A narrower request (e.g. "check the Add to Cart campaign") only needs that scope.
- **Any finding implying a next move goes to campaign-strategist, every time** — not performance-analyst's own judgment call about whether it's "strategic enough." Pure no-action reconfirmations don't need this handoff.
- **Blended cost-per-order AND blended ROAS every review** — total Meta spend vs. total Shopify+Stitchflow order value/count, labeled directional/correlational, alongside any pixel-attributed numbers. *(Note: as of this session's direct user correction, "all Shopify orders are also punched in Stitchflow" — Stitchflow alone is now the complete order source; Shopify should not be separately summed. This correction is logged in the learning log as `human_knowledge`, `KL-2026-08-18-01`, but has **not yet been reflected into this agent's `.md` file wording**, which still says "Shopify + Stitchflow combined" — see §14, Gap.)*

**Standing output checklist — required on every broad review, not dependent on how the request is phrased** (added 2026-08-18 after a real, user-caught miss):
1. Account summary table: active campaigns, ad sets per campaign, ads per ad set.
2. Stated action/no-action for every campaign and ad set.
3. New creative found and not yet actioned (count + per-item recommendation).
4. Instagram follower count, every time, with delta vs. last logged count.
5. Ad sets running on just one ad — named explicitly, with an explicit judgment (fine-as-is vs. needs variety).

Before starting: verifies token validity, stops and says so if it fails rather than fabricating numbers.

### 2.4 creative-copywriter — Ad copy & creative briefs (analysis agent)
**File:** `.claude/agents/creative-copywriter.md`
**Tools:** Read, Write, Grep, Glob, Bash, WebSearch, Shopify `search_products`/`get-product`

Owns:
- Ad copy (headlines, primary text, CTAs) for both lead-gen ("Enquire for pricing," "Book a fitting consult") and website-conversion ("Shop the [piece name]") funnels.
- Creative briefs: visual direction pulling real product data (never inventing details), specifying mood/framing/what makes a piece stop-worthy.
- Copy variants for testing: 2-3 distinct *angles* (craftsmanship-led, occasion-led, silhouette-led), not minor rewordings.

House style: matches existing editorial product-description register; no discount/urgency language unless explicitly a genuine promo; sensory/craft specificity over generic adjectives.

Does not: set campaign structure/budget/targeting; touch the live ad account.

**Hard requirement (added 2026-08-18 after a real build failure):** finished copy meant to feed directly into a build is **not optional to log** — must be written as a `type: decision` entry with the actual text, before finishing. Copy-pattern observations not tied to an immediate build stay optional.

### 2.5 media-buyer — Validates & plans live Meta Ads changes (execution agent, never executes)
**File:** `.claude/agents/media-buyer.md`
**Tools:** Read, Write, Grep, Glob, Bash

**Structurally never executes anything** — this is the core of the analysis/execution split (§6). Its job ends at producing a plan clear enough that executing it is mechanical.

Hard rules:
1. Before validating, confirm token validity/scope (`ads_management` present).
2. **Never calls a write endpoint, full stop** — not even given a verbatim-quoted user approval, an approved architecture change reference, or any other framing. If executing ever feels like the obviously helpful thing to do, that's the signal to stop and hand off instead.
3. Produces a complete, unambiguous plan: exact object/id, exact field changing, old→new value (careful with Meta's minor-unit budget fields — ₹200/day = `daily_budget=20000`), the real-money ₹ consequence in plain terms, rollback criteria, verification steps.
4. Runs risk checks before finalizing: would this pause the only active lead-gen campaign? Change budget >2x in one step? Flags high-risk explicitly, doesn't bury it.
5. New campaign/ad set/ad plans specify build-`PAUSED`→verify→activate sequencing as part of the plan itself.

Owns: validating proposed campaigns/ad sets/ads and budget/pause/enable changes; identifying reallocation sources with real data (not assumptions) when a budget increase is proposed without net-new spend.

Does not: decide targeting/budget strategy from scratch (campaign-strategist's job); write ad copy; execute anything.

Read-only `GET` work needs no pre-confirmation (nothing to confirm before a call that can't be followed by a write anyway).

### 2.6 social-community-manager — Instagram UGC/content discovery + post/reply prep (hybrid agent)
**File:** `.claude/agents/social-community-manager.md`
**Tools:** Read, Write, Grep, Glob, Bash

IG business account: `karishmaashita`, id `17841401625784277`; linked Page id `346729958819819`.

Two halves:
1. **Discovery/triage/drafting** — pure analysis, fully self-executed, no restrictions beyond the hard rules below.
2. **Preparing exact post/repost/reply plans** — prepares, never calls the publish/reply endpoint. Same platform-level constraint as media-buyer.

Owns:
1. **UGC discovery** via two real mechanisms, with honestly-stated limits:
   - `GET /17841401625784277/tags` — reliable, pollable (actual photo/video tags).
   - `GET /{ig-media-id}/comments` on K&A's own posts — catches @mentions/questions on own content.
   - **Structural gap, stated explicitly**: caption-text mentions of `@karishmaashita` on *other people's* posts are not reliably discoverable by polling — requires a mentions webhook, not set up. Must not imply "searched all of Instagram."
2. **K&A's own new organic content** (added 2026-08-18, previously unowned) — `GET /17841401625784277/media`, reviewed for ad-suitability (product visibility, movement/lighting, on-brand styling) on request or periodically. Distinct from UGC — no repost-permission question, just ad-suitability judgment.
3. **Triage**: for every item — poster handle (or "own content"), permalink, what it shows, recommendation: worth reposting (UGC only) / worth flagging to ads team / neither.
4. **Comment replies**: routine categories (thank-yous, "price please"→redirect to DM/WhatsApp, availability) batch-draftable for approval; anything complaint-like, ambiguous, sensitive, or brand-risk flagged individually, never bundled.
5. **Preparing the exact post/reply plan** (endpoint, exact media URL/caption, or exact reply text + target comment id) — marketing-lead executes verbatim.

Hard rules: a tag is never consent to repost (say plainly whether permission is confirmed or still needed); never calls publish/reply, full stop; comment replies default to individual review; the publish-endpoint restriction is a genuine platform constraint for Stories/Feed/Reels/replies alike.

**Handoff (updated 2026-08-18):** strong UGC/owned content goes to **campaign-strategist** first (decides whether/where it becomes an ad), only then to creative-copywriter (decides how) — not straight to creative-copywriter as before.

---

## 3. Who reports to whom — full delegation flow

```
                         Human (Suraj) — the ONLY place approval is genuinely direct
                                    │
                     ┌──────────────┴───────────────────────────────┐
                     │  can dispatch any agent directly, or route    │
                     │  broad/ambiguous requests through:            │
                     ▼                                                ▼
              marketing-lead                              (direct dispatch to any
         (orchestrator + execution proxy)                  specialist is also valid
                     │                                       and common in practice)
     ┌───────────────┼────────────────────────────┬─────────────────┐
     ▼               ▼                            ▼                 ▼
campaign-strategist  performance-analyst   creative-copywriter  social-community-manager
(strategy/targeting) (reporting/attribution) (copy/briefs)      (discovery/triage half)
     ▲                    │                        ▲                 │
     │  handoff: findings │                        │  handoff: brief │ handoff: strong
     │  implying a next   │                        │  once strategist│ UGC/owned content
     │  move, every time  └────────────────────────┘  decides where │
     └─────────────────────────────────────────────────────────────┘
                     │
                     │ once a plan needs validating/preparing for live execution
                     ▼
        ┌────────────────────────────┐
        │  media-buyer                │  social-community-manager
        │  (validates Meta Ads plans) │  (execution half — post/reply plans)
        └────────────────────────────┘
                     │  both write a `type: decision` plan entry to the learning log
                     ▼
              marketing-lead
      (only after user's direct, in-conversation approval
       of that exact plan → executes verbatim → verifies →
       logs `type: change` → reports before/after)
```

**Chained work is the normal pattern**, e.g.:
- campaign-strategist plans → creative-copywriter writes copy → media-buyer validates the plan → marketing-lead executes after approval.
- social-community-manager surfaces UGC/owned content → campaign-strategist decides whether/where → creative-copywriter briefs it → media-buyer validates the resulting ad → marketing-lead executes.
- performance-analyst finds something actionable → campaign-strategist makes the strategic call → (creative-copywriter if new copy needed) → media-buyer validates → marketing-lead executes.

**Routing table (as used by marketing-lead / the top-level session):**
| Request type | Goes to |
|---|---|
| "How are we doing" / reporting | performance-analyst |
| "Should we change targeting/strategy" | campaign-strategist |
| "Write copy for X" | creative-copywriter |
| "Who's tagging us" / comments / repost | social-community-manager (discovery half) |
| A proposed live Meta Ads change | media-buyer (validates) → marketing-lead (executes after approval) |
| A proposed IG post/repost/reply | social-community-manager (execution half, validates) → marketing-lead (executes after approval) |
| Broad/ambiguous | marketing-lead routes + synthesizes |

**Important operational nuance:** in practice the human frequently dispatches specialists directly rather than routing every request through marketing-lead — this is valid and common. But execution is different: however the plan/approval arrive, the literal live write call happens in whatever conversation the user is directly present in — that conversation is *acting as* marketing-lead when it does so, whether or not the file was formally invoked via the Agent tool.

---

## 4. Who analyses, recommends, decides, validates, prepares, executes

| Function | Agent(s) |
|---|---|
| **Analyses / reports data** | performance-analyst (Meta + Stitchflow + Shopify performance data) |
| **Discovers content** | social-community-manager (UGC tags, comments, K&A's own new posts) |
| **Recommends strategy** | campaign-strategist (turns analyst findings + content discoveries into a strategic call — targeting, budget, whether/where to run an ad) |
| **Writes creative** | creative-copywriter (copy + briefs, only once campaign-strategist has decided where something is going) |
| **Decides** (the actual strategic/business call) | campaign-strategist for strategy; **the human, always**, for anything that becomes a live action |
| **Validates a proposed live change & prepares the exact execution plan** | media-buyer (Meta Ads); social-community-manager execution half (Instagram posts/replies) |
| **Executes** | marketing-lead only — and only the top-level/current conversation with the user's own direct, in-conversation approval of the exact plan |

No agent other than marketing-lead ever calls a Meta/Instagram write endpoint. This is not adjustable by instruction alone — see §6.

---

## 5. The approval flow for live changes

1. A change is proposed (by the human directly, or emerging from campaign-strategist/creative-copywriter analysis work).
2. The relevant execution agent (media-buyer for Meta Ads; social-community-manager for Instagram) **validates** it: checks current state via `GET`, runs risk checks (>2x budget change? touches the only active lead-gen campaign?), and produces a complete plan — exact object id, exact field, old value → new value, ₹ consequence in plain terms, rollback criteria, verification steps.
3. That plan is written to the learning log as a `type: decision` entry **before** the user approves it.
4. The plan is presented to the user, plainly, in the conversation.
5. **The user must give direct, specific approval of that exact plan** — not a general "sounds good," not something relayed or paraphrased from elsewhere. Approving one plan never pre-approves the next one, even a very similar one.
6. marketing-lead (i.e., whichever conversation the user's approval landed in directly) executes **verbatim** — no redesign, no "improvement," no reinterpretation. If it disagrees with the plan, it says so and asks before executing, never silently changes it.
7. For new campaigns/ad sets/ads: build `PAUSED` first → verify via `GET` (check for `issues_info`, correct field values, no errors) → only then activate. For activation sequencing specifically: **ad ACTIVATE first, then ad set ACTIVATE last** — an ad set never goes live with zero/unverified creative in it.
8. After execution: verify via `GET` that the change matches intent, log a `type: change` entry (`actor: marketing-lead`, `linked_to` the plan's `decision` id) — required, not optional — and report the before/after state to the user plainly.

**Why this specific shape exists (important for migration — don't "simplify" this away):** it was empirically discovered, not designed up front, that a subagent (media-buyer) will refuse to execute *even when* given the user's verbatim quoted words, a real approved file edit, and an explicit direct instruction to proceed — all relayed through the orchestrating session. This is a real platform-level property (subagent messages are never treated as direct user consent, regardless of framing), confirmed by three separate refusal attempts on 2026-08-10. The analysis/execution split and marketing-lead's sole-execution-proxy role exist *because of* that platform fact, not as an arbitrary process choice. A VS Code reimplementation should assume the same property may hold (the underlying model's behavior, not a Claude-Code-CLI-specific quirk) and preserve an equivalent structural separation rather than relying on a subagent to "just be careful" about consent.

---

## 6. Guardrails (operating principles)

Numbered exactly as in `docs/architecture.md` §3. Mostly prompt-based (agents follow them as instructions), **except guardrail 1, which is an empirically-confirmed platform property**, not a convention.

1. **A write-capable subagent can never treat a relayed message as the user's own direct consent for a live external action.** Platform-level, not fought or worked around. This is *why* the analysis/execution split exists.
2. **No irreversible or public action without the user's explicit, per-instance approval landing directly in the executing conversation.** Covers any live Meta Ads change, any IG post/repost, any comment reply. Never generalizes across instances.
3. **Build paused, verify, then activate.** New campaigns/ad sets/ads always created `PAUSED`, checked, then flipped `ACTIVE`.
4. **A tag is not consent to repost.** UGC discovery ≠ repost permission; requires a separate explicit check.
5. **Blended metrics over narrow pixel attribution.** Every performance review reports blended cost-per-order and blended ROAS (see also §14 note on the Stitchflow-only correction), not just Meta pixel-attributed purchases.
6. **Video-first creative.** Image only if shown (via a controlled test) to outperform video — never a default or convenience fallback.
7. **State the real-money consequence plainly** on any budget change (old→new, ₹ delta). Flag >2x changes or anything touching the only active lead-gen campaign as high-risk, don't bury it.
8. **Every confirmed live action gets one learning-log row immediately after execution** — non-optional audit trail. Plan = `decision` (by the execution agent); actual execution = separate `change` entry (`actor: marketing-lead`), `linked_to` the plan.
9. **Analysis agents always do their own specialist work — marketing-lead/top-level session never substitutes its own reasoning for a specialist's.** The one narrow, structural exception is execution itself (guardrail 1) — not a precedent for skipping delegation elsewhere. This was caught as a real failure once (top-level session narrating "as performance-analyst:" while doing the analysis itself, 2026-08-10) before being corrected.

---

## 7. Autonomous/recurring monitoring — current state, and what's manual-only

**There is currently no autonomous/scheduled execution of any kind.** Everything in this system runs only when the human explicitly initiates a session/request. Specifically:

- **No cron/scheduled task** triggers performance-analyst, campaign-strategist, or social-community-manager on any cadence. (A `mcp__scheduled-tasks__*` MCP tool set and `CronCreate`/`CronList` tools exist in the broader Claude Code environment and are visible/available, but **nothing in this project currently uses them** — no scheduled task has been created for this account.)
- **Performance analysis** happens only when the human asks ("it's time to do the performance analysis," "how are the ads doing"). There is no daily/weekly digest that fires on its own.
- **UGC/comment/new-content sweeps** are explicitly **on-demand or bundled with an ad check**, not auto-scheduled — this is a standing user preference (see persistent memory, `feedback_ugc_check_cadence`).
- **Experiment check-ins** are not truly autonomous either: an experiment's `follow_up` field states a due date/condition, but nothing polls for it — performance-analyst only checks "open experiments due for review" (learning-log recipe 7) **as a step at the start of a check-in the human initiated**, not as a background trigger.
- **Token/account health** is checked reactively (each agent verifies `debug_token` before starting its own work), not monitored continuously.

**What this means concretely — the gap the user hit directly this session:** any standing goal (e.g. "we want to grow Instagram followers") is only checked/reported *if a broad performance-analysis request happens to be issued* — and even then, only correctly if the analysis agent's own instructions guarantee coverage (which was a real, caught gap — see §14). There is no mechanism that proactively tells the human "the Profile Visits campaign hasn't been reviewed in N days" or "an experiment's stop-rule check-in date has passed" — the human has to ask, or a check-in has to happen to organically surface it.

**For a VS Code migration, if recurring/autonomous checks are wanted, this is new functionality, not a port of something that already runs** — currently 100% of monitoring is manually triggered.

---

## 8. Data sources, APIs, MCPs, tools, credentials, external integrations

### 8.1 Meta Graph API (Facebook/Instagram) — direct HTTP, no MCP
- **No dedicated Meta Ads MCP is used.** Called directly via `curl` (Bash) or Python `urllib` for complex nested JSON payloads.
- **API version in use:** v23.0 (`https://graph.facebook.com/v23.0/...`).
- **Ad account:** `act_686170454752172`.
- **Instagram business account:** `17841401625784277` (handle `karishmaashita`).
- **Linked Facebook Page:** `346729958819819`.
- **Auth:** System User access token stored in plaintext at project root, `meta_token.txt.txt`. Read fresh every time via `TOKEN=$(cat "meta_token.txt.txt" | tr -d '[:space:]')` — **never echoed/displayed in any agent output**, only used directly in calls.
- **Token scopes (as last confirmed):** `ads_management`, `instagram_basic`, `instagram_manage_comments`, `instagram_content_publish`, and related Instagram permissions.
- **Endpoints actually used this session/system:**
  - `GET /debug_token` — token validity check, run by every agent before starting live-account work.
  - `GET /act_.../campaigns`, `/adsets`, `/ads`, `/insights` — performance data, structure checks.
  - `POST /act_.../adcreatives` — new ad creative (image/video/DPA/catalog `template_data`, `asset_feed_spec`, `degrees_of_freedom_spec`).
  - `POST /act_.../ads` — new ad (created `status=PAUSED`).
  - `POST /{ad-id}`, `/{adset-id}` — status changes (`ACTIVE`/`PAUSED`), budget changes (`daily_budget`, minor units — paise, not rupees).
  - `POST /act_.../adimages` — thumbnail upload for `image_hash` — **known broken**: fails with "Application does not have the capability to make this API call" (error code 3). **Workaround in active use:** pass the thumbnail as `image_url` directly inside `video_data`/`template_data` instead of obtaining an `image_hash`.
  - `GET /search?type=adinterest&q=<term>` — required whenever adding interest-based targeting; Meta silently deprecates interest IDs (`error_subcode: 1870247`), and the error response itself returns `alternative_interest_id`/`alternative_interest_name` — never guess an interest ID, always verify/search first.
  - `GET /17841401625784277/tags` — UGC tag discovery.
  - `GET /{ig-media-id}/comments` — comment discovery/replies.
  - `GET /17841401625784277/media` — K&A's own organic post discovery.
  - `POST /17841401625784277/media` + `/media_publish` — publishing (prepared by social-community-manager, called only by marketing-lead).
  - `POST /{comment-id}/replies` — comment replies (same restriction).
  - WhatsApp-destination ads: `instagram_user_id` + `call_to_action:{type:WHATSAPP_MESSAGE, value:{app_destination:WHATSAPP, link:"https://api.whatsapp.com/send"}}` + `page_welcome_message` (a JSON string, VISUAL_EDITOR schema, with `text_format`/`image_format`/`video_format` sub-objects).
  - Dynamic Product Ads (DPA/catalog): `template_data` (not `video_data`) with `{{product.name}}` templating, `product_set_id`, `asset_feed_spec` (`{"ad_formats":["CAROUSEL","COLLECTION"],"optimization_type":"FORMAT_AUTOMATION"}`), and `degrees_of_freedom_spec.creative_features_spec` (~78 named features, all `{"enroll_status":"OPT_OUT"}` in this account's conservative pattern).

### 8.2 Stitchflow (MCP server) — source of truth for total order/revenue
- Registered in `.mcp.json`:
  ```json
  {
    "mcpServers": {
      "stitchflow": {
        "type": "http",
        "url": "https://stitchflow.in/api/mcp",
        "headers": { "Authorization": "Bearer sfp_LYsR6uP25t0O3zjKkn7i-rSswCIZg-Tln7PpcdB7tig" }
      }
    }
  }
  ```
- **This session confirmed Stitchflow was migrated off Emergent to an independent platform and is correctly connected** (verified by checking total order count).
- **Direct user correction, logged 2026-08-18 (`human_knowledge`, `KL-2026-08-18-01`):** "all Shopify orders are also punched in Stitchflow" — Stitchflow is the complete/sole order source going forward; Shopify orders are a subset already captured there, should not be separately summed for blended metrics. (Agent `.md` files have not all been updated to reflect this yet — see §14.)
- Full tool surface granted to performance-analyst: `get_order`, `get_order_timeline`, `get_order_payments`, `list_orders`, `get_customer`, `get_customer_details`, `get_customer_timeline`, `get_customer_purchase_behavior`, `list_customers`, `get_repeat_customers`, `get_sales_by_city`, `get_sales_by_grouping`, `get_revenue_trends`, `get_monthly_summary`, `get_dashboard_stats`, `get_stores_summary`, `get_store_report`.
- A much larger Stitchflow tool surface exists at the environment level (inventory, costing, BOM, capacity, consignment, agents/production-managers, etc.) but is **not** granted to any agent in this project's roster — only the order/customer/revenue subset above is wired to performance-analyst.

### 8.3 Shopify (MCP server) — online-storefront slice only
- Tools referenced in agent files: `list-orders`, `run-analytics-query` (performance-analyst); `search_products`, `get-product` (creative-copywriter, for real product detail in briefs).
- A larger Shopify tool surface (product/collection CRUD, discounts, GraphQL, inventory) exists at the environment level but is not part of this system's working set beyond the above.
- Explicitly **not** the source of truth for total order volume — Shopify captures only self-serve online checkout; most high-value bridal/custom orders bypass it entirely (per `docs/brand-brief.md`).

### 8.4 Google Drive/Sheets (MCP)
- Referenced in `docs/architecture.md` §2 as used for one-off historical data extraction (a legacy pre-Stitchflow order ledger) — **not a live/ongoing integration**, and relevant to the separately-tracked customer-retention project goal (see persistent memory `project_customer_retention_goal`).

### 8.5 Windsor.ai — fully retired
- **Explicitly retired.** All agents call Meta directly. Every agent file carries an explicit instruction not to use any Windsor.ai tool even if one appears available in the environment. (`docs/brand-brief.md` still contains one stale line referencing Windsor for Instagram organic — see §14.)

### 8.6 Local tool access per agent (from YAML frontmatter)
| Agent | Tools |
|---|---|
| marketing-lead | Read, Write, Grep, Glob, Bash |
| campaign-strategist | Read, Write, Grep, Glob, Bash, WebSearch |
| performance-analyst | Read, Write, Grep, Glob, Bash, Shopify (`list-orders`, `run-analytics-query`), full Stitchflow order/customer/revenue tool set (listed §8.2) |
| creative-copywriter | Read, Write, Grep, Glob, Bash, WebSearch, Shopify (`search_products`, `get-product`) |
| media-buyer | Read, Write, Grep, Glob, Bash |
| social-community-manager | Read, Write, Grep, Glob, Bash |

None of the agents have a dedicated Meta Ads MCP tool — all Meta access for all agents is raw `Bash`+`curl`/Python.

### 8.7 Credentials / secrets inventory (for migration — handle deliberately, don't just copy-paste into a new repo casually)
- `meta_token.txt.txt` (project root) — Meta Graph API System User token, plaintext.
- Stitchflow bearer token — currently inline in `.mcp.json` (`sfp_LYsR6uP25t0O3zjKkn7i-rSswCIZg-Tln7PpcdB7tig`), not in a separate secrets file.
- No `.env` file exists in this project; no environment-variable-based secret loading is currently used — both secrets above are read directly from files.

---

## 9. The learning layer

**File:** `knowledge/learning-log.jsonl` — single append-only file, one JSON object per line. **Never edited in place; only appended to.** Resolution of an open item happens via a *later* entry's `linked_to` field referencing the earlier one, not by rewriting history.

**Current size (2026-08-18): 63 entries**, spanning 2026-08-07 to 2026-08-18.
- By type: `outcome` 16, `decision` 13, `observation` 10, `change` 6, `override` 3, `experiment` 2, `human_knowledge` 1, `best_practice` 1.
- By actor: `performance-analyst` 22, `campaign-strategist` 12, `media-buyer` 8, `human` 6, `marketing-lead` 2, `creative-copywriter` 1, `social-community-manager` 1.

### 9.1 Schema
**Required on every entry:** `id` (format below), `date` (ISO), `actor` (agent name or `human`), `type` (enum, 8 values — see §9.2), `subject` (the real Meta object name, used consistently for grep), `summary` (one sentence, human-skimmable).

**Optional but important:** `reasoning`, `evidence` (concrete numbers — required in practice if `confidence` is medium/high and `source` is `measured`), `source` (`measured` / `human_told` / `inferred` — the single most important field for not treating a hunch as a fact), `confidence` (`low`/`medium`/`high`), `tags` (array, reuse existing tags), `linked_to` (array of ids), `supersedes` (id, if this replaces an earlier wrong/outdated conclusion), `follow_up` (a stated next-check-in condition, e.g. "re-check when spend > ₹5000" or a date), `valid_context` (for seasonal/time-bound findings).

**ID format — IMPORTANT, changed mid-session on 2026-08-18:** `KL-<date>-<HHMMSS>` (e.g. `KL-2026-08-18-153042`), date + time-to-the-second, 24h clock, no colons. **Not** a small sequence counter — the original `KL-<date>-<seq>` scheme collided three separate times in one day once multiple agents ran close together, each independently guessing the "next" number from a slightly stale log read. This is now the canonical format documented in `docs/learning-layer-design.md` and used in `media-buyer.md`'s example — **any new implementation should use this format from the start**, not the old sequence-counter one. A `tail -1` sanity check before writing is still good practice but no longer load-bearing for avoiding collisions.

### 9.2 The 8 entry types and when each is used
| Type | Written by | When |
|---|---|---|
| `decision` | Execution agents (media-buyer, social-community-manager) | The validated execution plan, **before** user approval — must contain everything needed to execute (old/new values, rollback criteria, verification steps) |
| `decision` | campaign-strategist | Approved strategic calls that aren't formal tests |
| `experiment` | campaign-strategist | Once the human agrees a specific test should run — **must include a stop-rule/check-in condition decided before results are known**, never invented after |
| `outcome` | performance-analyst | Resolving an open experiment or standing question, judged against the original stop-rule, `linked_to` the original |
| `change` | marketing-lead **only** | Required, immediately after every confirmed live execution, `linked_to` the plan's `decision` id |
| `observation` | performance-analyst, social-community-manager | Something genuinely new/unexpected |
| `human_knowledge` | human (agents should proactively offer to log this) | A general business/customer truth stated unprompted, not a specific campaign instruction |
| `override` | human (agents should proactively offer to log this) | Any time the human declines/overrides an agent's recommendation — **treated as high-signal, explicitly not to be skipped** |
| `best_practice` | Deliberately, by an agent or human | Only after **two or more independent** supporting `outcome` entries for the same underlying claim — never automatic |

### 9.3 What does NOT get logged
- Routine checks confirming nothing changed/nothing new (ten campaigns individually reconfirmed "fine" in one sitting don't need ten entries — or arguably any).
- Draft work not approved/acted on (e.g., a drafted reply that wasn't sent) — *except* if the human explicitly declines a specific draft with a stated reason, that's an `override`.
- Mechanical/plumbing operations (pulling data, checking token validity).
- Near-duplicate of an already-open, unresolved observation — either skip, or log a short entry explicitly `linked_to` the original.

### 9.4 Retrieval — 7 named recipes, documented verbatim in `knowledge/RETRIEVAL.md`
No physical index (deliberately — see rationale in `docs/learning-layer-design.md` §3a: a second data structure that can drift from the source of truth, for no real benefit at current/foreseeable scale of dozens-to-low-hundreds of entries).
1. **Recent** — `tail -n 20 knowledge/learning-log.jsonl`
2. **Campaign-specific** — `grep '"subject":"<exact-subject>"' knowledge/learning-log.jsonl`
3. **Audience-specific** — `grep '"<audience-tag>"' knowledge/learning-log.jsonl`
4. **Creative-specific** — `grep '"<creative-or-product-tag>"' knowledge/learning-log.jsonl`
5. **Experiment outcomes** — `grep '"type":"outcome"' knowledge/learning-log.jsonl` (then filter by subject/tag)
6. **Best practices** — `grep '"type":"best_practice"' knowledge/learning-log.jsonl`
7. **Open experiments due for review** (composite, performance-analyst's standing check) — `grep '"type":"experiment"'`, then for each id, check whether it appears in any other entry's `linked_to`; if not, still open.

**When a real index would become worth it:** only if the log passes ~150–200 entries *and* a specific recipe demonstrably returns too much to skim — fix narrower tagging first, index only if that's not enough.

### 9.5 Per-agent read/write integration
| Agent | Reads (when) | Writes (when) |
|---|---|---|
| campaign-strategist | `human_knowledge`/`best_practice`/prior `experiment`/`outcome` (before proposing anything) | `experiment` (test agreed to run, with stop-rule); `decision` (non-test strategic calls) |
| performance-analyst | Open `experiment`s due for review + recent `observation`/`change` (start of every check-in) | `outcome` (resolving, against the stated stop-rule); `observation` (new findings) |
| media-buyer | Prior `override`/`decision` on the object being validated | `decision` — the validated plan, written once ready, **before** user approval |
| creative-copywriter | `best_practice`/`outcome` on relevant product/theme (before briefing) | Mandatory `decision` for build-feeding copy; optional `observation` otherwise |
| social-community-manager | Prior `override`/`decision` on vendors/accounts (before proposing repost/reply) | `decision` (validated plan); `observation` (recurring patterns) |
| marketing-lead | The specific `decision` entry it's about to execute (confirms it's executing the plan as written) | `change` — required immediately after every confirmed execution, `linked_to` the `decision` |
| human | — | `human_knowledge`, `override` — agents proactively offer to log these |

### 9.6 Confidence & quality discipline
- **Low** — single observation/small sample/first-time pattern. Default for anything new.
- **Medium** — confirmed a second time independently, or a `human_told` statement with clear reasoning.
- **High** — confirmed by 2+ independent resolved experiments/outcomes on the same question, OR a direct confident human statement about their own business not really up for measurement.
- `human_told` entries default to `confidence: high` — the business owner knows their own customers; doesn't need to earn its way up like a measured pattern (unless explicitly flagged as a hunch, then `medium`/`low`).
- Contradictory learnings across time are **never silently resolved** — surfaced explicitly to the human ("earlier we thought X, a later better-powered check found Y"), resolved by a new entry (ideally with `supersedes` set), recency is a tiebreaker signal only, never automatic.

### 9.7 Relationship to the separate persistent-memory system
There are **two distinct memory systems, deliberately not merged**:
- **Learning log** (`knowledge/learning-log.jsonl`) — business/campaign facts: what changed, what was tested, what's true about customers/market.
- **Persistent memory** (`C:\Users\ADMIN\.claude\projects\...\memory\`, indexed by `MEMORY.md`, outside this project directory) — **how to work with the user**: standing preferences, working-relationship context (e.g. "always report blended CPO/ROAS," "UGC checks are on-demand not scheduled," "don't push implementation just because I said, give genuine launch-timing advice," the customer-retention/legacy-order-sheet project goal).

**Disambiguation test used throughout:** does this change how to *talk to the user*, or what to *recommend for the business*? First → memory. Second → learning log.

**Migration note:** the persistent-memory system lives **outside** this project folder entirely, at a path keyed to the Claude Code installation/session structure, not inside `K&A Marketing/`. A VS Code migration needs its own decision about where standing user preferences live — this content will not automatically carry over just by copying the project folder.

---

## 10. The complete Meta Ads performance-analysis workflow

Owned entirely by **performance-analyst**, read-only, never modifies live state.

**Step 0 — before starting:** verify token (`GET /debug_token`), confirm `is_valid`; stop and say so (never fabricate numbers) if it fails. Run learning-log recipe 7 (open experiments due) and recipe 1 (recent) so the review doesn't re-derive conclusions already reached.

**Account level:** for a broad "performance analysis" request, cover the *entire* account exhaustively:
- **Account summary table** — every active campaign, ad sets per campaign, ads per ad set (this is what makes coverage checkable at a glance — added specifically because Profile Visits was once missed silently).
- Every campaign gets a stated action or explicit no-action.

**Campaign level:** for every active campaign — spend, results, cost per result vs. prior period, notable changes.

**Ad set level:** for every active ad set within every campaign — same metrics, fresh-pulled each cycle (not substituting an older cached read just because it wasn't last cycle's headline story). Ad sets running on just one ad get an explicit named call-out with a real judgment (fine as-is, e.g. a single-template DPA that doesn't need variety, vs. worth adding creative variety).

**Ad level:** for every active ad within every ad set — CTR/CPM/frequency (creative-fatigue signals), cost per result.

**Creative analysis:** cross-references social-community-manager's discovery work — new creative found (UGC or K&A's own new posts) and not yet actioned, count + recommendation per item (use / hold / skip).

**Instagram new-content discovery:** not performance-analyst's own job (that's social-community-manager's `GET /17841401625784277/media` + tags/comments work) — but performance-analyst's report must include the *count and status* of pending discoveries, plus the current follower count with delta vs. last logged baseline, every time a broad review touches this account (this account has a stated follower-growth goal via the Profile Visits campaign).

**Stitchflow order/revenue reconciliation:** for every campaign under review, check Stitchflow (by customer name/phone/timing correlation with lead capture) before concluding a lead "didn't convert" — a lead with zero Shopify orders may have converted to a large custom Stitchflow order. Blended cost-per-order and blended ROAS (total ad spend vs. total real order value/count) computed and reported every time, labeled directional/correlational — not just Meta-pixel-attributed purchase numbers. *(See §14 for the not-yet-propagated Stitchflow-is-sole-source correction.)*

**Budget/scaling decisions:** performance-analyst does **not** make these itself — it surfaces the data and hands any finding implying a next move to campaign-strategist, which makes the actual strategic call (scale, cut, reallocate, hold), which media-buyer then validates into an exact execution plan.

**Anomaly flagging:** CPL spikes, CTR/frequency creative-fatigue signals, budget pacing (over/underspend vs. daily budget), lead-volume drops, funnel-stage breaks (e.g. high link clicks + zero leads → landing page/form problem, not targeting).

**Output format:** lead with the headline number + trend (not a metrics wall); always separate "what happened" from "what to do about it"; say which agent should own the next step; produce a concrete recommendation for *every* entity reviewed, not capped at "2-3."

---

## 11. Budget-management logic and limitations

**Logic that exists (as guardrails + campaign-strategist's role):**
- Budget allocation across campaigns/ad sets, and test-vs-scale splits (holding back a % for creative testing before scaling a winner) is campaign-strategist's call.
- Reallocation sources for a proposed budget increase must be backed by real pulled data on candidate campaigns (media-buyer's job when validating) — never assumed.
- Any budget change must state the ₹ consequence plainly (old→new, delta) — guardrail 7.
- Changes >2x in one step, or anything touching the account's only active lead-gen campaign, must be flagged explicitly as high-risk in the plan, not buried.
- Meta budget fields are in minor currency units — ₹200/day is `daily_budget=20000`, a documented gotcha repeatedly relevant when building plans.

**Limitations (stated plainly, not solved):**
- **No automated/algorithmic budget optimization** — every reallocation or increase is a manual campaign-strategist judgment call backed by media-buyer's data pull, approved individually by the human. No CBO/auto-rules layer beyond what Meta's own campaign-level settings do natively.
- **No portfolio-level view or single "total daily spend" guardrail enforced in code** — each budget change is evaluated per-campaign/ad set in isolation; nothing currently sums total account daily spend against a stated ceiling automatically.
- **No rollback tooling beyond the learning log** — the log gives a queryable history of what changed/why, but there's no one-click "undo" for a bad live budget change; reverting means someone manually issuing the opposite change. Stated as an acceptable manual-improvisation risk at current campaign volume/count (~7 active campaigns, daily budgets mostly hundreds-to-low-thousands ₹), not yet worth building for.
- Reallocation-source identification depends on media-buyer correctly recognizing a source campaign's budget is *actually available* (not already committed/spent) — this was caught as a live risk this session (a proposed funding source turned out to already be stale/spent) and corrected in-session rather than being structurally guarded against.

---

## 12. All guardrails and execution rules

See §6 for the 9 numbered guardrails in full. Additional execution-specific rules not already covered there:
- Ad-set activation sequencing: **ad ACTIVATE first, then ad set ACTIVATE last** — an ad set must never go live with zero or unverified creative in it.
- Two near-identical creative pieces are staggered in activation (days apart), not launched simultaneously — an operational pattern used this session (two similar lehenga ad variants) though not written into any agent file as a formal numbered rule; worth deciding explicitly whether to formalize during migration.
- Deprecated Meta interest-targeting IDs must be resolved via `GET /search?type=adinterest&q=<term>` before use, never guessed; if Meta's own error response suggests an alternative, evaluate fit rather than force-substituting blindly (document explicitly when an alternative is a poor match and the interest is dropped instead).
- Ad creatives are immutable once created — changing copy requires creating a *new* `adcreatives` object and re-pointing the ad's `creative` field, never editing in place.

---

## 13. Scheduled checks, follow-ups, stop rules, re-check mechanisms

**Nothing is truly scheduled/autonomous (see §7).** What exists is a *convention* for tracking due re-checks manually:
- `follow_up` field on any learning-log entry — free text, e.g. `"re-check when spend > ₹5000"` or `"re-check 2026-08-14"`. Checked only when a human-initiated review happens to run learning-log recipe 7.
- `experiment` entries require a stop-rule stated *before* the test starts (metric + threshold + check-in condition) — evaluated later, at whatever check-in happens to occur, strictly against that pre-stated rule, never a criterion invented after seeing results.
- **Currently open/pending re-checks as of 2026-08-18** (tracked only in conversation continuity / the learning log, not in any automated reminder system):
  - Add to Cart Remarketing creative-swap: re-check ~5-7 days post-swap (~2026-08-21 to 08-23).
  - Blush Pink Lehenga ad: staggered activation ~3-5 days after Sage Green (~2026-08-21 to 08-23).
  - Profile Visits creative variety (2 new video variants): staggered a few days after the 2026-08-18 budget change.
  - Interest Stack creative variety (1-2 video variants): staggered 3-5 days after the 2026-08-18 targeting fix.
  - Interest Stack CPM/cost-per-conversation vs. baseline: post-targeting-fix check.
  - WhatsApp catalog-DPA ad: ~14-day stop-rule check-in (~2026-09-01 from activation).
- **No mechanism currently pings the human when one of these dates arrives** — surfacing depends entirely on either the human asking, or a broad review happening to run recipe 7 and noticing.

---

## 14. Known architectural gaps, weaknesses, and things that depend on manually asking

This section is the most important one for a faithful migration — these are real, discovered-in-use limitations, not hypothetical concerns.

1. **No autonomous/scheduled triggering of anything.** Every check, review, sweep, or re-check happens only because the human asked, in that specific session. (§7, §13)
2. **Instagram mention discovery is structurally incomplete.** No reliable polling for `@handle` mentions in *other people's* caption text — only tags and comments-on-own-posts are discoverable. A mentions webhook would fix this; not set up.
3. **Most guardrails are prompt-based, not technically enforced.** Only guardrail 1 (relayed-message-≠-consent) is confirmed as an actual platform property; everything else (build-paused-verify-activate, state the ₹ consequence, blended metrics, etc.) relies on the agent's instructions being followed, not a hard boundary. A VS Code reimplementation on a different underlying model should re-verify guardrail 1's platform property rather than assume it transfers automatically.
4. **No audit/rollback tooling beyond the learning log.** Queryable history exists; one-click undo does not.
5. **Subagent definitions don't hot-reload mid-session** (confirmed twice in this system's history) — editing a `.md` file mid-session doesn't affect `Agent` tool calls made in that same session; a fresh session is needed. This is a real operational gotcha worth explicitly re-testing in whatever VS Code mechanism replaces the `Agent` tool.
6. **`docs/brand-brief.md` still contains one stale reference to Windsor.ai** ("Instagram: @karishmaashita (connected to Windsor.ai for organic + will connect for Meta Ads)") despite Windsor being fully retired account-wide — a small drift the grep-check discipline (§2, integration-change discipline in `architecture.md` §2) is designed to catch but hasn't yet been applied to this specific file.
7. **The Stitchflow-is-sole-order-source correction (`KL-2026-08-18-01`, `human_knowledge`) has not been propagated into agent `.md` files.** `performance-analyst.md` still describes blended metrics as "Shopify + Stitchflow combined" — per the user's direct statement this session ("all shopify orders are also punched in stitchflow"), Shopify should no longer be separately summed. This is a real, live drift between the learning log (correct, current) and the agent instruction file (stale) that should be fixed as part of, or immediately after, migration.
8. **Two-near-identical-creative staggering is an operational pattern, not a written rule.** Used this session (lehenga ad variants) by direct human instruction each time, never generalized into any agent's `.md` file as a standing rule — worth deciding whether to formalize.
9. **No portfolio-level total-spend guardrail.** Budget risk-checking happens per-change, not against any account-wide daily-spend ceiling.
10. **Reallocation-source staleness isn't structurally guarded against.** Caught once this session in conversation (a proposed funding source had already been spent) — currently relies on the human or an agent noticing in the moment, not a systematic check.
11. **The persistent-memory system (standing user preferences) lives entirely outside this project folder**, at a path tied to the Claude Code installation, not inside `K&A Marketing/`. Migrating the project folder alone will not carry this content — it needs a deliberate decision about where it lives in the new environment. (§9.7)
12. **CONFIRMED: `.codex/agents/*.toml` files are significantly out of sync with `.claude/agents/*.md` and should NOT be treated as a source of truth.** Verified directly (line counts are ~15-30% shorter across all 6, and `performance-analyst.toml`'s content is checked word-for-word): the `.toml` versions still reflect the **pre-2026-08-10 architecture**, before the analysis/execution split existed — e.g. `performance-analyst.toml` still says "You report and recommend; media-buyer and the user decide" (the `.md` version correctly says findings route through campaign-strategist), still hands digests to "media-buyer (for direct execution)" (media-buyer never executes anything as of 2026-08-10), still caps recommendations at "2-3" (removed in the `.md` on 2026-08-18), and is missing the entire standing reporting checklist, the campaign-strategist handoff rule, and the Stitchflow-sole-source correction. **For migration: the `.claude/agents/*.md` files are the only current source of truth. The `.toml` files should be regenerated from the `.md` files, or dropped, not migrated as-is.**
13. **No formal mechanism preventing the top-level session from doing a specialist's work itself** beyond guardrail 9 as a written instruction — this failure mode has already happened once in this system's history (2026-08-10) and was only caught because the human was paying close attention, not because anything structurally prevented it.
14. **`docs/agent-architecture.md` and `docs/agent-architecture-v2-review.md` are retired stub files** kept only so old links don't break — genuinely superseded, safe to leave behind or drop entirely in a migration, but flagged here so they're not mistaken for still-relevant source material.
15. **Credentials are stored as plaintext files, not environment variables or a secrets manager** (`meta_token.txt.txt`, the Stitchflow bearer token inline in `.mcp.json`). Fine for a single-operator local CLI setup; worth a deliberate decision in a VS Code migration about whether to keep this pattern or move to `.env`/a secrets store — flagged as a decision point, not silently carried over or silently "improved" without the user weighing in.

---

## 15. Files important to the architecture — inventory with purpose

| File | Purpose |
|---|---|
| `docs/architecture.md` | **Single source of truth.** Agent roster, guardrails, data/integration layer summary, learning-layer summary, known limitations, scale/scope statement, architecture diagram (mermaid), and the append-only Architecture Change Log recording every dated decision and why it was made. Read this first in any future session. |
| `docs/learning-layer-design.md` | Companion deep-design doc for the learning log subsystem specifically — full schema rationale, retrieval design, confidence/quality discipline, phased implementation plan. Historical Phase-1 content is preserved as-written even where superseded, explicitly marked, rather than silently rewritten. |
| `docs/brand-brief.md` | Shared brand reference — pricing, positioning, funnel reality, tone/copy rules, creative-format default (video-first). Every agent reads this before producing copy/creative/positioning/targeting decisions. Contains one known stale Windsor.ai reference (§14). |
| `knowledge/learning-log.jsonl` | The shared, append-only institutional memory — every decision, experiment, outcome, change, observation, human-stated fact, override, and best practice, one JSON object per line. The single most valuable data asset to carry into any migration intact. |
| `knowledge/RETRIEVAL.md` | The 7 named grep recipes used verbatim by every agent instead of inventing ad-hoc searches against the learning log. |
| `.claude/agents/marketing-lead.md` | Orchestrator + sole execution-proxy agent definition. |
| `.claude/agents/campaign-strategist.md` | Strategy/targeting/budget-planning agent definition. |
| `.claude/agents/performance-analyst.md` | Reporting/attribution agent definition, including the standing exhaustive-coverage checklist. |
| `.claude/agents/creative-copywriter.md` | Ad copy/creative-brief agent definition. |
| `.claude/agents/media-buyer.md` | Meta Ads change validation/planning agent definition (never executes). |
| `.claude/agents/social-community-manager.md` | Instagram discovery + post/reply-plan agent definition (never publishes). |
| `.claude/settings.local.json` | Locally pre-approved tool-permission allowlist (specific Bash commands, specific MCP tool grants) — a Claude-Code-CLI-specific mechanism; will need an equivalent (or none, depending on VS Code's permission model) in the new environment. |
| `.mcp.json` | MCP server registration — currently only Stitchflow. The Shopify and Google Drive/Sheets MCP connections referenced in agent files are registered at the broader environment level, not in this file — worth confirming their exact registration source before migration. |
| `meta_token.txt.txt` | Meta Graph API System User access token, plaintext. Treat as a secret; do not display/log its value. |
| `.codex/agents/*.toml` | Parallel Codex-CLI-format agent definitions — sync status vs. the `.claude/agents/*.md` files not verified (§14, gap 12). |
| `docs/charts/ka_meta_ads_org_chart.html` | Printable A4 reference chart of the current agent hierarchy/reporting lines — useful visual cross-check against this document, not a source of truth itself. |
| `docs/agent-architecture.md`, `docs/agent-architecture-v2-review.md` | Retired stub files, pointer-only, safe to leave behind. |
| `Content/*.mp4` | Raw source video assets used in recent ad builds (First Love, reels) — working creative assets, not architecture, but referenced by filename in learning-log entries. |
| `KA_Meta_Ads_Creative_Deck.pdf` | Reference creative deck, background material. |

---

*End of migration handover document. See the verification section reported separately in conversation for a cross-check of this document against the live project files.*
