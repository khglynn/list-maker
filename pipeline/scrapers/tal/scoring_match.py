#!/usr/bin/env python3
"""
Spotify Matching for Scoring Tracks

Matches scoring_tracks to Spotify and optionally adds to playlist.

Usage:
    python scoring_match.py --show-id 2 --dry-run    # Preview matches
    python scoring_match.py --show-id 2 --execute    # Match and update DB
    python scoring_match.py --show-id 2 --sync       # Also add to playlist
"""
from __future__ import annotations  # PEP-604 (dict | None) annotations under Python 3.9

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common import SPOTIFY_SCOPE, ensure_spotify_token  # noqa: E402


# Playlist IDs by show
PLAYLISTS = {
    2: "3d7fjfrTTKvrl7VHv5JzIz",  # TAL
}


def get_db_connection():
    """Connect to Neon database."""
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=RealDictCursor,
    )


def get_spotify_client():
    """Initialize Spotify client."""
    cache_path = os.path.expanduser(
        "~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/.cache"
    )
    auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback"),
        scope=SPOTIFY_SCOPE,  # the shared-cache union — never a per-script scope
        cache_path=cache_path,
        # Browser only in a real terminal: local re-auth is one click; CI never
        # reaches the interactive path (ensure_spotify_token raises first).
        open_browser=sys.stdin.isatty(),
    )
    ensure_spotify_token(auth_manager)
    return spotipy.Spotify(auth_manager=auth_manager)


def get_unmatched_tracks(conn, show_id: int) -> list[dict]:
    """Get scoring tracks without Spotify matches."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, artist, album, title
            FROM scoring_tracks
            WHERE show_id = %s AND spotify_track_id IS NULL
            ORDER BY artist, track_number
        """, (show_id,))
        return cur.fetchall()


def search_spotify(sp, title: str, artist: str) -> dict | None:
    """Search Spotify for a track. Returns match info or None."""
    # Try exact search first
    query = f"track:{title} artist:{artist}"
    try:
        results = sp.search(q=query, type="track", limit=5)
        tracks = results.get("tracks", {}).get("items", [])

        if tracks:
            track = tracks[0]
            # Calculate simple confidence based on name match
            track_name = track["name"].lower()
            search_title = title.lower()

            if search_title in track_name or track_name in search_title:
                confidence = "HIGH"
            else:
                confidence = "MEDIUM"

            return {
                "id": track["id"],
                "name": track["name"],
                "artists": [a["name"] for a in track["artists"]],
                "album": track["album"]["name"],
                "confidence": confidence,
            }

        # Try fuzzy search (title only)
        results = sp.search(q=title, type="track", limit=5)
        tracks = results.get("tracks", {}).get("items", [])

        for track in tracks:
            # Check if artist matches loosely
            track_artists = " ".join(a["name"].lower() for a in track["artists"])
            if artist.lower().split()[0] in track_artists:
                return {
                    "id": track["id"],
                    "name": track["name"],
                    "artists": [a["name"] for a in track["artists"]],
                    "album": track["album"]["name"],
                    "confidence": "LOW",
                }

        return None

    except Exception as e:
        print(f"  Search error: {e}")
        return None


def update_track_match(conn, track_id: int, spotify_id: str, confidence: str):
    """Update scoring_track with Spotify match."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE scoring_tracks
            SET spotify_track_id = %s, spotify_match_confidence = %s
            WHERE id = %s
        """, (spotify_id, confidence, track_id))
    conn.commit()


def mark_not_found(conn, track_id: int):
    """Mark track as not found on Spotify."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE scoring_tracks
            SET spotify_match_confidence = 'NOT_FOUND'
            WHERE id = %s
        """, (track_id,))
    conn.commit()


def add_to_playlist(sp, playlist_id: str, track_ids: list[str]):
    """Add tracks to playlist."""
    # Get existing tracks to avoid duplicates
    existing = set()
    offset = 0
    while True:
        results = sp.playlist_tracks(playlist_id, offset=offset, limit=100)
        items = results.get("items", [])
        if not items:
            break
        for item in items:
            if item.get("track") and item["track"].get("id"):
                existing.add(item["track"]["id"])
        offset += len(items)
        if len(items) < 100:
            break

    # Filter to new tracks only
    new_tracks = [t for t in track_ids if t not in existing]

    if not new_tracks:
        print("All tracks already in playlist")
        return 0

    # Add in batches of 100
    added = 0
    for i in range(0, len(new_tracks), 100):
        batch = new_tracks[i:i+100]
        uris = [f"spotify:track:{t}" for t in batch]
        sp.playlist_add_items(playlist_id, uris)
        added += len(batch)
        print(f"  Added batch: {len(batch)} tracks (total: {added})")
        time.sleep(0.5)

    return added


def main():
    parser = argparse.ArgumentParser(description="Match scoring tracks to Spotify")
    parser.add_argument("--show-id", type=int, required=True, help="Show ID (2=TAL)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--execute", action="store_true", help="Update database")
    parser.add_argument("--sync", action="store_true", help="Also add to playlist")
    args = parser.parse_args()

    # Load env
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    load_dotenv(os.path.expanduser("~/DevKev/personal/spotify-bulk-actions-mcp/.env"))
    load_dotenv(project_root / ".env.local")

    conn = get_db_connection()
    sp = get_spotify_client()

    try:
        # Get unmatched tracks
        tracks = get_unmatched_tracks(conn, args.show_id)
        print(f"Found {len(tracks)} unmatched scoring tracks")

        if not tracks:
            print("Nothing to match!")
            return

        matched = []
        not_found = []

        for i, track in enumerate(tracks, 1):
            title = track["title"]
            artist = track["artist"]
            print(f"[{i}/{len(tracks)}] {artist} - {title}...", end=" ")

            result = search_spotify(sp, title, artist)

            if result:
                print(f"FOUND ({result['confidence']}): {result['name']}")
                matched.append({
                    "db_id": track["id"],
                    "spotify_id": result["id"],
                    "confidence": result["confidence"],
                })

                if args.execute:
                    update_track_match(conn, track["id"], result["id"], result["confidence"])
            else:
                print("NOT FOUND")
                not_found.append(track)

                if args.execute:
                    mark_not_found(conn, track["id"])

            time.sleep(0.2)  # Rate limiting

        # Summary
        print(f"\n--- Summary ---")
        print(f"Matched: {len(matched)}")
        print(f"  HIGH: {sum(1 for m in matched if m['confidence'] == 'HIGH')}")
        print(f"  MEDIUM: {sum(1 for m in matched if m['confidence'] == 'MEDIUM')}")
        print(f"  LOW: {sum(1 for m in matched if m['confidence'] == 'LOW')}")
        print(f"Not found: {len(not_found)}")

        # Sync to playlist
        if args.sync and matched:
            playlist_id = PLAYLISTS.get(args.show_id)
            if playlist_id:
                print(f"\nAdding {len(matched)} tracks to playlist...")
                track_ids = [m["spotify_id"] for m in matched]
                added = add_to_playlist(sp, playlist_id, track_ids)
                print(f"Added {added} new tracks to playlist")

                # Mark as added
                if args.execute:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE scoring_tracks
                            SET added_to_playlist = true
                            WHERE spotify_track_id = ANY(%s)
                        """, ([m["spotify_id"] for m in matched],))
                    conn.commit()

    finally:
        conn.close()


if __name__ == "__main__":
    main()
