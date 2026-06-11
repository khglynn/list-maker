"""Unit tests for the transcript->Notion chunker.

chunk_text guards a hard external limit: Notion rejects a rich_text content value over
2000 chars. If a transcript ever produces an over-limit chunk, the page create fails — so
the size invariant is pinned here.
"""

from pipeline.sync_transcripts_notion import RICH_TEXT_LIMIT, chunk_text


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("hello world") == ["hello world"]


def test_empty_is_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_every_chunk_within_limit() -> None:
    text = "word " * 5000  # ~25k chars
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= RICH_TEXT_LIMIT for c in chunks)
    # nothing dropped (modulo whitespace normalization at the joins)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_breaks_at_whitespace_not_mid_word() -> None:
    text = ("alpha " * 400).strip()  # 2400 chars, all short words
    chunks = chunk_text(text)
    # no chunk should end mid-"alpha" — boundary lands on a space
    for c in chunks[:-1]:
        assert c.endswith("alpha")


def test_long_unbroken_token_hard_splits_at_limit() -> None:
    text = "x" * (RICH_TEXT_LIMIT + 500)  # no spaces to break on
    chunks = chunk_text(text)
    assert all(len(c) <= RICH_TEXT_LIMIT for c in chunks)
    assert "".join(chunks) == text


def test_default_shows_derives_from_show_config() -> None:
    """Single-source guard: the sync default and data_health's notion-freshness
    check must watch the SAME shows (drift here = silent unmonitored sync gaps)."""
    from pipeline.show_config import TRANSCRIPT_NOTION_SHOWS
    from pipeline.sync_transcripts_notion import DEFAULT_SHOWS

    assert DEFAULT_SHOWS == ",".join(TRANSCRIPT_NOTION_SHOWS)
