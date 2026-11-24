"""Monthly episode count + audio minutes per show from the live feeds (read-only)."""
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "feed-probe/1.0"}
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
FEEDS = {
    "ai-daily-brief": "https://anchor.fm/s/f7cac464/podcast/rss",
    "hard-fork": "https://feeds.simplecast.com/6HKOhNgS",
    "pchh": "https://feeds.npr.org/510282/podcast.xml",
    "tal": "https://www.thisamericanlife.org/podcast/rss.xml",
}


def dur_seconds(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        if ":" in raw:
            parts = [int(p) for p in raw.split(":")]
            s = 0
            for p in parts:
                s = s * 60 + p
            return s
        return int(float(raw))
    except ValueError:
        return None


now = datetime.now(timezone.utc)
out = {}
for slug, url in FEEDS.items():
    req = urllib.request.Request(url, headers=UA)
    body = urllib.request.urlopen(req, timeout=120).read()
    ch = ET.fromstring(body).find("channel")
    rows = []
    for it in ch.findall("item"):
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            continue
        enc = it.find("enclosure")
        rows.append({
            "dt": dt,
            "secs": dur_seconds(it.findtext(f"{{{ITUNES_NS}}}duration")),
            "bytes": int(enc.get("length") or 0) if enc is not None and (enc.get("length") or "").isdigit() else 0,
            "title": (it.findtext("title") or "")[:70],
        })
    def window(days):
        sel = [r for r in rows if r["dt"] >= now - timedelta(days=days)]
        mins = sum((r["secs"] or 0) for r in sel) / 60
        mb = sum(r["bytes"] for r in sel) / 1e6
        return {"episodes": len(sel), "audio_minutes": round(mins), "audio_mb": round(mb)}
    out[slug] = {
        "last_30d": window(30),
        "last_60d": window(60),
        "last_90d": window(90),
        "newest_5": [(r["dt"].isoformat(), r["secs"], r["title"]) for r in sorted(rows, key=lambda r: r["dt"], reverse=True)[:5]],
    }
print(json.dumps(out, indent=2, default=str))
