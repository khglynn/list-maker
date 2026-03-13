#!/usr/bin/env python3
"""
TAL Song Fill Script - Add missing songs to database

Compares parsed songs against database and fills in any missing ones.
Uses parameterized queries for safe handling of special characters.

Usage:
    python tal_fill_songs.py --dry-run           # Show what would be inserted
    python tal_fill_songs.py --range 901 950     # Process subset (dry-run)
    python tal_fill_songs.py --range 901 950 --execute  # Actually insert
    python tal_fill_songs.py --execute           # Process all and insert
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Import parser from parse.py (formerly tal_parse.py)
sys.path.insert(0, str(Path(__file__).parent))
from parse import parse_episode


def get_db_connection():
    """Connect to Neon database."""
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=RealDictCursor,
    )


def cleanup_existing_songs(conn) -> int:
    """Strip leading/trailing quotes from all song titles.

    Returns count of songs cleaned.
    """
    with conn.cursor() as cur:
        # Use REGEXP_REPLACE to strip all quote types (straight and curly)
        cur.execute("""
            UPDATE songs
            SET title = REGEXP_REPLACE(
                REGEXP_REPLACE(title, E'^["\u201c\u201d]+', ''),
                E'["\u201c\u201d]+$', ''
            )
            WHERE title ~ E'^["\u201c\u201d]+' OR title ~ E'["\u201c\u201d]+$'
        """)
        return cur.rowcount


def fix_has_songs_flags(conn) -> tuple[int, int]:
    """Fix has_songs_discussed flags to match actual song presence.

    Returns (count_set_to_true, count_set_to_false).
    """
    with conn.cursor() as cur:
        # Set true where songs exist but flag is false
        cur.execute("""
            UPDATE episodes e SET has_songs_discussed = true
            WHERE EXISTS (SELECT 1 FROM songs s WHERE s.episode_id = e.id)
            AND has_songs_discussed = false
        """)
        fixed_true = cur.rowcount

        # Set false where no songs exist but flag is true (and not a 404)
        cur.execute("""
            UPDATE episodes e SET has_songs_discussed = false
            WHERE NOT EXISTS (SELECT 1 FROM songs s WHERE s.episode_id = e.id)
            AND has_songs_discussed = true
            AND title IS NOT NULL
        """)
        fixed_false = cur.rowcount

        return fixed_true, fixed_false


def check_duplicates(conn) -> int:
    """Check for duplicate songs (same episode + title).

    Returns count of duplicate pairs.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) as cnt FROM (
                SELECT episode_id, title
                FROM songs
                GROUP BY episode_id, title
                HAVING COUNT(*) > 1
            ) dupes
        """)
        return cur.fetchone()['cnt']


def remove_duplicates(conn) -> int:
    """Remove duplicate songs, keeping the one with lowest ID.

    Returns count of songs deleted.
    """
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM songs a
            USING songs b
            WHERE a.id > b.id
            AND a.episode_id = b.episode_id
            AND a.title = b.title
        """)
        return cur.rowcount


