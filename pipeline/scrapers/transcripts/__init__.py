"""Publisher transcript readers, one per source, behind one contract (see base.py).

`READERS` maps a show's `transcript_source` (show_config) to its reader module. A new
source is a new module with a `fetch(item, session=None) -> Result` and one line here;
tests/test_show_config.py fails if a show names a source that is not registered.
"""

from __future__ import annotations

import importlib

# transcript_source -> module under scrapers.transcripts
READERS: dict[str, str] = {
    "aidailybrief": "aidailybrief",
    "npr": "npr",
    "tal": "tal",
}


def reader_for(source: str):
    """The reader module for a show's transcript_source. KeyError for an unknown one."""
    return importlib.import_module(f"scrapers.transcripts.{READERS[source]}")
