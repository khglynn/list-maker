# Album Cover Mosaic Generator

*Last updated: 2026-01-25*

Creates mosaic artwork from album/episode covers for podcast logos.

**Shows supported:**
- **SOP (Switched On Pop)** - Album art from song recommendations (~400 tiles)
- **TAL (This American Life)** - Episode art from archives (874 tiles)

## Show-Specific Directories

Each show has its own folder with config, tiles, targets, and output:
```
sop/config.json, sop/tiles/, sop/targets/, sop/output/
tal/config.json, tal/tiles/, tal/targets/, tal/output/
```

**Using configs (recommended):**
```bash
python3 create_mosaic.py --config tal/config.json
python3 create_mosaic.py --config sop/config.json --tile-size 30  # override settings
```

## Quick Start

### Step 1: Install Dependencies

```bash
cd ~/DevKev/personal/list-maker/marketing
pip install spotipy python-dotenv Pillow numpy tqdm psycopg2-binary
```

### Step 2: Download Album Covers

**Option A: From Neon database (SOP songs)**
```bash
python download_album_art.py --from-db --show-id 1 --output ./album_covers
```

**Option B: From a Spotify playlist URL**
```bash
python download_album_art.py --playlist "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID" --output ./album_covers
```

This will download all unique album covers (640x640px) to `./album_covers/`.

### Step 3: Get Target Image

You need the SOP podcast logo/artwork to use as the target. Save it as `sop_logo.png` in this folder.

The SOP logo can be found at: https://switchedonpop.com (grab from their website or podcast apps)

### Step 4: Generate Mosaic

```bash
python create_mosaic.py --target sop_logo.png --tiles ./album_covers/ --output sop_mosaic.png
```

#### Optional Parameters

| Flag | Description | Default |
|------|-------------|---------|
| `--tile-size 50` | Size of each album tile in pixels | 40 |
| `--no-reuse` | Don't repeat any album cover | Off (allows reuse) |
| `--max-reuse N` | Max times a single tile can be used (0 = unlimited) | 0 |
| `--min-distance N` | Min grid distance before same tile can repeat | 3 |
| `--enlarge 2` | Scale factor for final image | 1 |
| `--background MODE` | Background handling: `auto`, `white`, `black`, `R,G,B`, or `none` | none |
| `--bg-threshold 0.7` | Fraction of cell that must match background to skip (0.0-1.0) | 0.7 |
| `--diversity 0.5` | Diversity weight: 0 = pure color match, higher = more variety | 0 |
| `--no-color-match` | Random tile selection (ignores color matching) | off |
| `--tint HEX` | Per-tile color tint: `02135B` or `#ed0017` | - |
| `--tint-alpha 0.2` | Tint opacity (0.0-1.0) | 0.25 |
| `--region-tint` | Apply different tints based on target color at each cell | off |
| `--cheat 0.2` | Blend original image on top (0.0-1.0) for recognition | 0 |
| `--config FILE` | Load settings from JSON config file | - |

**Examples:**

```bash
# Higher resolution output (each tile is 80px)
python create_mosaic.py --target sop_logo.png --tiles ./album_covers/ --tile-size 40 --enlarge 2

# No tile repetition (needs 2500+ unique albums)
python create_mosaic.py --target sop_logo.png --tiles ./album_covers/ --no-reuse

# Smaller tiles = more detail (but tiles less recognizable)
python create_mosaic.py --target sop_logo.png --tiles ./album_covers/ --tile-size 20

# White background with variety (recommended for logos)
python create_mosaic.py --target sop_logo.png --tiles ./album_covers/ --background white --diversity 0.4

# Auto-detect background from image corners
python create_mosaic.py --target sop_logo.png --tiles ./album_covers/ --background auto --bg-threshold 0.6

# Reduce repetition without background detection
python create_mosaic.py --target sop_logo.png --tiles ./album_covers/ --diversity 0.5 --max-reuse 3
```

### Background Detection

The `--background` flag enables smart background handling:

- **`auto`**: Samples pixels from image corners to detect background color
- **`white`**: Uses RGB(255,255,255) as background
- **`black`**: Uses RGB(0,0,0) as background
- **`R,G,B`**: Specific color (e.g., `255,128,0` for orange)
- **`none`**: No background detection (default)

Cells where >70% of pixels (adjustable with `--bg-threshold`) match the background color will be filled with solid background instead of tiles. This keeps logos clean.

### Diversity Weight

The `--diversity` flag reduces tile repetition by penalizing frequently-used tiles:

- **0.0**: Pure color matching (tiles with best color match always win)
- **0.3-0.5**: Moderate diversity (good balance for most use cases)
- **1.0+**: Strong diversity (may sacrifice color accuracy for variety)

The algorithm adds a usage penalty to each tile's color distance score, making less-used tiles more competitive.

## How It Works

