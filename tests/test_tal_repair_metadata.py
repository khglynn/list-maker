from pipeline.scrapers.tal.repair_metadata import (
    clean_title,
    extract_episode_number,
    parse_page_metadata,
)


def test_clean_title_removes_site_suffix() -> None:
    assert clean_title("Mind Games - This American Life") == "Mind Games"
    assert clean_title("Mind Games | This American Life") == "Mind Games"


def test_extract_episode_number_from_official_url() -> None:
    assert extract_episode_number("https://www.thisamericanlife.org/331/habeas-schmabeas-2007") == 331


def test_parse_page_metadata_from_meta_tags() -> None:
    markup = """
    <html>
      <head>
        <meta property="og:title" content="Habeas Schmabeas - This American Life">
        <meta property="article:published_time" content="2007-03-09T00:00:00-05:00">
      </head>
    </html>
    """

    metadata = parse_page_metadata(
        "https://www.thisamericanlife.org/331/habeas-schmabeas-2007",
        markup,
    )

    assert metadata.title == "Habeas Schmabeas"
    assert metadata.publish_date == "2007-03-09"
    assert metadata.episode_number == 331
