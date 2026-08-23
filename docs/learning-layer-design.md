# K&A Learning Layer — Detailed Design

Companion document to `agent-architecture-v2-review.md`. This is the full design of the single recommendation that review treated as highest-priority: a shared, append-only learning log that closes the "no learning loop" gap without adding agents or infrastructure.

**Governing constraint, stated up front:** everything below has to survive being maintained by one person, part-time, using tools that already exist (Read/Write/Grep/Glob/Bash). If a design element needs a database, an embedding model, or a dedicated maintenance job to stay useful, it's out of scope until real usage proves the simple version insufficient. That's not a hedge — it's the actual design principle this whole thing is built on.

---

## 1. The Learning Log — structure

**Format: JSON Lines (`.jsonl`), one JSON object per line, at `knowledge/learning-log.jsonl`.**

Why JSONL over a Markdown table or a database:
- **Append-only by nature.** Adding an entry is a single line append — no risk of corrupting existing rows, which a Markdown table edit always risks (misaligned columns, a Write-tool full-file rewrite that could drop a line). **As of 2026-08-19, appends go through `scripts/append-learning-log.sh`, not a raw `echo >>`** — with two independent writers now possible at any given moment (interactive VS Code sessions, and unattended droplet cron runs), a plain append-and-push has a real race: two writers reading the file at roughly the same time and pushing close together could otherwise result in a rejected push being mishandled, or worse, a force-push silently discarding one writer's entry. The script performs fetch → rebase onto the latest remote state → append → commit → push, retrying on a rejected push (never force-pushing) and failing loudly rather than guessing if a true conflict occurs. This preserves the file's append-only property under concurrent writers instead of just describing it.
- **Grep-able as text, parseable as data.** `grep -i "broad advantage"` finds it instantly for a human or agent skimming; a one-line Python `json.loads()` per line makes it a real dataset the moment you need to filter/sort/aggregate. Markdown tables give you the first property poorly and the second not at all.
- **No schema migration pain.** Adding a new optional field later just means new lines have it and old ones don't — nothing breaks. A spreadsheet or SQL table would need a migration step.

### Fields

**Required on every entry** (kept minimal deliberately — a field nobody reliably fills in is worse than not having it):

| Field | Type | Purpose |
|---|---|---|
| `id` | string | Unique, sortable — `KL-2026-08-18-153042` (date + time-to-the-second, 24h clock, no colons). **Not a small manually-tracked sequence number** — that scheme collided three times in one day (2026-08-18) once multiple agents started running close together in time, each independently guessing the "next" number from a slightly stale read of the log. Time-to-the-second makes collision structurally unlikely without requiring any cross-agent coordination. Before writing, `tail -1 knowledge/learning-log.jsonl` as a sanity check is still worth doing, but the ID itself no longer depends on getting that check right. |
| `date` | ISO date | When the entry was logged. |
| `actor` | string | Which agent (or `human`) wrote this entry. |
| `type` | enum | See §2 — `decision` / `experiment` / `outcome` / `change` / `observation` / `human_knowledge` / `override` / `best_practice` |
| `subject` | string | What this is about — campaign/ad set/audience/creative name. Use the *real* Meta object name consistently so grep across an entity's history actually works. |
| `summary` | string | One sentence, human-readable. This is the field a skim reads. If you can only write one thing well, write this one well. |

**Optional, but expected on most entries:**

