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


def test_blog_target_properties_use_curated_vocabulary() -> None:
    """Per-target labels (2026-06-11 UX pass): blog rows say Source/Item ID and skip
    the constant source-label select; transcripts keep Show/Episode ID/Source."""
    from pipeline.sync_transcripts_notion import SYNC_TARGETS, build_properties

    ep = {
        "episode_id": 1, "title": "Post", "publish_date": None, "show_slug": "openai-blog",
        "chars": 10, "url": "https://openai.com/index/abc",
        "transcript_text": "see https://a.com and https://b.com",
    }
    blog_props = build_properties(ep, SYNC_TARGETS["blog-posts"])
    assert blog_props["URL"] == {"url": "https://openai.com/index/abc"}
    assert blog_props["Links Out"] == {"number": 2}
    assert blog_props["Source"]["select"]["name"] == "OpenAI Blog"
    assert blog_props["Item ID"] == {"number": 1}
    assert "Show" not in blog_props and "Episode ID" not in blog_props

    transcript_props = build_properties(ep, SYNC_TARGETS["transcripts"])
    assert "URL" not in transcript_props and "Links Out" not in transcript_props
    assert transcript_props["Source"]["select"]["name"] == "transcript"
    assert transcript_props["Episode ID"] == {"number": 1}
    assert "Item ID" not in transcript_props


def test_sync_target_shows_come_from_show_config() -> None:
    from pipeline.show_config import BLOG_NOTION_SHOWS, TRANSCRIPT_NOTION_SHOWS
    from pipeline.sync_transcripts_notion import SYNC_TARGETS

    assert SYNC_TARGETS["transcripts"]["shows"] == TRANSCRIPT_NOTION_SHOWS
    assert SYNC_TARGETS["blog-posts"]["shows"] == BLOG_NOTION_SHOWS


def test_episode_source_name_prefers_oneoff_real_show() -> None:
    """Saved-episode pages must show the REAL show (Science Vs), not the
    catch-all; non-oneoff rows keep their configured display name."""
    from pipeline.sync_transcripts_notion import build_properties, episode_source_name, SYNC_TARGETS

    oneoff = {"episode_id": 9, "title": "T", "publish_date": None, "chars": 5,
              "show_slug": "saved-episodes", "source_type": "castro_clip",
              "raw_content": '{"provider": "oneoff_episode", "source_name": "Science Vs"}'}
    assert episode_source_name(oneoff) == "Science Vs"
    props = build_properties(oneoff, SYNC_TARGETS["transcripts"])
    assert props["Show"]["select"]["name"] == "Science Vs"
    assert props["Source"]["select"]["name"] == "castro_clip"  # honest per-row source

    regular = {"episode_id": 1, "title": "T", "publish_date": None, "chars": 5,
               "show_slug": "ai-daily-brief", "source_type": "taddy_transcript", "raw_content": None}
    assert episode_source_name(regular) == "AI Daily"
    props = build_properties(regular, SYNC_TARGETS["transcripts"])
    assert props["Source"]["select"]["name"] == "transcript"  # blanket label, NOT source_type
