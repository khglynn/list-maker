#!/usr/bin/env python3
"""
Spotify Playlist Sync Script for list-maker project.

Queries matched songs from Neon and adds them to the Spotify playlist.
Handles duplicates by checking existing playlist tracks first.

Usage:
    python sync_playlist.py --show-id 1              # SOP playlist
    python sync_playlist.py --show-id 2              # TAL playlist
    python sync_playlist.py --show-id 1 --dry-run    # Preview only
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, NamedTuple, Set

import psycopg2
from psycopg2.extras import RealDictCursor
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SPOTIFY_SCOPE, ensure_spotify_token  # noqa: E402

# =============================================================================
# Constants
# =============================================================================

BATCH_SIZE = 100         # Tracks per Spotify API call (max 100)
API_DELAY = 0.5          # Seconds between API calls
MAX_RETRIES = 3

# The one key run_pipeline reads to decide a step failed (run_pipeline.STEP_FAILURE_KEY
# / record_step_failures). Duplicated rather than imported because run_pipeline imports
# sync_show from here — importing back would be circular. tests/test_sync_playlist.py
# pins the two spellings against each other so they cannot drift apart silently.
STEP_FAILURE_KEY = "failures"

# Show configuration - add new shows here
SHOWS = {
    1: {
        "name": "Switched On Pop - All Songs Ever Discussed",
        "playlist_id": "0cEVeX4pdHf5RJOiTRzgxX",
        "acronym": "SOP",
    },
    2: {
        "name": "This American Life: Full Music Archive",
        "playlist_id": "3d7fjfrTTKvrl7VHv5JzIz",
        "acronym": "TAL",
    },
}

# Universal description template - {songs}, {episodes}, {acronym}, {date} are interpolated
DESCRIPTION_TEMPLATE = (
    "{songs:,} songs across {episodes} {acronym} episodes. "
    "Last updated {date}. "
    "Support: buymeacoffee.com/kevinhg. Requests: hi@kevinhg.com."
)

# =============================================================================
# Spotify Client
# =============================================================================

DEFAULT_CACHE_PATH = "~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/.cache"


def get_spotify_client(cache_path: str = None) -> spotipy.Spotify:
    """Initialize Spotify client with OAuth."""
    resolved_cache = os.path.expanduser(cache_path or DEFAULT_CACHE_PATH)

    auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback"),
        scope=SPOTIFY_SCOPE,  # the shared-cache union — never a per-script scope
        cache_path=resolved_cache,
        # Browser only in a real terminal: local re-auth is one click; CI never
        # reaches the interactive path (ensure_spotify_token raises first).
        open_browser=sys.stdin.isatty(),
    )
    ensure_spotify_token(auth_manager)
    return spotipy.Spotify(auth_manager=auth_manager)


class PlaylistRead(NamedTuple):
    """What the playlist read got, and whether it got ALL of it.

    `complete` is the whole point, and it is a named field rather than a bare second
    tuple element because everything downstream turns on it: the diff in sync_show is
    the only dedup that exists (Spotify accepts a track a playlist already holds, and
    nothing in this repo ever removes one), so acting on a partial read means re-adding
    real tracks and growing duplicates only a human can clean up.
    """

    track_ids: Set[str]
    complete: bool


class AddOutcome(NamedTuple):
    """What actually reached the playlist, and what did not.

    `failed_batches` is the unit `failures` is counted in — one dropped batch is one
    failed operation — while `failed_tracks` is the number a person wants in the alert
    ("100 of 250 never landed").
    """

    added: int
    failed_tracks: int
    failed_batches: int


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> PlaylistRead:
    """Get all track IDs currently in the playlist, and say whether the read finished.

    Changed 2026-09-04 (Kevin's call: a partial sync must fail loudly). This used to
    swallow a mid-pagination error and return the pages that happened to arrive, with no
    exception and no flag — so a partly-read playlist was indistinguishable from a short
    one, and the caller's diff then re-added tracks Spotify already had.
    """
    track_ids = set()
    offset = 0

    while True:
        try:
            results = sp.playlist_tracks(playlist_id, offset=offset, limit=100)
            items = results.get("items", [])
            if not items:
                break

            for item in items:
                track = item.get("track")
                if track and track.get("id"):
                    track_ids.add(track["id"])

            offset += len(items)
            if len(items) < 100:
                break
            time.sleep(0.2)
        except SpotifyException as e:
            print(f"Error fetching playlist tracks: {e}", file=sys.stderr)
            # Everything read so far is still returned — the caller decides what to do
            # with a partial picture — but it is now unmistakably marked partial.
            return PlaylistRead(track_ids, complete=False)

    return PlaylistRead(track_ids, complete=True)


def add_tracks_to_playlist(
    sp: spotipy.Spotify, playlist_id: str, track_ids: List[str]
) -> AddOutcome:
    """Add tracks to playlist in batches. Returns what landed and what was dropped.

    A batch is dropped when a non-429 error comes back, or when 429s outlast
    MAX_RETRIES. Every OTHER batch is still attempted — losing the good work because one
    batch failed would be a worse outcome than the partial sync — but the drop is now
    counted and returned instead of being printed to a log nobody reads on a green run.
    """
    added = 0
    failed_tracks = 0
    failed_batches = 0

    for i in range(0, len(track_ids), BATCH_SIZE):
        batch = track_ids[i:i + BATCH_SIZE]
        uris = [f"spotify:track:{tid}" for tid in batch]
        landed = False

        for attempt in range(MAX_RETRIES):
            try:
                sp.playlist_add_items(playlist_id, uris)
                added += len(batch)
                landed = True
                print(f"  Added batch {i // BATCH_SIZE + 1}: {len(batch)} tracks (total: {added})")
                time.sleep(API_DELAY)
                break
            except SpotifyException as e:
                if e.http_status == 429:
                    retry_after = int(e.headers.get("Retry-After", 5)) + 1
                    print(f"  Rate limited. Waiting {retry_after}s...", file=sys.stderr)
                    time.sleep(retry_after)
                else:
                    print(f"  Error adding tracks: {e}", file=sys.stderr)
                    break

        # Covers BOTH ways a batch is lost: the non-429 break above, and a 429 that
        # simply ran out of attempts (which exits the loop without ever succeeding, and
        # for years reported nothing at all).
        if not landed:
            failed_batches += 1
            failed_tracks += len(batch)
            print(
                f"  ✗ Batch {i // BATCH_SIZE + 1} DROPPED: {len(batch)} track(s) never "
                f"reached the playlist",
                file=sys.stderr,
            )

    return AddOutcome(added, failed_tracks, failed_batches)


# =============================================================================
# Database
# =============================================================================

def get_db_connection():
    """Connect to Neon database (delegates to common.get_db_connection)."""
    # One implementation for the scheduled path — pipeline/common.py carries the connect
    # timeout, keepalives, and bounded retry. This module's private copy had none, and it
    # sits on pipeline.yml's Mon/Wed/Fri chain (rewired 2026-09-01 after the 08-31 41-minute
    # hang). Lazy import so this file still runs as a script from its own directory.
    pipeline_dir = str(Path(__file__).resolve().parent)
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from common import get_db_connection as shared_connection

    return shared_connection()


def get_matched_track_ids(show_id: int) -> List[str]:
    """Query all matched track IDs for a show."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT spotify_track_id
                FROM songs s
                JOIN episodes e ON s.episode_id = e.id
                WHERE e.show_id = %s
                  AND spotify_track_id IS NOT NULL
                  AND spotify_match_confidence IN ('HIGH', 'MEDIUM', 'MANUAL')
                ORDER BY spotify_track_id
            """, (show_id,))
            return [row["spotify_track_id"] for row in cur.fetchall()]
    finally:
        conn.close()


def get_latest_episode(show_id: int) -> dict:
    """Get the most recent scraped episode for a show."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT title, episode_number, publish_date
                FROM episodes
                WHERE show_id = %s AND scraped_at IS NOT NULL
                ORDER BY publish_date DESC
                LIMIT 1
            """, (show_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_playlist_stats(show_id: int) -> dict:
    """Get song and episode counts for a show."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Count matched songs
            cur.execute("""
                SELECT COUNT(DISTINCT spotify_track_id) as songs
                FROM songs s
                JOIN episodes e ON s.episode_id = e.id
                WHERE e.show_id = %s
                  AND spotify_track_id IS NOT NULL
                  AND spotify_match_confidence IN ('HIGH', 'MEDIUM', 'MANUAL')
            """, (show_id,))
            songs = cur.fetchone()["songs"]

            # Count scraped episodes
            cur.execute("""
                SELECT COUNT(*) as episodes
                FROM episodes
                WHERE show_id = %s AND scraped_at IS NOT NULL
            """, (show_id,))
            episodes = cur.fetchone()["episodes"]

            return {"songs": songs, "episodes": episodes}
    finally:
        conn.close()


def update_playlist_description(sp: spotipy.Spotify, playlist_id: str, show_id: int):
    """Update playlist description with stats and date."""
    from datetime import datetime

    stats = get_playlist_stats(show_id)
    date_str = datetime.now().strftime("%m/%y")
    acronym = SHOWS[show_id]["acronym"]

    desc = DESCRIPTION_TEMPLATE.format(
        songs=stats['songs'],
        episodes=stats['episodes'],
        acronym=acronym,
        date=date_str
    )

    try:
        sp.playlist_change_details(playlist_id, description=desc)
        print(f"  Updated description: {stats['songs']:,} songs across {stats['episodes']} episodes")
    except SpotifyException as e:
        print(f"  Warning: Could not update description: {e}", file=sys.stderr)


# =============================================================================
# Main
# =============================================================================

def sync_show(
    show_id: int,
    dry_run: bool = False,
    cache_path: str = None,
) -> dict:
    """
    Sync matched songs to a Spotify playlist for a show.

    Returns dict with stats: {db_tracks, existing_tracks, new_tracks, added,
    failed_tracks, failures}. `failures` counts FAILED OPERATIONS — one per dropped
    batch, plus one for a truncated playlist read — and is the key run_pipeline reads to
    fail the run (record_step_failures). A run that adds 150 of 250 tracks is no longer
    a success (Kevin's call, 2026-09-04).

    Callable from orchestrator or CLI.
    """
    if show_id not in SHOWS:
        raise ValueError(f"Unknown show ID {show_id}. Valid: {list(SHOWS.keys())}")

    show = SHOWS[show_id]
    playlist_id = show["playlist_id"]
    print(f"Syncing '{show['name']}' to playlist {playlist_id}")

    # Get matched tracks from database
    print("Querying matched tracks from database...")
    db_tracks = get_matched_track_ids(show_id)
    print(f"  Found {len(db_tracks)} unique matched tracks")

    stats = {
        "db_tracks": len(db_tracks),
        "existing_tracks": 0,
        "new_tracks": 0,
        "added": 0,
        "failed_tracks": 0,
        STEP_FAILURE_KEY: 0,
    }

    if not db_tracks:
        print("No tracks to sync.")
        return stats

    # Get current playlist tracks
    print("Fetching current playlist tracks...")
    sp = get_spotify_client(cache_path)
    playlist = get_playlist_tracks(sp, playlist_id)
    existing_tracks = playlist.track_ids
    print(f"  Playlist has {len(existing_tracks)} tracks")
    stats["existing_tracks"] = len(existing_tracks)

    if not playlist.complete:
        # STOP, before any add and before the description update. The diff below is the
        # only dedup there is, so acting on a partial read means re-adding tracks the
        # playlist already holds — and nothing here ever removes one, so a human has to
        # undo it. Skipping this show's sync for one run costs nothing that the next run
        # does not fix; a wrong picture of the playlist costs a manual cleanup.
        stats[STEP_FAILURE_KEY] = 1
        stats["error"] = "playlist read was truncated — refusing to sync on a partial diff"
        print(
            f"\n  ✗ Playlist read was TRUNCATED ({len(existing_tracks)} tracks read "
            f"before the error). Refusing to add anything on a partial diff — a wrong "
            f"picture of the playlist would re-add tracks it already holds.",
            file=sys.stderr,
        )
        return stats

    # Find tracks to add
    new_tracks = [t for t in db_tracks if t not in existing_tracks]
    print(f"  {len(new_tracks)} new tracks to add")
    stats["new_tracks"] = len(new_tracks)

    if not new_tracks:
        print("Playlist is already up to date!")
        # Still update description (date changes)
        if not dry_run:
            update_playlist_description(sp, playlist_id, show_id)
        return stats

    if dry_run:
        print(f"\nDry run - would add {len(new_tracks)} tracks")
        return stats

    # Add new tracks
    print(f"\nAdding {len(new_tracks)} tracks...")
    outcome = add_tracks_to_playlist(sp, playlist_id, new_tracks)
    print(f"\nDone! Added {outcome.added} tracks to playlist.")
    stats["added"] = outcome.added
    stats["failed_tracks"] = outcome.failed_tracks
    stats[STEP_FAILURE_KEY] = outcome.failed_batches

    if outcome.failed_batches:
        # The description reports how many songs the playlist holds. After a dropped
        # batch it does not hold them, so publishing the new description would put a
        # number on the playlist that is simply untrue — and it is the one part of this
        # sync a listener actually reads. Leave the previous description standing; the
        # next successful run writes an accurate one.
        stats["error"] = (
            f"{outcome.failed_tracks} of {len(new_tracks)} track(s) never reached the "
            f"playlist ({outcome.failed_batches} batch(es) dropped)"
        )
        print(
            f"\n  ✗ {stats['error']} — leaving the description alone rather than "
            f"publishing a count the playlist does not hold.",
            file=sys.stderr,
        )
        return stats

    # Update playlist description with latest episode
    update_playlist_description(sp, playlist_id, show_id)

    return stats


def main():
    """CLI entry point."""
    # Load env vars from multiple sources
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    # 1. Spotify credentials from spotify-bulk-actions-mcp
    spotify_env = os.path.expanduser("~/DevKev/personal/spotify-bulk-actions-mcp/.env")
    load_dotenv(spotify_env)

    # 2. Project-specific vars (DATABASE_URL) from project root
    load_dotenv(os.path.join(project_root, ".env.local"))

    parser = argparse.ArgumentParser(description="Sync matched songs to Spotify playlist")
    parser.add_argument("--show-id", type=int, required=True, help="Show ID (1=SOP, 2=TAL)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't add tracks")
    args = parser.parse_args()

    try:
        stats = sync_show(show_id=args.show_id, dry_run=args.dry_run)
    except ValueError as e:
        # The only ValueError raised in this file is sync_show's unknown --show-id,
        # which the next attempt reproduces exactly — so exit 2 = deterministic.
        # Two orchestrators call this module, and only one of them ever sees that code:
        #   * run_new_episodes.step_spotify_sync shells out to this script, and
        #     run_script skips the retry on DETERMINISTIC_EXIT_CODE. That is this line.
        #   * The live SOP/TAL cron does NOT come through here: pipeline.yml runs
        #     run_pipeline.py, whose run_sync() imports sync_show and calls it in
        #     process, so the ValueError is caught by its bare `except Exception`,
        #     recorded as a failed step, and the run exits 1.
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    if stats.get(STEP_FAILURE_KEY):
        # The OTHER orchestrator's path. run_new_episodes.step_spotify_sync shells out
        # to this script and reads only the exit code, so without this a dropped batch
        # would be exactly the silence this change exists to end — just relocated.
        #
        # Exit 1, deliberately NOT 2: a truncated read or a dropped batch is transient
        # (a Spotify blip, a rate limit that outlasted its retries) and the next run
        # adds what this one missed, so run_script SHOULD retry it. Exit 2 stays
        # reserved for the unknown --show-id above, which every attempt reproduces
        # identically — that is what DETERMINISTIC_EXIT_CODE means.
        print(f"Error: {stats.get('error', 'playlist sync was incomplete')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
