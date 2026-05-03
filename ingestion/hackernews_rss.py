from __future__ import annotations
import hashlib
import time
from typing import Dict, Any, Iterable, List

import feedparser

from .prefilter import prefilter
from .logging_config import log

HN_RSS_URL = "https://hnrss.org/frontpage?points={min_points}&count={limit}"
HN_SEARCH_RSS = "https://hnrss.org/newest?q={query}&points={min_points}&count={limit}"


def fetch_hackernews(limit: int = 30, min_points: int = 5, query: str | None = None) -> Iterable[Dict[str, Any]]:
    if query:
        url = HN_SEARCH_RSS.format(query=query, min_points=min_points, limit=limit)
    else:
        url = HN_RSS_URL.format(min_points=min_points, limit=limit)

    log.info("Fetching HackerNews RSS", extra={"url": url})
    feed = feedparser.parse(url)
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = entry.get("link") or ""
        author = (entry.get("author") or "").strip() or None
        published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        created_at = int(time.mktime(published_parsed)) if published_parsed else int(time.time())

        # hn_description may contain the self-post text
        description = (entry.get("hn_description") or entry.get("summary") or "").strip()
        text = f"{title}\n{description}" if description else title

        # approximate engagement from points
        points = 0
        for tag in entry.get("tags", []) or []:
            label = (tag.get("label") or "").lower()
            term = (tag.get("term") or "").lower()
            if "points" in label or "points" in term:
                try:
                    points = int(label.split()[0])
                except (ValueError, IndexError):
                    pass

        yield {
            "url": link,
            "author": author,
            "text": text,
            "created_at": created_at,
            "upvotes": points if points > 0 else None,
            "comments": None,
        }


def scored_rows(limit: int = 30, min_points: int = 5) -> Iterable[Dict[str, Any]]:
    for item in fetch_hackernews(limit=limit, min_points=min_points):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": pf.hash,
        }