| Field | Type | Purpose |
|---|---|---|
| `reasoning` | string | Why — the justification behind a decision or the hypothesis behind an experiment. |
| `evidence` | string | Concrete numbers backing it (e.g. `"CTR 5.2% vs 1.8% baseline, ₹331 spend, n=47 clicks"`). Required if `confidence` is `medium` or `high` and `source` is `measured` — a confident measured claim with no evidence attached shouldn't be trusted next time it's read, so the schema should discourage that combination in practice even though it isn't mechanically enforced. |
| `source` | enum | `measured` (from real Meta/Stitchflow/Shopify data) / `human_told` (Suraj said so) / `inferred` (agent reasoning without hard data backing it) / `published` (added 2026-08-19 — an external, citable, third-party source: official Meta documentation/announcements, credible industry reporting; used by campaign-strategist's competitor/platform intelligence work, where "measured" would misleadingly imply it came from this account's own data). This is the single most important field for preventing the system from quietly treating a hunch as a fact — see §5. |
| `confidence` | enum | `low` / `medium` / `high` — see §4 for how this is assigned and how it evolves. |
| `tags` | array of strings | Freeform, but reuse existing tags where possible (e.g. `["first-love", "broad-advantage-plus", "budget", "india"]`) — consistent tags are what make retrieval work at all without a real search system. |
| `linked_to` | array of ids | Other entries this one relates to — an `outcome` links to the `experiment`/`decision` it resolves; a `best_practice` links to the outcomes that support it. |
| `supersedes` | id (optional) | If this entry replaces an earlier conclusion that turned out wrong/outdated, say so explicitly rather than leaving two contradictory entries with no relationship. |
| `follow_up` | string | A stated next check-in condition — `"re-check when spend > ₹5000"` or `"re-check 2026-08-14"`. This is what lets `performance-analyst` know what's still open without re-reading the whole log. |
| `valid_context` | string (optional) | For time/context-bound learnings — `"wedding season 2026, re-verify next season"`. Prevents a seasonal finding from being silently treated as a permanent truth. |
| `telegram_summary` | string (optional, added 2026-08-23) | A short, plain-English, pre-formatted-with-real-linebreaks version of the plan, written by the execution agent (media-buyer / social-community-manager) alongside `summary` for every `type: decision` plan. This is what `scripts/send-telegram-approval.sh` actually displays — the full technical `summary` (object ids, field names, rollback math) is the audit record, not something a person should have to read on a phone to decide whether to approve. Names the real campaign/ad set/ad (or Instagram object) affected and describes the change in 1-2 plain sentences, no jargon. Entries without it fall back to a truncated `summary` snippet (older entries, or anything not written with this convention yet).

**Why not also track "status" (open/resolved) as a field on the original entry?** Because that would require *editing* an existing line, which breaks append-only. Instead: an `experiment` is "open" until some later entry's `linked_to` references it with `type: outcome`. Resolution is a *relationship*, not a mutated field. This is the one piece of schema design worth understanding deeply, because it's what keeps the whole log honest and simple at the same time — history is never rewritten, only added to.

---

## 2. What gets logged — and what doesn't

**The test for whether something becomes an entry:** *would a human relying on this system next month be worse off if this weren't recorded?* If the answer is genuinely no, don't log it. A log full of "checked X, all normal" noise is worse than no log, because it trains whoever reads it to stop reading.

**Always log:**
- Every confirmed live write action on the ad account or Instagram (campaign/ad set/ad created, paused, enabled; budget changed; audience created) — `type: change`. This is the audit trail, non-negotiable, matches the v2 review's "critical" recommendation.
- Every test proposed once the human agrees it should actually run — `type: experiment`, with a stated hypothesis and stop-rule *before* results are known.
- Every resolved analysis question — "is Broad Advantage+ still holding efficiency" answered yes/no with evidence — `type: outcome`, `linked_to` the original decision/experiment if one exists.
- Unexpected/anomalous performance worth remembering (a real surprise, not a routine metric) — `type: observation`.
- Any time the human declines or overrides an agent's recommendation — `type: override`. **This is undervalued and easy to skip — don't skip it.** It's some of the highest-signal data in the whole system: it's the gap between what an agent thought was right and what the person who actually knows the business decided, and it's exactly the kind of thing that should stop an agent from proposing the same declined thing again.
- Anything the human states as general business/customer knowledge, unprompted by a specific campaign question — `type: human_knowledge`. See §5.
- A generalizable lesson, once genuinely earned — `type: best_practice`. See §4 for the promotion bar.

