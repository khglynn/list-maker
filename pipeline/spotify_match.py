#!/usr/bin/env python3
"""
Spotify Song Matching Script for list-maker project.

Queries unmatched songs from Neon, searches Spotify, scores matches,
and writes results back to the database.

Usage:
    python spotify_match.py --show-id 1 --limit 100
    python spotify_match.py --show-id 1 --limit 10 --dry-run
    python spotify_match.py --show-id 1  # All unmatched songs
"""

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

import psycopg2
from psycopg2.extras import RealDictCursor
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from thefuzz import fuzz
from dotenv import load_dotenv

# =============================================================================
# Constants
# =============================================================================

BATCH_SIZE = 50          # Songs per DB query
API_DELAY = 0.3          # Seconds between Spotify API calls
MAX_RETRIES = 3          # For rate limit handling
LOG_FILE = "match_progress.log"

# Confidence thresholds (same as MCP)
HIGH_THRESHOLD = 0.90
MEDIUM_THRESHOLD = 0.70

# =============================================================================
# Spotify Client
# =============================================================================

DEFAULT_CACHE_PATH = "~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/.cache"


def get_spotify_client(cache_path: Optional[str] = None) -> spotipy.Spotify:
    """Initialize Spotify client with OAuth."""
    resolved_cache = os.path.expanduser(cache_path or DEFAULT_CACHE_PATH)

    auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback"),
        scope="user-library-read",
        cache_path=resolved_cache,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def search_with_retry(sp: spotipy.Spotify, query: str, retries: int = MAX_RETRIES) -> Optional[Dict]:
    """Search Spotify with rate limit handling."""
    for attempt in range(retries):
        try:
            result = sp.search(q=query, type="track", limit=3)
            return result
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", 5)) + 1
                print(f"  Rate limited. Waiting {retry_after}s...", file=sys.stderr)
                time.sleep(retry_after)
            else:
                print(f"  Spotify error: {e}", file=sys.stderr)
                return None
        except Exception as e:
            print(f"  Network error: {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(2)
            else:
                return None
    return None

# =============================================================================
# Confidence Scoring (same algorithm as MCP)
# =============================================================================

def calculate_match_confidence(
    query_title: str,
    query_artist: str,
    result_title: str,
    result_artists: List[str],
) -> float:
    """
    Calculate confidence score for a track match.

    Uses thefuzz for string similarity.
    Title weight: 55%, Artist weight: 45%

    Returns: Float between 0 and 1
    """
    # Title similarity
    title_score = fuzz.ratio(query_title.lower(), result_title.lower()) / 100

    # Artist similarity (check against all artists on the track)
    artist_scores = [
        fuzz.ratio(query_artist.lower(), artist.lower()) / 100
        for artist in result_artists
    ]
    artist_score = max(artist_scores) if artist_scores else 0

    # Weighted average
    confidence = (title_score * 0.55) + (artist_score * 0.45)

    return round(confidence, 3)


def get_confidence_category(confidence: float) -> str:
    """Categorize confidence score."""
    if confidence >= HIGH_THRESHOLD:
        return "HIGH"
    elif confidence >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    else:
        return "LOW"

# =============================================================================
# Database Operations
# =============================================================================

def get_db_connection() -> psycopg2.extensions.connection:
    """Get Neon database connection."""
    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) not set in environment")

    return psycopg2.connect(db_url)


def fetch_unmatched_songs(
    conn: psycopg2.extensions.connection,
    show_id: Optional[int],
    limit: int,
) -> List[Dict]:
    """Query songs that haven't been matched yet."""
    query = """
        SELECT s.id, s.title, s.artist
        FROM songs s
        JOIN episodes e ON s.episode_id = e.id
        WHERE s.spotify_track_id IS NULL
          AND s.spotify_match_confidence IS NULL
    """
    params = []

    if show_id:
        query += " AND e.show_id = %s"
        params.append(show_id)

    query += " ORDER BY s.id LIMIT %s"
    params.append(limit)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def save_results(
    conn: psycopg2.extensions.connection,
    matched: List[Dict],
    not_found: List[int],
) -> None:
    """Save match results to database."""
    with conn.cursor() as cur:
        # Update matched songs
        for match in matched:
            cur.execute("""
                UPDATE songs SET
                    spotify_track_id = %s,
                    spotify_match_confidence = %s,
                    album = %s,
                    spotify_web_url = %s,
                    spotify_popularity = %s,
                    spotify_title = %s,
                    spotify_artist = %s
                WHERE id = %s
            """, (
                match["track_id"],
                match["confidence_category"],
                match["album"],
                match["web_url"],
                match["popularity"],
                match["spotify_title"],
                match["spotify_artist"],
                match["song_id"],
            ))

        # Mark NOT_FOUND songs
        for song_id in not_found:
            cur.execute("""
                UPDATE songs SET spotify_match_confidence = 'NOT_FOUND'
                WHERE id = %s
            """, (song_id,))

    conn.commit()

