from __future__ import annotations
import time
from typing import Iterable, Dict, Any, List
import html
import re
import urllib.parse

import feedparser

from .prefilter import prefilter

# We use Nitter RSS as a low-cost, low-friction read-only view for search timelines where available.
# Note: Nitter instances can be rate-limited; this is a best-effort v1. Users can disable X ingestion in config.


def nitter_search_rss(base: str, query: str, limit: int = 20) -> str:
    # Nitter search RSS format varies by instance. Common pattern: /search/rss?f=tweets&q=...
    q = urllib.parse.quote(query)
    return f"{base.rstrip('/')}/search/rss?f=tweets&q={q}&count={limit}"


def parse_x_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    # entry.title often contains the text; summary may contain HTML.
    title = html.unescape((entry.get("title") or "").strip())
    summary = html.unescape(re.sub(r"<[^>]+>", " ", (entry.get("summary") or ""))).strip()
    content_text = title if len(title) >= len(summary) else summary
    link = entry.get("link") or ""
    author = (entry.get("author") or "").strip() or None
    published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if published_parsed:
        created_at = int(time.mktime(published_parsed))
    else:
        created_at = int(time.time())
    return {
        "url": link,
        "author": author,
        "text": content_text,
        "created_at": created_at,
        "upvotes": None,
        "comments": None,
    }


def fetch_x_search(base: str, queries: List[str], limit_per: int = 20):
    for q in queries:
        url = nitter_search_rss(base, q, limit_per)
        feed = feedparser.parse(url)
        for entry in feed.entries:
            yield parse_x_entry(entry)


def scored_rows(base: str, queries: List[str], limit_per: int = 20):
    for item in fetch_x_search(base, queries, limit_per):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": pf.hash,
        }
