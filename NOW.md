# NOW — pod-lists

**Last updated:** 2026-05-16

## Just Completed
- Transcript catch-up through 2026-05-15 for Taddy-backed shows:
  - AI Daily Brief: 978/978 transcripts
  - Pop Culture Happy Hour: 356/356 transcripts
  - Switched On Pop: 531/531 Taddy-catalog transcripts
  - This American Life: official current-feed Taddy source added; 13 new transcript rows imported, 15/15 current-feed episodes covered
- AI Daily entity extraction fully caught up:
  - 60 recent episodes plus 141 historical gap episodes extracted
  - 4 empty mini-model extractions retried with `gpt-4.1`
  - 0 unextracted AI Daily episodes remain
- Notion sync current:
  - 1,067 Notion-eligible AI Daily entities synced
  - Final dry-run: 0 creates, 0 updates

## Next Up
- Restore/configure Spotify credentials so SOP/TAL matching and playlist sync can run locally again.
- Automate `run_new_episodes.py` or equivalent so AI Daily/PCHH/SOP/TAL transcript catch-up does not drift again.
- PCHH pipeline: movies/TV/books extraction from transcripts → Notion.
- TAL historical transcript caveat: official Taddy source only exposes the current rolling feed; full archive source is not transcribing.
