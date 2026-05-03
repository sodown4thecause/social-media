from __future__ import annotations
import time
from typing import Dict, Any, Iterable, List

import feedparser

from .prefilter import prefilter
from .logging_config import log

IH_RSS_BASE = "https://www.indiehackers.com/feed.xml"


def fetch_indiehackers(limit: int = 20) -> Iterable[Dict[str, Any]]:
    url = IH_RSS_BASE
    log.info("Fetching IndieHackers RSS", extra={"url": url})
    feed = feedparser.parse(url)

    count = 0
    for entry in feed.entries:
        if count >= limit:
            break

        title = (entry.get("title") or "").strip()
        link = entry.get("link") or ""
        author = (entry.get("author") or "").strip() or None
        summary = (entry.get("summary") or "").strip()
        text = f"{title}\n{summary}" if summary else title

        published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        created_at = int(time.mktime(published_parsed)) if published_parsed else int(time.time())

        count += 1
        yield {
            "url": link,
            "author": author,
            "text": text,
            "created_at": created_at,
            "upvotes": None,
            "comments": None,
        }


def scored_rows(limit: int = 20) -> Iterable[Dict[str, Any]]:
    for item in fetch_indiehackers(limit=limit):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": pf.hash,
        }
