# Learning Log — Retrieval Recipes

Eight named queries against `knowledge/learning-log.jsonl`. Use these verbatim instead of inventing a search each time — full design/rationale in `docs/learning-layer-design.md` §3/§3a.

Every recipe assumes you're in the project root (`C:\Users\ADMIN\Desktop\Claude AI Projects\K&A Marketing`).

## 1. Recent
General context on what's happened lately. Use for any task where you just want the latest state of things.
```bash
tail -n 20 knowledge/learning-log.jsonl
```

## 2. Campaign-specific
Planning or reviewing a specific campaign/ad set. Use the exact `subject` string as it appears in prior entries (check recipe 1 if unsure of exact naming).
```bash
grep '"subject":"<exact-subject>"' knowledge/learning-log.jsonl
```

## 3. Audience-specific
Targeting decisions involving a named audience.
```bash
grep '"<audience-tag>"' knowledge/learning-log.jsonl
```

## 4. Creative-specific
Briefing or reviewing a specific creative/product theme.
```bash
grep '"<creative-or-product-tag>"' knowledge/learning-log.jsonl
```

## 5. Experiment outcomes
Before proposing a test similar to one that's been tried before.
```bash
grep '"type":"outcome"' knowledge/learning-log.jsonl
```
Narrow further by piping into a second grep for a subject/tag.

## 6. Best practices
Start of any planning task, before proposing anything — check this first.
```bash
grep '"type":"best_practice"' knowledge/learning-log.jsonl
```

## 7. Open experiments due for review (composite — performance-analyst's standing check)
```bash
grep '"type":"experiment"' knowledge/learning-log.jsonl
```
Then, for each `id` returned, check whether it appears in any other entry's `linked_to` array — if not, it's still open. This is a two-step grep, not an index; see the design doc for why that's sufficient at current scale.

## 8. Due-now sweep (added 2026-08-19 — the daily heartbeat's standing first step)
Generalizes recipe 7 beyond just experiments: any entry carrying a `follow_up` field — a staggered-activation date, a re-check condition, a standing review cadence note — that's due today or earlier and has no later entry resolving it.
```bash
grep '"follow_up"' knowledge/learning-log.jsonl
```
For each match, read the `follow_up` text and judge whether its condition/date has been reached, then check (same two-step pattern as recipe 7) whether a later entry already resolved it via `linked_to`. This is the check marketing-lead runs first in every scheduled/heartbeat run, before deciding which specialist(s) to actually dispatch — if nothing is due and nothing else is flagged by the daily anomaly pull, that's a silent no-op, not a forced escalation.

---

**When to add a real index instead of grepping:** if the log passes ~150-200 entries *and* a specific tag (e.g. an overused one like `"india"`) is returning too much to skim. Fix narrower tagging first; only build an index if that's not enough.
