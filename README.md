# K&A Meta Ads System

Multi-agent Meta (Facebook/Instagram) Ads system for K&A by Karishma and Ashita — a luxury Indian bridal couture brand. This repo is the permanent, independent home for the system: the single source of truth for VS Code interactive work and the DigitalOcean droplet's scheduled headless runs alike.

**Start here:** [`docs/architecture.md`](docs/architecture.md) — the living source of truth for the agent roster, guardrails, approval model, budget policy, and the append-only Architecture Change Log recording every real decision made. Read it before doing anything else in this project, human or agent.

## What this is

Six Claude Code subagents (`.claude/agents/*.md`) — `marketing-lead` (orchestrator + sole live-execution proxy), `campaign-strategist`, `performance-analyst`, `creative-copywriter`, `media-buyer`, `social-community-manager` — plus a shared append-only learning log (`knowledge/learning-log.jsonl`) that gives the system institutional memory across sessions and across runs.

**Core guarantee, unchanged since this system's earliest design and never up for negotiation:** no live Meta or Instagram write happens without the user's direct, real-time, in-conversation approval — whether the request originates from an interactive VS Code session or an unattended scheduled run on the droplet. See `docs/architecture.md` §3 guardrail 1.

## How to work with this project

- **Interactive work:** open this folder directly as its own VS Code workspace root (not nested under another folder) — that's what lets Claude Code discover and dispatch the six agents by name via the Agent/Task tool.
- **Scheduled/unattended work:** three cadences (daily heartbeat, weekly full review, monthly Strategic Intelligence Review) run headlessly on the existing DigitalOcean droplet, triggered by cron, via `scripts/run-*.sh`. Full design in [`docs/proactive-operations.md`](docs/proactive-operations.md).
- **GitHub is the only path** project changes travel between VS Code and the droplet — the droplet hard-syncs to `origin`'s exact state before every scheduled run. Secrets and operational logs never touch GitHub; see `.gitignore` and `docs/proactive-operations.md` §3/§7.

## Repo layout

```
.claude/agents/     the 6 agent definitions
docs/                architecture.md (source of truth), brand-brief.md, learning-layer-design.md,
                      proactive-operations.md, current-architecture-migration-handover.md (history)
knowledge/           learning-log.jsonl (append-only memory), RETRIEVAL.md (query recipes)
prompts/             the exact headless prompt text for each scheduled cadence
scripts/             append-learning-log.sh (safe multi-writer log protocol),
                      run-heartbeat.sh / run-weekly-review.sh / run-monthly-review.sh
```

Not in this repo, by design: `meta_token.txt.txt` and the real `.mcp.json` (secrets, placed locally on every machine that needs them, never via git — see `.mcp.json.example`), `telegram_config.txt` (same, see `telegram_config.txt.example`), raw creative video assets (not architecture, several exceed GitHub's file-size limits), and anything belonging to this brand's other, independent projects (Website Engineering, SEO & Discoverability) — out of scope for this repo entirely.

## History

This project was migrated twice: first from a Claude Code Desktop/CLI-only setup to VS Code (see `docs/current-architecture-migration-handover.md`), then from a shared multi-project folder (`K&A Marketing/`) into this dedicated repo, adding GitHub + droplet operation along the way (see the 2026-08-19 entries in `docs/architecture.md`'s Architecture Change Log for both).
