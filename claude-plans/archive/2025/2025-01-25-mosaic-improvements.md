# Mosaic Improvements: SOP Refinement + TAL Creation

*Created: 2025-01-25*
*Status: Complete*

## Overview

Improve the SOP album/episode art mosaic (less repetition, white background) and create a new TAL mosaic using the same improved techniques.

---

## ✅ Part 1: Mosaic Script Improvements - COMPLETE

Added to `create_mosaic.py`:

| Feature | Flags | Description |
|---------|-------|-------------|
| Background detection | `--background`, `--bg-threshold` | Skip tile placement in background areas |
| Diversity weight | `--diversity` | Penalize frequently-used tiles |
| Per-tile tinting | `--tint`, `--tint-alpha` | Color wash on each tile (not whole image) |
| Random selection | `--no-color-match` | Bypass color matching for variety |
| Config files | `--config` | Load settings from JSON |

---

## ✅ Part 2: TAL Episode Art - COMPLETE

- Downloaded 874 episode art images via `download_tal_episode_art.py`
- Stored in `tal/tiles/`

---

## ✅ Part 3: TAL Mosaic - COMPLETE

**Best output:** `tal/output/tal_20px_random.png` (no tint, pure artwork variety)

**Settings used:**
```bash
python3 create_mosaic.py --config tal/config.json --tile-size 20 --no-color-match --tint-alpha 0
```

**Tinted variants generated:**
- `tal_20px_red_18.png` - #ed0017 at 18%
- `tal_20px_blue_18.png` - #02135B at 18%
- 12% versions also available

**Key learnings:**
- Mono-color logos need `--no-color-match` - otherwise color matching picks tiles that match the logo color (red artwork for red logo)
- Per-tile tinting works better than whole-image overlay for edge quality
- 20px tiles with 874 images = ~200 tiles repeat once, randomly distributed

---

## ✅ Part 4: SOP Mosaic - COMPLETE (Updated 2026-01-25)

**Two approaches tested:**

### A. Album Covers (2026 Spotify album art)
- Config updated: `tiles_dir: ../album_covers`
- Best output: `sop/output/switched_on_pop_*_no_reuse.png`
- Settings: `--no-reuse` for unique tiles, color matching ON

### B. Episode Art (303 episode illustrations) - FINAL
- Uses `sop/tiles/` (episode cards with text + artwork)
- Added `--blend-mode` flag with 6 modes: normal, multiply, screen, overlay, soft_light, color
- Tested per-tile tinting vs cheat overlay - **overlay method won**
- Tested all blend modes at 55% opacity - **normal blend won**

**Final settings:**
| Setting | Value |
|---------|-------|
| Tiles | 303 episode art (`sop/tiles/`) |
| Background | White (skips ~19% of cells) |
| Tile selection | Random (`--no-color-match`) |
| Overlay | 30% normal blend (`--cheat 0.30 --blend-mode normal`) |
| Target | Diagonal split (`sop/targets/sop_p_diagonal.png`) |
| Size | 1760×1760px (40px tiles × 2x enlarge) |

**Best outputs:**
- `sop_final.png` - 30% overlay, diagonal target (primary)
- `sop_final_45.png` - 45% overlay, diagonal target
- `sop_final_white.png` - 30% overlay, white background target

**Comparison artifacts:** `sop/output/compare_blends.html` shows all blend modes at 55%

---

## Session Notes (2026-01-25)

### What was built:
1. **Fixed SOP tiles** - was using episode art (`sop/tiles/`) instead of album covers (`album_covers/`)
2. **Added `--region-tint` feature** - applies different tints based on target image color at each cell
3. **Created 3 target variants** - white, yellow, diagonal split backgrounds
4. **Generated 6 SOP episode art mosaics** - 3 with uniform tint, 3 with region tint

### Key files:
- `create_mosaic.py` - main script with new `--region-tint` flag
- `sop/targets/sop_p_diagonal.png` - new diagonal split target
- `sop/output/sop_ep_*_region.png` - region-tinted outputs

### What user wants next:
- Try different overlay/tint approaches (beyond the current per-tile tint blend)
- Possibly gradient tints, different blend modes, etc.

### Cleanup needed:
- `sop/output/` has 30 files - consider archiving old experiments to `_archive/`
- `detect_text_tiles.py` - utility script created, can delete or keep for future use

---

## ✅ Part 5: Cleanup - COMPLETE

- Added to `.gitignore`: `__pycache__/`, tile/output dirs, `match_progress.log`
- Created show-specific directories: `sop/`, `tal/`
- Updated `CLAUDE.md` with new features

---

## Files Modified

- `scripts/album-cover-mosaic/create_mosaic.py` - all new features
- `scripts/album-cover-mosaic/CLAUDE.md` - documentation
- `scripts/album-cover-mosaic/sop/config.json` - SOP settings
- `scripts/album-cover-mosaic/tal/config.json` - TAL settings
- `scripts/download_tal_episode_art.py` - new scraper
- `.gitignore` - cleanup entries
