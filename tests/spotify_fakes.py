"""One fake for the Spotify boundary, shared by every test that touches it.

`pipeline/spotify_match.py` and `pipeline/sync_playlist.py` between them call exactly
four spotipy methods — `search`, `playlist_tracks`, `playlist_add_items`,
`playlist_change_details`. Two ad-hoc mocks would drift apart and each would encode its
own guess at the payload shape; one fake keeps that shape honest in both places. Written
for `tests/test_spotify_match.py` and imported by `tests/test_sync_playlist.py`, so the
interface below is a contract — grow it additively, don't reshape it.

The interface
-------------
    FakeSpotify(search_results=[...], playlist_pages=[...], errors={...})

    search(q=, type=, limit=, ...)                      -> a search payload
    playlist_tracks(playlist_id, offset=, limit=, ...)  -> one page of items
    playlist_add_items(playlist_id, items, ...)         -> {"snapshot_id": ...}
    playlist_change_details(playlist_id, description=)  -> {"snapshot_id": ...}

    .calls          every call in order, as Call(method, params). `params` binds EVERY
                    argument by name, so a test asserts on names whether production
                    passed it positionally (`playlist_add_items(pid, uris)`) or by
                    keyword (`search(q=..., type=..., limit=...)`).
    .calls_to(m)    just the calls to one method.

Canned responses are queues, consumed in call order:

  * When a queue runs dry the fake returns an EMPTY payload of the right shape rather
    than raising — a pagination loop then terminates instead of hanging the suite.
    Assert on `.calls` to pin how many pages were actually requested.
  * The fake does NOT slice by `offset`: it hands back the next canned page and records
    the offset the caller asked for. Paging is the behaviour under test; a fake that
    implemented paging would be testing itself.

Errors are keyed by method name:

    errors={"search": exc}       every call to search raises exc
    errors={"search": {2: exc}}  only the 2nd call raises (1-based, counting the calls
                                 that raise as well as the ones that return)

A raising call does not consume a canned response — the error happens *instead of* one.
So `errors={"search": {1: rate_limited()}}` plus one canned payload gives you "429, then
the answer", which is the retry path in one line.

Any exception instance works, not only `SpotifyException`: `search_with_retry` treats a
SpotifyException and a generic exception completely differently (no retry vs. bounded
retry) and both branches need coverage. For the Spotify ones use the `rate_limited()` /
`spotify_error()` builders below — they mint a REAL
`spotipy.exceptions.SpotifyException` (a pure import, no network), so the 429 branch
reads `e.http_status` and `e.headers` exactly as it does in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from spotipy.exceptions import SpotifyException

# What the fake hands back once a canned queue is empty, per method.
_EMPTY: Dict[str, Any] = {
    "search": {"tracks": {"items": []}},
    "playlist_tracks": {"items": []},
    "playlist_add_items": {"snapshot_id": "fake-snapshot"},
    "playlist_change_details": {"snapshot_id": "fake-snapshot"},
}


@dataclass(frozen=True)
class Call:
    """One recorded call. `params` holds every argument of the call, bound by name."""

    method: str
    params: Dict[str, Any]


class FakeSpotify:
    """A recording stand-in for `spotipy.Spotify`, covering the four methods this repo calls."""

    def __init__(
        self,
        *,
        search_results: Optional[Sequence[Any]] = None,
        playlist_pages: Optional[Sequence[Any]] = None,
        errors: Optional[Mapping[str, Union[BaseException, Mapping[int, BaseException]]]] = None,
    ) -> None:
        self.calls: List[Call] = []
        self._queues: Dict[str, List[Any]] = {
            "search": list(search_results or []),
            "playlist_tracks": list(playlist_pages or []),
        }
        # An exception value means "every call"; a mapping means "these call numbers".
        self._errors: Dict[str, Dict[Optional[int], BaseException]] = {
            method: ({None: spec} if isinstance(spec, BaseException) else dict(spec))
            for method, spec in (errors or {}).items()
        }
        self._counts: Dict[str, int] = {}

    # -- assertion helpers ----------------------------------------------------

    def calls_to(self, method: str) -> List[Call]:
        return [call for call in self.calls if call.method == method]

    # -- the four methods -----------------------------------------------------

    def search(
        self,
        q: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        type: str = "track",  # noqa: A002 — mirrors spotipy's own parameter name
        market: Optional[str] = None,
    ) -> Any:
        return self._dispatch(
            "search",
            {"q": q, "limit": limit, "offset": offset, "type": type, "market": market},
        )

    def playlist_tracks(
        self,
        playlist_id: Optional[str] = None,
        fields: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        market: Optional[str] = None,
    ) -> Any:
        return self._dispatch(
            "playlist_tracks",
            {
                "playlist_id": playlist_id,
                "fields": fields,
                "limit": limit,
                "offset": offset,
                "market": market,
            },
        )

    def playlist_add_items(
        self,
        playlist_id: Optional[str] = None,
        items: Optional[Sequence[str]] = None,
        position: Optional[int] = None,
    ) -> Any:
        return self._dispatch(
            "playlist_add_items",
            {"playlist_id": playlist_id, "items": list(items or []), "position": position},
        )

    def playlist_change_details(
        self,
        playlist_id: Optional[str] = None,
        name: Optional[str] = None,
        public: Optional[bool] = None,
        collaborative: Optional[bool] = None,
        description: Optional[str] = None,
    ) -> Any:
        return self._dispatch(
            "playlist_change_details",
            {
                "playlist_id": playlist_id,
                "name": name,
                "public": public,
                "collaborative": collaborative,
                "description": description,
            },
        )

    # -- plumbing -------------------------------------------------------------

    def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        self.calls.append(Call(method, params))
        self._counts[method] = self._counts.get(method, 0) + 1

        spec = self._errors.get(method)
        if spec is not None:
            exc = spec.get(self._counts[method], spec.get(None))
            if exc is not None:
                raise exc

        queue = self._queues.get(method)
        if queue:
            return queue.pop(0)
        return _EMPTY[method]


# =============================================================================
# Payload builders — the real Spotify shapes, written down once
# =============================================================================


def track(
    track_id: str,
    name: str,
    artists: Sequence[str],
    album: str = "An Album",
    popularity: int = 50,
) -> Dict[str, Any]:
    """One item from a search response, in the shape `search_and_score` reads."""
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist} for artist in artists],
        "album": {"name": album},
        "popularity": popularity,
    }


def search_payload(*tracks: Dict[str, Any]) -> Dict[str, Any]:
    """A `sp.search()` response. No arguments = the no-results case."""
    return {"tracks": {"items": list(tracks)}}


def playlist_page(*track_ids: Optional[str]) -> Dict[str, Any]:
    """One page of `sp.playlist_tracks()`. A `None` id becomes an item whose track is
    None — the removed / local-file case `get_playlist_tracks` guards against."""
    return {"items": [{"track": None if tid is None else {"id": tid}} for tid in track_ids]}


def rate_limited(retry_after: Optional[int] = None) -> SpotifyException:
    """A real 429. Omit `retry_after` to exercise the header-missing default (production
    falls back to 5 seconds, then adds one of its own)."""
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return SpotifyException(429, -1, "API rate limit exceeded", headers=headers)


def spotify_error(http_status: int = 500, msg: str = "Server error") -> SpotifyException:
    """A real non-429 Spotify failure — the branch that is deliberately never retried."""
    return SpotifyException(http_status, -1, msg)
