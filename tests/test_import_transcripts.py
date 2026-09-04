"""Taddy importer dedup key (Workstream C — Hard Fork fix).

Some shows (Hard Fork) return a generic show-level websiteUrl for every episode.
Deduping on that url collapses all episodes onto one row, so the dedup key must
prefer the always-unique Taddy episode uuid.
"""

import argparse

import pytest

from pipeline.scrapers.taddy.import_transcripts import episode_url_key, run


def test_episode_url_key_prefers_unique_uuid_over_generic_website_url() -> None:
    # Two different episodes sharing a generic show-level websiteUrl (the Hard Fork
    # bug) must get DISTINCT keys so they don't collapse onto one row.
    generic = "https://www.nytimes.com/column/hard-fork"
    a = {"uuid": "uuid-aaa", "websiteUrl": generic}
    b = {"uuid": "uuid-bbb", "websiteUrl": generic}

    assert episode_url_key(a) != episode_url_key(b)
    assert "uuid-aaa" in episode_url_key(a)


def test_episode_url_key_falls_back_without_uuid() -> None:
    assert episode_url_key({"websiteUrl": "https://x.com/ep1"}) == "https://x.com/ep1"
    assert episode_url_key({"audioUrl": "https://x.com/a.mp3"}) == "https://x.com/a.mp3"
    assert episode_url_key({"guid": "guid-1"}) == "guid-1"
    # Past that chain the key is scoped to the show and the episode, not one shared
    # literal — see the collision test below for why.
    assert episode_url_key({}, show_id=3).startswith("taddy-unidentified:3:")


def test_episode_url_key_scopes_unidentified_fallback_by_show_and_episode() -> None:
    """The old fallback was the literal "unknown-episode" for EVERY malformed episode of
    EVERY show. episodes.url is UNIQUE, so the second such episode to arrive silently
    collapsed onto the first one's row — data loss with no error. The replacement has to
    be unique per episode AND stable across re-imports, or it trades data loss for
    duplicate rows on every run."""
    a = {"name": "Episode A", "datePublished": 1788105600}
    b = {"name": "Episode B", "datePublished": 1788105600}

    # Different episodes in one show no longer collide.
    assert episode_url_key(a, 3) != episode_url_key(b, 3)
    # The same episode in different shows no longer collides (the cross-show bug).
    assert episode_url_key(a, 3) != episode_url_key(a, 11)
    # Idempotent: a re-import finds and updates the same row instead of inserting one.
    assert episode_url_key(a, 3) == episode_url_key(a, 3)
    # Nothing at all is still deterministic rather than a crash.
    assert episode_url_key({}, 3) == episode_url_key({}, 3)
    assert episode_url_key({}) == "taddy-unidentified:no-show:no-date:untitled"


def test_episode_url_key_fallback_survives_a_taddy_redate_within_the_day() -> None:
    """The key uses the DATE, not the raw timestamp. Surviving a Taddy re-date is the
    point of the identity work this sits beside; a key built on the raw epoch would fork
    on a re-date and insert a duplicate row on the next import instead of updating."""
    noon = {"name": "Ep", "datePublished": 1788105600}
    an_hour_later = {"name": "Ep", "datePublished": 1788105600 + 3600}
    as_a_string = {"name": "Ep", "datePublished": "1788105600"}

    assert episode_url_key(noon, 3) == episode_url_key(an_hour_later, 3)
    assert episode_url_key(noon, 3) == episode_url_key(as_a_string, 3)  # int/str payload
    assert episode_url_key(noon, 3).endswith(":2026-08-30:ep")  # the stored publish_date


def test_find_existing_episode_id_uses_title_date_fallback_for_old_url_rows() -> None:
    # Migration safety net (the real risk of changing the dedup key): an episode
    # already stored under its OLD websiteUrl url won't match the new uuid url-lookup,
    # but the show_id+title+date fallback must still find it — so the key change does
    # NOT re-import existing episodes. (The dry-run confirmed this on 980+532 rows.)
    from pipeline.scrapers.taddy.import_transcripts import find_existing_episode_id

    class _Cur:
        def __init__(self) -> None:
            self.fetches = 0

        def __enter__(self) -> "_Cur":
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def execute(self, sql: str, params=()) -> None:
            self._sql = sql

        def fetchone(self):
            self.fetches += 1
            # 1st query = uuid url lookup → miss; 2nd = title+date fallback → hit.
            return None if self.fetches == 1 else {"id": 4242}

    class _Conn:
        def __init__(self) -> None:
            self._cur = _Cur()

        def cursor(self) -> "_Cur":
            return self._cur

    episode = {
        "uuid": "u-new",
        "websiteUrl": "https://www.nytimes.com/column/hard-fork",  # generic
        "name": "Some Existing Episode",
        "datePublished": 1696000000,  # valid epoch → real publish_date
    }
    assert find_existing_episode_id(_Conn(), show_id=3, episode=episode) == 4242


# --- deterministic refusals (exit 2) ---
# Both checks run before any Taddy or DB call and reproduce identically on a retry, so
# they exit 2 and run_new_episodes.run_script fails the step instead of burning two
# retries and 15s of backoff on a config problem. Raised inline, not via
# `except RuntimeError`, because this file also raises RuntimeError for Taddy GraphQL
# errors and exhausted retries — the transient case the retry exists for.


def _taddy_args(**overrides) -> argparse.Namespace:
    base = {"shows": "ai-daily-brief", "dry_run": True}
    base.update(overrides)
    return argparse.Namespace(**base)


def test_run_exits_deterministically_without_taddy_credentials(monkeypatch) -> None:
    monkeypatch.setenv("TADDY_USER_ID", "")
    monkeypatch.setenv("TADDY_API_KEY", "")
    with pytest.raises(SystemExit) as exc:
        run(_taddy_args())
    assert exc.value.code == 2


def test_run_exits_deterministically_on_unknown_show_slug(monkeypatch) -> None:
    monkeypatch.setenv("TADDY_USER_ID", "u")
    monkeypatch.setenv("TADDY_API_KEY", "k")
    with pytest.raises(SystemExit) as exc:
        run(_taddy_args(shows="not-a-real-show"))
    assert exc.value.code == 2
