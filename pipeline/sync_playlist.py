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
from typing import List, Set

import psycopg2
from psycopg2.extras import RealDictCursor
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv

# =============================================================================
# Constants
# =============================================================================

BATCH_SIZE = 100         # Tracks per Spotify API call (max 100)
API_DELAY = 0.5          # Seconds between API calls
MAX_RETRIES = 3

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
        scope="playlist-modify-public playlist-modify-private playlist-read-private",
        cache_path=resolved_cache,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> Set[str]:
    """Get all track IDs currently in the playlist."""
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
            break

    return track_ids


def add_tracks_to_playlist(sp: spotipy.Spotify, playlist_id: str, track_ids: List[str]) -> int:
    """Add tracks to playlist in batches. Returns count added."""
    added = 0

    for i in range(0, len(track_ids), BATCH_SIZE):
        batch = track_ids[i:i + BATCH_SIZE]
        uris = [f"spotify:track:{tid}" for tid in batch]

        for attempt in range(MAX_RETRIES):
            try:
                sp.playlist_add_items(playlist_id, uris)
                added += len(batch)
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

    return added


# =============================================================================
# Database
# =============================================================================

def get_db_connection():
    """Connect to Neon database."""
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=RealDictCursor,
    )


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

    Returns dict with stats: {db_tracks, existing_tracks, new_tracks, added}
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

    stats = {"db_tracks": len(db_tracks), "existing_tracks": 0, "new_tracks": 0, "added": 0}

    if not db_tracks:
        print("No tracks to sync.")
        return stats

    # Get current playlist tracks
    print("Fetching current playlist tracks...")
    sp = get_spotify_client(cache_path)
    existing_tracks = get_playlist_tracks(sp, playlist_id)
    print(f"  Playlist has {len(existing_tracks)} tracks")
    stats["existing_tracks"] = len(existing_tracks)

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
    added = add_tracks_to_playlist(sp, playlist_id, new_tracks)
    print(f"\nDone! Added {added} tracks to playlist.")
    stats["added"] = added

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
        sync_show(show_id=args.show_id, dry_run=args.dry_run)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