# =============================================================================
# Matching Logic
# =============================================================================

def search_and_score(
    sp: spotipy.Spotify,
    title: str,
    artist: str,
) -> Optional[Dict]:
    """Search Spotify and return best match with score."""
    query = f'track:"{title}" artist:"{artist}"'
    result = search_with_retry(sp, query)

    if not result or not result["tracks"]["items"]:
        return None

    # Score all results and pick the best
    best_match = None
    best_confidence = 0

    for track in result["tracks"]["items"]:
        artists = [a["name"] for a in track["artists"]]
        confidence = calculate_match_confidence(title, artist, track["name"], artists)

        if confidence > best_confidence:
            best_confidence = confidence
            best_match = {
                "track_id": track["id"],
                "confidence": confidence,
                "confidence_category": get_confidence_category(confidence),
                "album": track["album"]["name"],
                "web_url": f"https://open.spotify.com/track/{track['id']}",
                "popularity": track.get("popularity", 0),
                "spotify_title": track["name"],
                "spotify_artist": ", ".join(artists),
            }

    return best_match


def match_songs_batch(
    sp: spotipy.Spotify,
    songs: List[Dict],
) -> Dict[str, Any]:
    """Search Spotify for each song, return categorized results."""
    results = {
        "high": [],
        "medium": [],
        "low": [],
        "not_found": [],
    }

    for i, song in enumerate(songs):
        title = song["title"] or ""
        artist = song["artist"] or ""

        if not title:
            results["not_found"].append(song["id"])
            print(f"  [{i+1}/{len(songs)}] SKIP: Empty title (id={song['id']})")
            continue

        print(f"  [{i+1}/{len(songs)}] Searching: {title[:40]} - {artist[:20]}...", end="")

        try:
            match = search_and_score(sp, title, artist)

            if match:
                match["song_id"] = song["id"]
                category = match["confidence_category"].lower()
                results[category].append(match)
                print(f" {match['confidence_category']} ({match['confidence']:.0%})")
            else:
                results["not_found"].append(song["id"])
                print(" NOT_FOUND")

        except Exception as e:
            print(f" ERROR: {e}")
            results["not_found"].append(song["id"])

        time.sleep(API_DELAY)

    return results

# =============================================================================
# Logging
# =============================================================================