**Never log:**
- Routine checks that confirm nothing changed and found nothing new. If a check-in reconfirms an *already-open* follow-up with no new information, that's still worth a short `outcome` entry (it moves the follow-up from open to resolved) — but ten campaigns individually confirmed as "still fine, no change" in one sitting doesn't need ten entries, or arguably any, unless one of them was specifically flagged as something to watch.
- Draft work that wasn't approved or acted on (e.g., comment replies drafted and shown but not sent) — nothing happened, there's nothing to remember yet. The exception: if the human explicitly declines a specific draft with a stated reason, *that's* an `override` entry, because the reasoning is worth keeping even though the action wasn'ttaken.
- Mechanical/tool-level operations (pulling data, checking a token's validity). These aren't learnings, they're plumbing.
- A near-duplicate of an already-open, unresolved observation. If the same thing gets noticed again with no new information, don't create a second entry — either skip it, or if it's genuinely worth reinforcing, log a short entry that's explicitly `linked_to` the original rather than a disconnected clone.

---

## 3. Retrieval

**Mechanism: `grep` on `subject`/`tags`, filtered by `type`, read the actual matching JSON lines, done.** No ranking algorithm, no embeddings, no semantic search — at a few hundred rows, a keyword match against a consistent naming/tagging convention will find the relevant history reliably, and it's something every agent can already do with the `Grep` tool with zero new capability.

**Before making a recommendation, an agent should:**
1. `Grep` the log for the specific subject (the exact campaign/ad set/audience/creative name being discussed) — this catches the direct history.
2. `Grep` for relevant tags (product line, audience type, geography) — this catches related-but-not-identical history, e.g. a lesson about "Broad Advantage+ in India" that's relevant to a new "Broad Advantage+ in Canada" question.
3. Filter mentally (or with a second grep/`type` field check) to the entry types that matter for the task at hand — `campaign-strategist` planning a new test cares most about `human_knowledge`, `best_practice`, and past `experiment`/`outcome` entries; `performance-analyst` doing a check-in cares most about open `experiment` entries with a due `follow_up` and recent `observation`/`change` entries.

**How many past learnings to consider:** all matches for the specific subject — at this scale there won't be many (single digits to low dozens per subject for years). Cap defensively at the most recent ~20 matches per query so a future, much larger log doesn't flood context; that cap is a safety valve, not an expected real limit any time soon.

**How relevance is determined:** exact/substring match on subject and tags. Nothing fuzzier is needed yet — see Phase 2 in §9 for when (if ever) that changes.

### 3a. Retrieval layer — six named queries, not an index

**Decision: no physical secondary index (no tag→id map, no rebuilt cache file).** At realistic volume for this system (dozens to low hundreds of entries for years, per the Phase 2 threshold in §9), a `grep` over the entire log is already sub-millisecond. A real index would be a *second data structure that has to stay in sync with the source of truth* — that's a new failure mode (index drifts from the log, someone trusts the stale index) for zero measurable performance benefit. Building it now would repeat the exact mistake already ruled out elsewhere in this design (knowledge graphs, embeddings) — solving a scale problem that doesn't exist yet.

What actually stops an agent from linearly re-reasoning over the whole log isn't an index, it's **not having to invent a query from scratch every time.** So the retrieval layer is a set of named, pre-defined recipes — documented once in `knowledge/RETRIEVAL.md` (nine as of 2026-08-21; this table shows the original six, with the three added since described in prose below it rather than the table being kept in lockstep — `knowledge/RETRIEVAL.md` is the current source of truth for the exact count and wording, not this table), used verbatim by every agent instead of ad-hoc searching:

| Recipe | When to use | Query pattern |
|---|---|---|
| **Recent** | General context on what's happened lately, any task | `tail -n 20 knowledge/learning-log.jsonl` |
| **Campaign-specific** | Planning or reviewing a specific campaign/ad set | `grep '"subject":"<exact-subject>"' knowledge/learning-log.jsonl` |
| **Audience-specific** | Targeting decisions involving a named audience | `grep '"<audience-tag>"' knowledge/learning-log.jsonl` |
| **Creative-specific** | Briefing or reviewing a specific creative/product theme | `grep '"<creative-or-product-tag>"' knowledge/learning-log.jsonl` |
| **Experiment outcomes** | Before proposing a test similar to a past one | `grep '"type":"outcome"' knowledge/learning-log.jsonl` (then filter by subject/tag as above) |
| **Best practices** | Start of any planning task, before proposing anything | `grep '"type":"best_practice"' knowledge/learning-log.jsonl` |

A seventh, composite recipe worth naming explicitly because `performance-analyst` needs it every check-in: **open experiments due for review** — `grep '"type":"experiment"' knowledge/learning-log.jsonl`, then for each id check whether any entry's `linked_to` array contains it (a two-grep pattern, still no index, still trivial at this volume).

An eighth, added 2026-08-19 for the daily heartbeat: **due-now sweep** — generalizes the seventh beyond just experiments to any entry carrying a `follow_up` field due today or earlier with no later resolving entry (`grep '"follow_up"'`).

A ninth, added 2026-08-21 for Geographic & Customer Demographic Intelligence (`docs/architecture.md` §3c): **geography & demographic** — the same tag-grep pattern as the audience/creative-specific recipes above, against a consistent tagging convention: `geo-<city|state|country>` for geography (e.g. `geo-mumbai`), `age-bracket-<range>` for Meta's platform age-bracket findings (e.g. `age-bracket-25-34`), and `dob-coverage` specifically for entries tracking known-DOB coverage over time. No new entry type — these are ordinary `observation`/`experiment`/`decision`/`outcome` entries, distinguished by tag alone, same as any other subject area.

These aren't enforced by tooling — they're a documented convention every agent's instructions point to, the same way the blended-metrics rule or the confirm-before-write rule are conventions, not code. That's consistent with how every other guardrail in this system already works, and it's the right amount of structure for the actual problem (consistency and low friction), not the problem this design deliberately isn't solving (large-scale search).

**When this would actually need to become a real index:** if the log passes the ~150–200 entry Phase 2 threshold *and* a specific recipe above is demonstrably returning too much to skim (e.g. a heavily-reused tag like `"india"` matching dozens of unrelated entries). At that point the fix is likely narrower tags first (cheap), and only a real index if that's not enough — not before.

**Handling contradictory learnings:** never silently pick one and move on. If a grep turns up two entries that disagree (e.g., a July observation says X, an August one with better evidence says not-X), the agent should surface both explicitly when presenting its recommendation — "earlier we thought X, a later and better-powered check found Y, worth knowing these conflict" — and let the resolution happen as a new entry (ideally with `supersedes` set) rather than the agent quietly trusting whichever it saw first or last. Recency is a *tiebreaker signal*, never an automatic resolution — a well-evidenced older finding can be more trustworthy than a thin newer one, and the agent should say so if that's the case rather than defaulting to "newest wins."

---

## 4. Learning quality — avoiding a log full of confident nonsense

**The core discipline: `confidence` and `source` are never optional in spirit, even though only `type`/`subject`/`summary` are mechanically required.** An agent writing an entry should always be able to answer "how do I know this, and how sure am I" — if it can't, the honest entry is `type: observation`, `confidence: low`, not a confidently-worded `decision`.

**Confidence levels, defined concretely (not computed, assigned by the writing agent against these criteria):**
- **Low** — a single observation, small sample size, or a first-time pattern noticed once. Default for anything new.
- **Medium** — either confirmed a second time independently, or a `human_told` statement given with clear reasoning (Suraj explaining *why* something is true about his customers, not just asserting it).
- **High** — confirmed by two or more independent resolved experiments/outcomes on the same underlying question, OR a direct, confident human statement about their own business that isn't really up for measurement (see §5 — some human knowledge is just true because it's the business owner's own knowledge, not because it needs statistical confirmation).

**Promotion from observation to `best_practice`:** requires **two or more independent supporting `outcome` entries** for the same underlying claim, each `linked_to` in the new `best_practice` entry, written deliberately (by an agent or the human, not automatically) once that bar is met. "Independent" matters — two check-ins re-confirming the exact same single test don't count as two confirmations; it needs to have actually been tested/observed twice in genuinely different circumstances (different time period, different audience, etc.).

**Why this stays manual, not automatic:** with the actual data volume this system has (a handful of active campaigns, low daily spend, dozens rather than thousands of data points), automatic pattern-promotion would manufacture false confidence out of noise almost immediately. A human or agent explicitly deciding "yes, this has now been shown twice, it's a real pattern" is slower but honest. Revisit automatic promotion only if data volume genuinely grows to where manual review becomes the bottleneck — not before.

---

## 5. Human-provided knowledge

When Suraj states something as a general truth about the business ("luxury bridal customers respond better to WhatsApp than website checkout") rather than a specific campaign instruction, that's a **`type: human_knowledge`** entry:

```json
{"id":"KL-2026-08-07-05","date":"2026-08-07","actor":"human","type":"human_knowledge","subject":"customer-behavior","summary":"Luxury bridal customers respond better to WhatsApp than website checkout for high-value pieces.","source":"human_told","confidence":"high","tags":["whatsapp","customer-behavior","bridal"]}
```

**How it's distinguished from measured results:** the `source` field, always. Every time an agent surfaces or acts on a claim, it should be able to say (and should say, in its output to the human) whether that claim is `measured` (came from real Meta/Stitchflow/Shopify data), `human_told` (you said so), or `inferred` (the agent's own reasoning without hard backing). This isn't a minor bookkeeping detail — it's the difference between "we know this" and "you told us this, and we're trusting it," and conflating the two is exactly how a system quietly starts making confident claims it can't actually back up.

