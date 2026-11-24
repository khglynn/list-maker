"""Read-only probe of each show's public RSS feed (2026-11-23 off-Taddy plan).

Finds each feed via the iTunes lookup/search API, then reads the feed and reports:
item count, guid shape, date range, enclosure host, and podcast:transcript tags.
Standard library only. Writes a JSON summary next to this file.
"""
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from collections import Counter
from pathlib import Path

UA = {"User-Agent": "feed-probe/1.0"}
PI_NS = "https://podcastindex.org/namespace/1.0"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

SHOWS = {
    "ai-daily-brief": "The AI Daily Brief",
    "hard-fork": "Hard Fork",
    "pchh": "Pop Culture Happy Hour",
    "tal": "This American Life",
    "sop": "Switched On Pop",
}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl(), dict(r.headers)


def itunes_search(term):
    q = urllib.parse.urlencode({"term": term, "media": "podcast", "entity": "podcast", "limit": 5})
    body, _, _ = get(f"https://itunes.apple.com/search?{q}")
    return json.loads(body)["results"]


def probe_feed(url):
    body, final_url, headers = get(url, timeout=120)
    root = ET.fromstring(body)
    ch = root.find("channel")
    items = ch.findall("item")
    out = {"feed_url": url, "final_url": final_url, "bytes": len(body), "items": len(items),
           "etag": headers.get("ETag") or headers.get("etag"),
           "last_modified": headers.get("Last-Modified") or headers.get("last-modified")}
    dates, guid_samples, transcript_types, link_samples, enc_hosts = [], [], Counter(), [], Counter()
    with_transcript = 0
    recent_with_transcript = 0
    guid_unique = set()
    for i, it in enumerate(items):
        guid = (it.findtext("guid") or "").strip()
        guid_unique.add(guid)
        if i < 3:
            guid_samples.append(guid)
            link_samples.append((it.findtext("link") or "").strip())
        pd = it.findtext("pubDate")
        try:
            dates.append(parsedate_to_datetime(pd).date().isoformat())
        except Exception:
            pass
        enc = it.find("enclosure")
        if enc is not None and enc.get("url"):
            enc_hosts[urllib.parse.urlparse(enc.get("url")).netloc] += 1
        ts = it.findall(f"{{{PI_NS}}}transcript")
        if ts:
            with_transcript += 1
            if i < 20:
                recent_with_transcript += 1
            for t in ts:
                transcript_types[t.get("type")] += 1
            if "transcript_sample" not in out:
                out["transcript_sample"] = [{"url": t.get("url"), "type": t.get("type")} for t in ts]
    out.update({
        "newest": max(dates) if dates else None,
        "oldest": min(dates) if dates else None,
        "guid_samples": guid_samples,
        "guids_unique": len(guid_unique) == len(items),
        "link_samples": link_samples,
        "enclosure_hosts": enc_hosts.most_common(3),
        "items_with_podcast_transcript": with_transcript,
        "of_newest_20_with_transcript": recent_with_transcript,
        "transcript_types": dict(transcript_types),
        "channel_title": ch.findtext("title"),
        "itunes_new_feed_url": ch.findtext(f"{{{ITUNES_NS}}}new-feed-url"),
    })
    # durations of newest 10
    durs = []
    for it in items[:10]:
        d = it.findtext(f"{{{ITUNES_NS}}}duration")
        durs.append(d)
    out["newest_10_durations"] = durs
    return out


def main():
    results = {}
    for slug, name in SHOWS.items():
        entry = {"search_term": name}
        try:
            hits = itunes_search(name)
            entry["itunes_hits"] = [
                {"collectionId": h.get("collectionId"), "name": h.get("collectionName"),
                 "artist": h.get("artistName"), "feedUrl": h.get("feedUrl"),
                 "trackCount": h.get("trackCount")}
                for h in hits[:3]
            ]
            feed = hits[0].get("feedUrl") if hits else None
            entry["chosen_feed"] = feed
            if feed:
                entry["feed"] = probe_feed(feed)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results[slug] = entry
        print(slug, "done", file=sys.stderr)
    out = Path(sys.argv[1])
    out.write_text(json.dumps(results, indent=2))
    print(out)


if __name__ == "__main__":
    main()
