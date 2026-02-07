# AI Daily Brief Discovery

*Created: 2026-02-05*
*Last updated: 2026-02-05*
*Branch: `codex/ai-daily-brief-kickoff`*

## Goal

Identify the likely AI Daily podcast source, pick transcript strategy options, and document setup blockers before schema/build work.

## What We Found

### 1) Likely podcast source

The likely target is:
- **The AI Daily Brief (Formerly The AI Breakdown)**

Discovered sources:
- Podnews podcast page: https://podnews.net/podcast/i9ry5
- Podcast RSS (audio): https://feeds.megaphone.fm/VMP5705694065
- Show site: https://www.aidailybrief.ai/
- Show site RSS (posts): https://www.aidailybrief.ai/feed

Notes:
- The Megaphone feed is the primary audio source for episode cadence and episode metadata.
- The show website has episode posts with useful text content and often explicit "mentioned in this episode" style references.

### 2) Transcript-source options (for pilot)

Use a cascading approach for first 25 episodes:

1. **Website episode pages (primary)**
- Pros: Already textual, cheaper/faster than full STT, likely richer in references and links.
- Cons: May not contain full verbatim transcript.

2. **YouTube captions when available (secondary)**
- Pros: Often free captions with better text depth for quotes/context.
- Cons: Not guaranteed for all episodes and quality varies.

3. **Whisper on podcast audio from RSS (fallback)**
- Pros: Complete coverage, deterministic fallback.
- Cons: Additional processing and cost.

4. **Transcript APIs (optional fallback)**
- Candidates from prior project notes: Taddy, Podscribe, Podchaser.

### 3) Setup status in this Codex runtime

- `codex mcp list` currently shows only `playwright`.
- Shell env currently has:
  - `FIRECRAWL_API_KEY`: set
  - `NEON_API_KEY`: set
  - `DATABASE_URL`: not set
  - `NEON_DATABASE_URL`: not set
- Repo currently has no checked-in `.env*` files with DB URL values.

## Recommendation for Pilot (Last 25 Episodes)

1. Ingest last 25 episodes from `https://www.aidailybrief.ai/feed` (post URLs + publish dates).
2. Scrape each post page and extract:
- Core entities (tools, reports, x accounts, benchmarks, models, products)
- Mention contexts (quote/snippet + linked URL if present)
- Sentiment flag and confidence
3. Only use full transcript/STT when post content is too thin for reference extraction.

This gives fast signal to finalize schema before committing to full transcript ingestion cost/complexity.

## Immediate Next Steps

1. Add `DATABASE_URL` (and `NEON_DATABASE_URL` alias) for this repo runtime.
2. Add Neon + Firecrawl MCPs to Codex runtime (optional but recommended for faster direct DB/scrape iteration).
3. Build a "25-episode ingestion + review" script and store output in staging tables.
4. Finalize AI Daily + PCHH-ready schema from observed data variance.