**Default confidence for `human_told` entries: `high`.** You know your own customers and business better than any measurement this system can currently make — a stated business truth from the owner isn't something that needs to earn its way up from `low` the way a measured pattern does. If you flag something as a hunch rather than a known truth, say so and it gets logged as `medium` or `low` instead.

**Relationship to the existing memory system** (the `.claude/…/memory/` files with `feedback`/`project`/`user`/`reference` types): these stay separate and serve different purposes. The memory system is about **how to work with you** — preferences, standing instructions, communication style. The knowledge log's `human_knowledge` entries are about **facts about your business/customers/market** that inform campaign decisions. Don't merge these — a preference like "always report blended ROAS" doesn't belong mixed in with "bridal customers prefer WhatsApp," even though both come from you. If it's ever unclear which one something belongs in, the test is: does this change how I should *talk to Suraj*, or does it change what I should *recommend for the business*? First is memory, second is the knowledge log.

---

## 6. Experiment tracking

Lightweight, built entirely from the schema above — no separate system:

**Created:** `campaign-strategist`, once the human agrees a test should actually run, writes a `type: experiment` entry with a real hypothesis, the specific variants, the budget, and — critically — a **stated stop-rule decided in advance**: the exact metric and threshold that will determine the verdict, plus a check-in date or triggering condition (`follow_up`).