1. **Download script** fetches album cover URLs from Spotify (either via playlist or from your Neon database of matched tracks)
2. **Mosaic script** divides target image into a grid, calculates average color of each cell, finds best-matching album cover by color similarity
3. **No cheating**: Unlike many mosaic tools, this does NOT overlay a transparent version of the target image. It's pure album covers arranged by color.

## Tips for Best Results

- **More tiles = better results**: 500+ unique albums recommended, 2000+ ideal
- **Tile size trade-off**: Smaller tiles = more detail in the mosaic, but individual albums harder to see
- **Target image**: Simple logos with solid colors work best. Complex photos need more tiles.
- **Color variety**: If your tile pool lacks certain colors, those areas will look off

## File Structure

```
marketing/
├── CLAUDE.md              # This file
├── create_mosaic.py       # Main mosaic generator
├── download_album_art.py  # Downloads SOP album covers from Spotify
├── sop/                   # Switched On Pop
│   ├── config.json        # Mosaic settings
│   ├── tiles/             # Album covers (gitignored)
│   ├── targets/           # Logo images
│   └── output/            # Generated mosaics (gitignored)
│       └── _archive/      # Old experiments (keep finals in output/)
└── tal/                   # This American Life
    ├── config.json        # Mosaic settings
    ├── tiles/             # Episode art (gitignored)
    ├── targets/           # Logo images
    └── output/            # Generated mosaics (gitignored)
        └── _archive/      # Old experiments (keep finals in output/)
```

**Output organization:** Keep final/best outputs in `output/`, move experiments to `output/_archive/`.

## Environment

Uses the same Spotify OAuth as the main `spotify_match.py` script:
- Reads `.env` from `~/DevKev/personal/spotify-bulk-actions-mcp/.env`
- Uses cached auth from `~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/`

Required env vars (already configured if you've used the Spotify MCP):
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `NEON_DATABASE_URL` (only for `--from-db` mode)

## TAL-Specific Notes

TAL has a mono-color logo (red letters), so color matching picks warm/red tiles and looks bad. Use `--no-color-match` for random selection instead.

**TAL episode art** was downloaded via `../pipeline/scrapers/tal/download_episode_art.py` which reads JSON from `pipeline/_cache/tal/` and downloads `og:image` URLs.

**Per-tile tinting** (`--tint HEX --tint-alpha N`) applies a color wash to each tile individually - useful for unifying diverse artwork under a single color theme without a whole-image overlay.

**Region-based tinting** (`--region-tint`) detects the target image color at each cell and applies matching tints:
- Pink regions → pink tint (#F472B6)
- Black regions → black tint
- Yellow regions → yellow tint
- White regions → no tile (background) or no tint

This is useful for multi-color logos like SOP where different regions should have different tints.

## SOP-Specific Notes

**Two tile sources available:**
1. `album_covers/` - 2026 Spotify album covers from songs discussed (best for color variety)
2. `sop/tiles/` - 303 episode illustration cards (includes text cards + artwork)

**Target images in `sop/targets/`:**
- `sop_p_white.png` - Pink P + black shadow on white background
- `sop_p_yellow.png` - Pink P + black shadow on yellow background
- `sop_p_diagonal.png` - Pink P + black shadow with diagonal split (white top-left, yellow bottom-right)

**Episode art workflow:** Use `--no-color-match` (random selection) + `--region-tint` to apply per-region tints since color matching doesn't work well with varied episode illustrations.

## Current State (Jan 2026)

| Show | Tiles | Status | Best Output |
|------|-------|--------|-------------|
| TAL | 874 | Done | `tal/output/tal_20px_random.png` (no tint) |
| SOP (album art) | 2026 | Done | `sop/output/switched_on_pop_*_no_reuse.png` |
| SOP (episode art) | 303 | Done | `sop/output/sop_final.png` |

**TAL variants:** tinted at 12%/18% in red (#ed0017) and blue (#02135B).

### SOP Episode Art Final Settings (Recommended)

**Best output:** `sop/output/sop_final.png`

| Setting | Value |
|---------|-------|
| Tiles | 303 episode art images (`sop/tiles/`) |
| Background | White (skips ~19% of cells) |
| Tile selection | Random (`--no-color-match`) |
| Overlay | 30% normal blend of original logo |
| Target | Diagonal split (`sop/targets/sop_p_diagonal.png`) |
| Size | 1760×1760px (40px tiles × 2x enlarge) |

**Command:**
```bash
python3 create_mosaic.py \
  --tiles sop/tiles \
  --target sop/targets/sop_p_diagonal.png \
  --output sop/output/sop_final.png \
  --tile-size 40 --enlarge 2 \
  --background white --bg-threshold 0.7 \
  --no-color-match \
  --cheat 0.30 --blend-mode normal
```

**Variants:**
- `sop_final.png` - 30% overlay, diagonal target (primary)
- `sop_final_45.png` - 45% overlay, diagonal target
- `sop_final_white.png` - 30% overlay, white background target
