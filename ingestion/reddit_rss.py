from __future__ import annotations
import time
from typing import Iterable, Dict, Any, List
import feedparser

from .prefilter import prefilter


def subreddit_rss_urls(subreddits: List[str], limit_per_feed: int = 25) -> List[str]:
    base = "https://www.reddit.com/r/{}/.rss?limit={}"
    return [base.format(sr, limit_per_feed) for sr in subreddits]


def parse_reddit_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    # Feed content fields are inconsistent; fall back smartly
    title = (entry.get("title") or "").strip()
    summary = (entry.get("summary") or "").strip()
    content_text = title if len(title) > len(summary) else summary
    link = entry.get("link") or ""
    author = (entry.get("author") or "").strip() or None
    # Parse published timestamp
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


def fetch_reddit(subreddits: List[str], limit_per_feed: int = 25):
    urls = subreddit_rss_urls(subreddits, limit_per_feed)
    for url in urls:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            yield parse_reddit_entry(entry)


def scored_rows(subreddits: List[str], limit_per_feed: int = 25):
    for item in fetch_reddit(subreddits, limit_per_feed):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": pf.hash,
        }
