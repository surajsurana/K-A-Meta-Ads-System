# K&A by Karishma and Ashita — Brand Brief (shared reference for all marketing agents)

> Every agent in this project should read this file before producing copy, creative direction, or audience/positioning decisions. Keep it up to date as the brand evolves.

## What we sell
Luxury Indo-Western occasion and bridal couture — structured corsets, lehengas, sarees, and drape ensembles. Signature pieces: sculpted corset silhouettes, hand-embellishment (zari, cutdana, crystal), bold color stories (sapphire, maroon, electric blue, mustard, fuchsia).

## Price tier
₹30,000 – ₹280,000+ per piece (INR). This is a considered, high-involvement purchase, not an impulse buy. Ads and landing experiences should never read as discount- or volume-driven.

*(Updated 2026-07-22, second correction same day: entry price corrected from ₹38,000 to ₹30,000 — Suraj confirmed live products priced at ₹30,000 are correct, not a data error. Meta Ads team: flag if this affects existing ad copy/targeting.)*

## Positioning
Bold, structured, modern silhouettes with heritage craftsmanship. "Not your usual corset moment" — confident, editorial, detail-obsessed. Occasion/festive/bridal context (weddings, receptions, sangeet, cocktail events).

## Store facts
- Domain: karishmaashita.com (Shopify) — online storefront only. **Correction, 2026-07-22:** this previously said "ready-to-ship pieces" — that was wrong. Per Suraj directly: pieces are **mostly made-to-order**, with true ready-to-ship inventory being rare/the exception rather than the norm. Site-stated production timelines run 20–45 working days depending on category (drapes/corset sarees ~20–25 days, Indo-Western ~30–35 days, lehengas ~40–45 days), shipped free worldwide via DHL Express. **Meta Ads team: if any existing ad copy or landing page promises fast/ready-to-ship delivery, that needs to change** — this materially affects delivery-time expectations set in ads.
- Returns/exchanges: generally not offered, since pieces are made-to-measure.
- Consultations: video/virtual consults available to any client, not just those able to visit the Santacruz studio in person.
- Customization: existing designs can sometimes be customized (color, embellishment, fit) depending on the request and the design team's assessment — not guaranteed, evaluated case-by-case.
- Currency/market: INR, India, IST
- Instagram: @karishmaashita (Windsor.ai fully retired account-wide as of 2026-08-10 — all agents access Meta/Instagram directly via the Graph API, see `docs/architecture.md` §2)

## Order data: Shopify is NOT the full picture
Shopify only captures online checkout orders. The real system of record is **Stitchflow** (stitchflow.in) — order management software originally built inside this business and now a separate SaaS product. It holds the full order history: made-to-order/custom bridal pieces, WhatsApp/DM orders, in-person/studio orders, and international clients — none of which necessarily flow through Shopify checkout. Any ad attribution or "did this lead actually convert" analysis must check Stitchflow, not just Shopify, or it will undercount real sales significantly. An MCP connection to Stitchflow is configured in this project's `.mcp.json` (may require a session restart to activate).

## Funnel reality
- Primary goal: **leads and enquiries** (Meta Lead Ads, WhatsApp click-to-message, DM, enquiry forms) — high-ticket buyers usually want a consult, sizing conversation, or appointment before purchase.
- Secondary goal: **direct website conversions** for lower-friction pieces or repeat/known buyers.
- Both funnels matter; campaigns should be structured to serve each with different creative and CTAs (never "Shop Now" as the only CTA — "Enquire", "Book a Consult", "DM to Order" belong alongside it).

## Tone and copy rules
- No discount language, no urgency/FOMO gimmicks ("sale ends tonight"), no generic e-commerce phrasing.
- Emphasize craftsmanship, exclusivity, fit/structure, occasion relevance.
- Product names and descriptions are already editorial in style (e.g. "Sapphire Corset and Pants," "Deep Purple cutout corset with pre-stitched drape") — match that register in ad copy.
- Visuals should look like the product photography already on the site (styled, editorial), not stock/generic.

## Creative format default
Default to **video** for all paid ad creative (Reels/video content), not static images. Only use an image instead of video for a specific ad if there's a demonstrated performance reason to prefer it (e.g. a controlled test showing that image outperforming the video alternative) — never as a default or fallback for convenience. If video can't be attached programmatically (e.g. a tooling limitation), flag it and get it resolved rather than quietly shipping an image in its place.

## Pending input
The website-theme project for this brand has a Creative Director agent with its own brand/creative guidelines. Those haven't been ported in yet — if you have specifics from that agent (visual direction, typography, color system, tone-of-voice doc), paste them here or tell the Creative & Copy agent directly so this brief stays consistent with the site.
