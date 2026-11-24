"""Coverage of AI Daily Brief's own transcript.md for each recent feed item (read-only).

For each feed item in the last N days: try https://aidailybrief.ai/e/<date>/transcript.md
for the item's date in US Eastern time and in UTC (and the day before), report status,
word count, words-per-minute vs the feed duration, and the first line (to confirm it is
the same episode).
"""
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0 (feed-probe)"}
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ET_TZ = ZoneInfo("America/New_York")


def dur(raw):
    if not raw:
        return None
    parts = [int(p) for p in raw.strip().split(":")] if ":" in raw else [int(float(raw))]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def fetch(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


body = urllib.request.urlopen(urllib.request.Request("https://anchor.fm/s/f7cac464/podcast/rss", headers=UA), timeout=120).read()
items = ET.fromstring(body).find("channel").findall("item")
now = datetime.now(timezone.utc)
hit = miss = 0
for it in items:
    dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
    if dt < now - timedelta(days=DAYS):
        break
    secs = dur(it.findtext(f"{{{ITUNES_NS}}}duration"))
    title = (it.findtext("title") or "")[:50]
    candidates = []
    for d in (dt.astimezone(ET_TZ).date(), dt.date(), dt.astimezone(ET_TZ).date() - timedelta(days=1)):
        if d not in candidates:
            candidates.append(d)
    found = None
    for d in candidates:
        status, text = fetch(f"https://aidailybrief.ai/e/{d.isoformat()}/transcript.md")
        time.sleep(0.5)
        if status == 200 and len(text.split()) > 200:
            found = (d, text)
            break
    if found:
        hit += 1
        d, text = found
        words = len(text.split())
        wpm = round(words / (secs / 60)) if secs else None
        first = re.sub(r"\s+", " ", text.strip().splitlines()[0])[:70]
        print(f"HIT  {dt:%Y-%m-%d %H:%MZ} -> /e/{d} words={words} wpm={wpm} | {title} | first: {first}")
    else:
        miss += 1
        print(f"MISS {dt:%Y-%m-%d %H:%MZ} tried {[str(c) for c in candidates]} | {title}")
print(f"\n{hit} hit, {miss} miss in last {DAYS} days")