```json
{"id":"KL-2026-08-10-01","date":"2026-08-10","actor":"campaign-strategist","type":"experiment","subject":"add-to-cart-remarketing-india","summary":"Testing whether widening to Add to Cart 180D fixes underspend without hurting cost-per-purchase.","reasoning":"90D audience too small to spend ₹200/day budget.","evidence":"3-day spend ₹198 of ₹600 budgeted (33%).","tags":["catalog","remarketing","india","add-to-cart"],"follow_up":"re-check 2026-08-17: verdict = widen if spend rate >70% of budget AND cost-per-purchase within 2x of the 90D-only baseline"}
```

**Tracked:** it just sits in the log as an unresolved entry. No dashboard, no separate tracker — `performance-analyst`'s standing check-in process includes grepping for `type: experiment` entries with a `follow_up` date at or before today that have no linked `outcome` yet.

**Evaluated:** at the check-in, `performance-analyst` appends a `type: outcome` entry, `linked_to` the experiment's id, with the actual measured result judged **against the stop-rule that was written down in advance** — not re-litigated from scratch, not judged against a new criterion invented after seeing the result (that's how experiments quietly become "we'll call it whatever we want after the fact").

**Concluded:** the experiment is resolved the moment a `linked_to` outcome entry exists — no status field to update, the relationship is the resolution (see §1).

