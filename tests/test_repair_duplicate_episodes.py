from datetime import date

from pipeline.repair_duplicate_episodes import choose_canonical


def episode(**overrides):
    base = {
        "id": 1,
        "slug": "sop",
        "title": "Example Episode",
        "url": "https://example.com/audio.mp3",
        "publish_date": date(2026, 1, 1),
        "episode_number": None,
        "description_body": None,
        "audio_url": None,
        "image_url": None,
        "has_raw_content": False,
        "song_count": 0,
        "transcript_count": 0,
    }
    base.update(overrides)
    return base


def test_sop_duplicate_prefers_human_song_page_over_audio_transcript_row() -> None:
    canonical, donors, choice = choose_canonical(
        [
            episode(
                id=10,
                url="https://traffic.megaphone.fm/example.mp3",
                audio_url="https://traffic.megaphone.fm/example.mp3",
                transcript_count=1,
            ),
            episode(
                id=20,
                url="https://switchedonpop.com/episodes/example",
                has_raw_content=True,
                song_count=12,
                description_body="Show notes",
            ),
        ]
    )

    assert canonical["id"] == 20
    assert [row["id"] for row in donors] == [10]
    assert "human URL" in choice.reasons


def test_tal_duplicate_prefers_non_reissue_url_when_otherwise_equal() -> None:
    canonical, donors, _choice = choose_canonical(
        [
            episode(
                id=1,
                slug="tal",
                title="Mind Games",
                url="https://www.thisamericanlife.org/286/mind-games",
                publish_date=date(2005, 4, 8),
                episode_number=286,
                has_raw_content=True,
                song_count=1,
                description_body="Official notes",
            ),
            episode(
                id=2,
                slug="tal",
                title="Mind Games",
                url="https://www.thisamericanlife.org/286/mind-games-2005",
                publish_date=date(2005, 4, 8),
                episode_number=286,
                has_raw_content=True,
                song_count=1,
                description_body="Official notes",
            ),
        ]
    )

    assert canonical["id"] == 1
    assert [row["id"] for row in donors] == [2]
