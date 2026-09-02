# SOP Episode Art Scraping & Mosaic Plan

*Created: 2025-12-26*
*Status: Completed*

## Goal
Scrape episode artwork from SOP website, download images locally, and use them for a mosaic.

## What We Built

### Episode Art Scraper (`scripts/download_sop_episode_art.py`)

Downloads episode artwork from switchedonpop.com with 3-tier episode number extraction:

1. **From filename** - e.g., `453+Cher.jpg` → 453 (245 episodes)
2. **From page content** - looks for `**EPISODE XXX**` pattern (89 episodes)
3. **From database URL** - matches page URL to episodes table (87 episodes)

**Results:** 397/453 episodes (88% coverage)

### Mosaic Generation

Used existing `create_mosaic.py` with settings:
- `--max-reuse 2` - each tile used max 2 times
- `--min-distance 8` - same tile can't appear within 8 cells

Generated 9 variants:
- 3 backgrounds: white, yellow, black
- 3 cheat levels: 0%, 15%, 30%

## Files Created/Modified

| File | Description |
|------|-------------|
| `scripts/download_sop_episode_art.py` | Episode art scraper with DB integration |
| `scripts/episode-art/` | 397 downloaded episode images (gitignored) |
| `scripts/album-cover-mosaic/sop_p_*.png` | 9 mosaic variants (gitignored) |
| `.gitignore` | Added `scripts/episode-art/` |

## Key Learnings

- SOP episode URLs in our DB contain episode numbers: `/episodes/001-heartbreak`
- Older episodes don't have episode numbers in image filenames
- 56 episodes missing artwork (mostly older episodes with non-standard filenames)