**Successful experiments becoming reusable learnings:** exactly the promotion rule from §4 — once the same underlying hypothesis has two independent `outcome` entries with a "win" verdict, write a `best_practice` entry linking both. `campaign-strategist` checks `best_practice` entries first, before designing any new plan for a similar subject, so a confirmed pattern actually changes future behavior instead of just sitting recorded and unused.

---

## 7. Workflow integration — per agent

| Agent | Reads | When | Writes | When |
|---|---|---|---|---|
| **campaign-strategist** | `human_knowledge`, `best_practice`, past `experiment`/`outcome` entries for the relevant subject/tags — including recipe 9 (`geo-`/`age-bracket-`/`dob-coverage` tags) for geographic/demographic decisions | Start of any planning/strategy task, before proposing anything | `type: experiment` (hypothesis + stop-rule) | Once the human agrees a specific test should run |
| | | | `type: decision` | For approved strategic calls that aren't formal tests (e.g., a targeting change made on judgment, not an experiment); includes geography-disposition and age-informed decisions (`docs/architecture.md` §3c) |
| **performance-analyst** | Open `experiment` entries with a due `follow_up`; recent `observation`/`change`/`outcome` entries for campaigns under review | Start of every check-in | `type: outcome` (resolving open experiments, against their stated stop-rule) | Whenever a check-in resolves something open |
| | | | `type: observation` | When something genuinely new/unexpected turns up — including geographic/demographic shifts, tagged `geo-`/`age-bracket-`/`dob-coverage` (§3c), always with stated data coverage |
| **media-buyer** *(execution agent — validates & plans, never executes; see `docs/architecture.md` §1/§3 guardrail 1)* | Recent `override`/`decision` entries for the object being validated (has the human already said no to something like this?) | Briefly, before finalizing a plan | `type: decision` (the validated execution plan — old/new values, rollback criteria, verification steps) | Once the plan is finalized, **before** the user approves it |
| **creative-copywriter** | `best_practice`, `outcome` entries tagged with the relevant product/theme | Before writing a new brief/copy | (rarely writes; optional low-frequency `observation` if it notices a copy-specific pattern) | — |
| **social-community-manager** *(discovery/drafting half is a normal analysis agent; publish/reply half is an execution agent with the same constraint as media-buyer)* | Prior `override`/`decision` entries about specific vendors/accounts (avoid re-asking, avoid re-surfacing declined content) | Before proposing a repost/reply action | `type: decision` (the validated post/reply plan) | Once the plan is finalized, before the user approves it |
| | | | `type: observation` | For notable recurring UGC/comment patterns |
| **marketing-lead** *(orchestrator, and the sole execution proxy — see `docs/architecture.md` §1)* | Broad grep across subjects when synthesizing a cross-specialist answer; the specific `type: decision` entry it's about to execute | When invoked for an ambiguous multi-specialist request; before executing any plan | `type: change` — **required, immediately after every confirmed live execution**, `linked_to` the `decision` it executed | Whenever it executes a plan on the user's direct approval |
| **human (you)** | — | — | `type: human_knowledge`, `type: override` | Whenever you state a general business truth, or decline/override a recommendation — an agent should proactively ask "should I log this?" rather than you having to remember to say so |

---

## 8. Memory maintenance

**Nothing gets deleted — this is a historical record, not a live state cache.** But relevance decays, and the system should handle that explicitly rather than pretending every old entry is equally trustworthy forever:

