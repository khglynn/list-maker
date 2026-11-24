"""Does NPR publish a full transcript for each recent PCHH episode, and how fast? (read-only)

For the newest N feed items: derive the story id from <link> (nx-s1-NNNN), GET
https://www.npr.org/transcripts/<id>, strip tags, and report the text length and
whether the page reads like a full transcript (speaker labels, length vs duration).
"""
import html
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0 (feed-probe)"}
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 12

body = urllib.request.urlopen(urllib.request.Request("https://feeds.npr.org/510282/podcast.xml", headers=UA), timeout=120).read()
items = ET.fromstring(body).find("channel").findall("item")[:N]
for it in items:
    link = (it.findtext("link") or "").strip()
    m = re.search(r"/(nx-s1-\d+|\d{6,})/", link)
    title = (it.findtext("title") or "")[:55]
    pub = parsedate_to_datetime(it.findtext("pubDate")).date()
    dur = it.findtext(f"{{{ITUNES_NS}}}duration")
    if not m:
        print(f"{pub} | no story id in link {link!r} | {title}")
        continue
    sid = m.group(1)
    url = f"https://www.npr.org/transcripts/{sid}"
    try:
        page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")
        # NPR's transcript body lives in <div class="transcript storytext"> ... paragraphs
        mm = re.search(r'class="transcript storytext"(.*?)</div>\s*</div>', page, re.S)
        chunk = mm.group(1) if mm else page
        paras = re.findall(r"<p>(.*?)</p>", chunk, re.S)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)) for p in paras)
        text = re.sub(r"\s+", " ", text).strip()
        speakers = len(re.findall(r"\b[A-Z][A-Z' -]{2,}:", text))
        words = len(text.split())
        wpm = round(words / (int(dur) / 60)) if dur and dur.isdigit() and int(dur) else None
        print(f"{pub} | {sid} | words={words} wpm_vs_duration={wpm} speaker_tags={speakers} | {title}")
    except Exception as exc:  # noqa: BLE001
        print(f"{pub} | {sid} | ERROR {type(exc).__name__}: {exc} | {title}")
    time.sleep(1)
