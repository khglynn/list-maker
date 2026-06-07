"""Taddy importer dedup key (Workstream C — Hard Fork fix).

Some shows (Hard Fork) return a generic show-level websiteUrl for every episode.
Deduping on that url collapses all episodes onto one row, so the dedup key must
prefer the always-unique Taddy episode uuid.
"""

from pipeline.scrapers.taddy.import_transcripts import episode_url_key


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
    assert episode_url_key({}) == "unknown-episode"


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