- **Superseding, not overwriting:** if a new finding contradicts an older one, the new entry sets `supersedes: [old_id]`. Retrieval should prefer non-superseded entries when summarizing "what do we currently believe," but the old entry stays in the log — useful history, e.g. for understanding *why* a belief changed.
- **Time/context-bound entries:** anything seasonal or platform-dependent (a Meta algorithm-driven observation, a wedding-season-specific pattern) should carry `valid_context`, so it's not silently trusted as a permanent truth a year later without a fresh check.
- **No hard expiry.** At this data volume, actively deleting or archiving entries adds maintenance overhead for no real benefit — the whole log is small enough to skim for years before "too much old stuff" becomes a real problem.
- **Conflicting historical lessons** are handled the same way as in retrieval (§3): surfaced, never silently resolved. The log is allowed to contain contradictions across time — that's honest — as long as retrieval always shows both sides rather than picking one.

---

## 9. Phased implementation plan

*Note (2026-08-10): Phase 1 below is preserved as-written for historical accuracy — it reflects the original model where media-buyer/social-community-manager wrote `type: change` directly. That model was superseded the same day by the analysis/execution split in `docs/architecture.md` §1 (a platform constraint made direct execution by those agents impossible, not just undesirable). Current behavior: execution agents write `type: decision` (the plan); marketing-lead writes the linked `type: change` after executing. See §7's table above, which has been updated to the current model.*

**Phase 1 — minimum viable, implementable in the next couple of weeks:**
1. Create `knowledge/learning-log.jsonl` with the schema above.
2. Update `media-buyer.md` and `social-community-manager.md`: add a hard rule — "append a `type: change` entry to the learning log immediately after every confirmed live action." This alone delivers the audit trail, the highest-value single piece.
3. Update `performance-analyst.md`: add "before starting a check-in, grep the learning log for open experiments (due `follow_up`, no linked outcome) and recent entries on the campaigns under review; write `outcome`/`observation` entries for anything resolved or newly found."
4. Update `campaign-strategist.md`: add "before proposing anything, grep the learning log for `human_knowledge`/`best_practice`/prior `experiment`/`outcome` entries on the relevant subject; write an `experiment` entry (with a stated stop-rule) whenever a test is agreed to run."
5. **Backfill a handful of entries now**, for real: this session already produced several resolved conclusions worth not losing — Broad Advantage+ holding at ₹550/day, Add to Cart 90D underspending with the planned 180D widen, the Nikki Mehra pause holding. Writing these retroactively costs a few minutes and means the log starts with real history instead of at zero.
6. No new tools needed — every agent involved already has `Read`/`Write`/`Grep`/`Glob`/`Bash`.

**Phase 2 — only if real usage justifies it, likely months out, not scheduled speculatively:**
- If the log passes roughly 150–200 entries and grep-and-skim genuinely starts feeling slow or unwieldy, do a mechanical upgrade: load the same JSONL into SQLite (a single Python script, no new dependency the account doesn't already have access to) or a Google Sheet for easier filtering/sorting. Same schema, same data — an upgrade, not a redesign.
- If tag-based retrieval starts missing relevant history because tags drifted inconsistent, write down a short controlled-vocabulary list of common tags (not a taxonomy system) and note it should be reused rather than reinvented per entry.
- If experiment volume grows enough that "grep for open experiments" stops being a quick scan, add a tiny summary step (a short generated "open experiments" list) rather than a dashboard.
- **Explicitly not in Phase 2 unless the simple version has demonstrably failed:** knowledge graphs, vector embeddings/semantic search, automatic confidence scoring, automatic lesson extraction. These solve problems this system doesn't have yet at this data volume — building them now would be solving a scale problem before it exists, which is the exact mistake this whole design is trying to avoid.

**Phase 3 — years out, mentioned only for completeness, not planned:** multi-brand namespacing (already cheap via the `brand` field the v2 review recommended adding to the schema now), and folding a future retention/WhatsApp agent's outcomes into the same log once that channel actually exists.

---

## Summary

One file. Seven event types. A handful of required fields, a few more optional ones that matter more than they look like they do (`source` and `confidence` especially). Five agents each get one short paragraph added to their instructions about when to read and when to write. No new agent, no database, no embeddings, nothing that needs babysitting. The entire mechanism for "smarter after every campaign" is: write down what you decided and why, write down what actually happened, and read that before deciding again. It's boring by design — boring is what survives five years of one person maintaining it part-time.