def get_existing_songs(conn, db_ids: list[int]) -> dict[int, set[tuple]]:
    """Get existing songs for given episode IDs.

    Returns dict: {episode_id: {(title, artist), ...}}
    """
    if not db_ids:
        return {}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT episode_id, title, artist
            FROM songs
            WHERE episode_id = ANY(%s)
        """, (db_ids,))

        result = {}
        for row in cur.fetchall():
            ep_id = row['episode_id']
            if ep_id not in result:
                result[ep_id] = set()
            result[ep_id].add((row['title'], row['artist']))

        return result


def get_episodes_needing_flag_update(conn, db_ids: list[int]) -> list[int]:
    """Get episodes where has_songs_discussed should be true but isn't."""
    if not db_ids:
        return []

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM episodes
            WHERE id = ANY(%s)
            AND has_songs_discussed = false
        """, (db_ids,))
        return [row['id'] for row in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description="Fill missing TAL songs")
    parser.add_argument("--range", nargs=2, type=int, metavar=("START", "END"),
                        help="Process range of db_ids")
    parser.add_argument("--execute", action="store_true",
                        help="Actually insert (default is dry-run)")
    parser.add_argument("--dir", default="fetched/tal",
                        help="Directory containing JSON files")
    args = parser.parse_args()

    # Load env
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    load_dotenv(project_root / ".env.local")

    base_dir = script_dir / args.dir

    # Determine file range
    if args.range:
        start, end = args.range
        db_ids = list(range(start, end + 1))
    else:
        # All files
        db_ids = sorted(int(f.stem) for f in base_dir.glob("*.json"))

    print(f"Processing {len(db_ids)} files...")

    # Parse all files
    parsed_data = []
    for db_id in db_ids:
        filepath = base_dir / f"{db_id}.json"
        if filepath.exists():
            try:
                result = parse_episode(filepath)
                if not result.get('is_404') and result.get('songs'):
                    parsed_data.append(result)
            except Exception as e:
                print(f"  Error parsing {db_id}: {e}")

    print(f"Found {len(parsed_data)} episodes with songs")

    # Get total parsed songs
    total_parsed_songs = sum(len(ep['songs']) for ep in parsed_data)
    print(f"Total songs in parsed data: {total_parsed_songs}")

    # Connect to DB and find missing songs
    conn = get_db_connection()
    try:
        episode_ids = [ep['db_id'] for ep in parsed_data]
        existing_songs = get_existing_songs(conn, episode_ids)

        # Count existing
        total_existing = sum(len(songs) for songs in existing_songs.values())
        print(f"Songs already in database: {total_existing}")

        # Find missing songs
        missing_songs = []  # [(episode_id, title, artist), ...]
        for ep in parsed_data:
            db_id = ep['db_id']
            existing = existing_songs.get(db_id, set())

            for song in ep['songs']:
                key = (song['title'], song['artist'])
                if key not in existing:
                    missing_songs.append((db_id, song['title'], song['artist']))

        print(f"Missing songs to insert: {len(missing_songs)}")

        # Find episodes needing flag update
        eps_with_songs = [ep['db_id'] for ep in parsed_data]
        eps_needing_update = get_episodes_needing_flag_update(conn, eps_with_songs)
        print(f"Episodes needing has_songs_discussed=true: {len(eps_needing_update)}")

        if not args.execute:
            print("\n--- DRY RUN (use --execute to insert) ---")
            if missing_songs:
                print("\nSample missing songs (first 10):")
                for ep_id, title, artist in missing_songs[:10]:
                    print(f"  [{ep_id}] \"{title}\" by {artist}")
                if len(missing_songs) > 10:
                    print(f"  ... and {len(missing_songs) - 10} more")

            if eps_needing_update:
                print(f"\nEpisodes to update: {eps_needing_update[:20]}")
                if len(eps_needing_update) > 20:
                    print(f"  ... and {len(eps_needing_update) - 20} more")
            return

        # Execute changes
        print("\n--- EXECUTING ---")

        # Phase 1: Clean existing song titles
        print("\nPhase 1: Cleaning existing song titles...")
        cleaned = cleanup_existing_songs(conn)
        print(f"  Cleaned {cleaned} song titles (stripped quotes)")

        # Phase 2: Re-query existing songs (now that they're cleaned)
        existing_songs = get_existing_songs(conn, episode_ids)
        total_existing = sum(len(songs) for songs in existing_songs.values())
        print(f"  Songs in DB after cleanup: {total_existing}")

        # Re-calculate missing songs
        missing_songs = []
        for ep in parsed_data:
            db_id = ep['db_id']
            existing = existing_songs.get(db_id, set())
            for song in ep['songs']:
                key = (song['title'], song['artist'])
                if key not in existing:
                    missing_songs.append((db_id, song['title'], song['artist']))

        # Phase 3: Insert missing songs
        print(f"\nPhase 2: Inserting missing songs...")
        if missing_songs:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO songs (episode_id, title, artist) VALUES (%s, %s, %s)",
                    missing_songs
                )
            print(f"  Inserted {len(missing_songs)} songs")
        else:
            print("  No missing songs to insert")

        # Phase 4: Fix has_songs_discussed flags
        print(f"\nPhase 3: Fixing has_songs_discussed flags...")
        fixed_true, fixed_false = fix_has_songs_flags(conn)
        print(f"  Set {fixed_true} episodes to has_songs=true")
        print(f"  Set {fixed_false} episodes to has_songs=false")

        # Phase 5: Check for and remove duplicates
        print(f"\nPhase 4: Checking for duplicates...")
        dupes = check_duplicates(conn)
        if dupes > 0:
            print(f"  Found {dupes} duplicate song pairs, removing...")
            removed = remove_duplicates(conn)
            print(f"  Removed {removed} duplicate songs")
        else:
            print("  No duplicates found")

        conn.commit()
        print("\n✅ Done! All changes committed.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