def log_progress(batch_num: int, song_range: str, results: Dict[str, Any]) -> None:
    """Append progress to log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    high = len(results["high"])
    med = len(results["medium"])
    low = len(results["low"])
    nf = len(results["not_found"])

    log_line = f"{timestamp} | Batch {batch_num} | Songs {song_range} | HIGH:{high} MED:{med} LOW:{low} NF:{nf}\n"

    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    with open(log_path, "a") as f:
        f.write(log_line)


def print_summary(total_results: Dict[str, int]) -> None:
    """Print final summary."""
    total = sum(total_results.values())

    print("\n" + "=" * 50)
    print("COMPLETE")
    print("=" * 50)
    print(f"Total processed: {total}")

    for category in ["high", "medium", "low", "not_found"]:
        count = total_results[category]
        pct = (count / total * 100) if total > 0 else 0
        label = category.upper().replace("_", " ")
        print(f"  {label}: {count} ({pct:.0f}%)")

# =============================================================================
# Main
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Match songs to Spotify and update database"
    )
    parser.add_argument(
        "--show-id",
        type=int,
        help="Filter by show ID (1=SOP, 2=TAL). Omit for all shows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum songs to process. Omit for all unmatched.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search and display results without writing to database.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (for automated runs).",
    )
    return parser.parse_args()


def match_songs_for_show(
    show_id: Optional[int] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    yes: bool = False,
    cache_path: Optional[str] = None,
) -> Dict[str, int]:
    """
    Run the full matching pipeline for a show.

    Returns dict with counts: {high, medium, low, not_found}
    Callable from orchestrator or CLI.
    """
    print("=" * 50)
    print("Spotify Song Matching Script")
    print("=" * 50)

    # Initialize clients
    print("\nInitializing...")
    try:
        sp = get_spotify_client(cache_path)
        conn = get_db_connection()
        print("  Spotify: Connected")
        print("  Neon: Connected")
    except Exception as e:
        print(f"\nCRITICAL: Failed to initialize: {e}")
        raise

    # Count unmatched songs
    count_query = """
        SELECT COUNT(*) FROM songs s
        JOIN episodes e ON s.episode_id = e.id
        WHERE s.spotify_track_id IS NULL
          AND s.spotify_match_confidence IS NULL
    """
    params = []
    if show_id:
        count_query += " AND e.show_id = %s"
        params.append(show_id)

    with conn.cursor() as cur:
        cur.execute(count_query, params)
        total_unmatched = cur.fetchone()[0]

    # Calculate batches
    effective_limit = limit or total_unmatched
    to_process = min(effective_limit, total_unmatched)
    num_batches = (to_process + BATCH_SIZE - 1) // BATCH_SIZE

    print(f"\nFound {total_unmatched} unmatched songs", end="")
    if show_id:
        print(f" (show_id={show_id})")
    else:
        print(" (all shows)")

    print(f"Will process {to_process} songs in {num_batches} batches of {BATCH_SIZE}")

    if dry_run:
        print("\n*** DRY RUN MODE - No database writes ***")

    # Confirmation prompt (skip with --yes)
    if not yes:
        print("\nPress Enter to continue or Ctrl+C to abort...")
        try:
            input()
        except KeyboardInterrupt:
            print("\nAborted.")
            return {"high": 0, "medium": 0, "low": 0, "not_found": 0}

    # Main processing loop
    total_results = {"high": 0, "medium": 0, "low": 0, "not_found": 0}
    processed = 0
    batch_num = 0

    try:
        while processed < to_process:
            batch_num += 1
            remaining = to_process - processed
            batch_limit = min(BATCH_SIZE, remaining)

            # Fetch songs
            songs = fetch_unmatched_songs(conn, show_id, batch_limit)
            if not songs:
                print("\nNo more unmatched songs found.")
                break

            song_ids = [s["id"] for s in songs]
            song_range = f"{min(song_ids)}-{max(song_ids)}"

            print(f"\n--- Batch {batch_num} (songs {song_range}) ---")

            # Match songs
            results = match_songs_batch(sp, songs)

            # Save results (unless dry run)
            if not dry_run:
                matched = results["high"] + results["medium"] + results["low"]
                save_results(conn, matched, results["not_found"])
                log_progress(batch_num, song_range, results)

            # Update totals
            for category in total_results:
                total_results[category] += len(results[category])

            processed += len(songs)

            # Batch summary
            print(f"\nBatch {batch_num} complete:")
            print(f"  HIGH: {len(results['high'])}, MEDIUM: {len(results['medium'])}, "
                  f"LOW: {len(results['low'])}, NOT_FOUND: {len(results['not_found'])}")
            print(f"  Total processed: {processed}/{to_process}")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Progress has been saved.")
    except Exception as e:
        print(f"\n\nCRITICAL ERROR: {e}")
        print("Progress up to last batch has been saved.")
        raise
    finally:
        conn.close()

    # Final summary
    print_summary(total_results)

    if dry_run:
        print("\n*** DRY RUN - No changes were made to the database ***")

    return total_results


def main():
    """CLI entry point."""
    args = parse_args()

    # Load environment from multiple sources
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    # 1. Spotify credentials from spotify-bulk-actions-mcp
    spotify_env = os.path.expanduser("~/DevKev/personal/spotify-bulk-actions-mcp/.env")
    load_dotenv(spotify_env)

    # 2. Project-specific vars (DATABASE_URL) from project root
    load_dotenv(os.path.join(project_root, ".env.local"))

    try:
        match_songs_for_show(
            show_id=args.show_id,
            limit=args.limit,
            dry_run=args.dry_run,
            yes=args.yes,
        )
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
