from __future__ import annotations
import re
import time
from typing import Dict, Any, Iterable, List

from .prefilter import prefilter
from .logging_config import log
from .feed_utils import clean_feed_text, fetch_feed

PH_RSS_BASE = "https://www.producthunt.com/feed?category={topic}"
PH_TRAILING_LINKS_RE = re.compile(r"\s*Discussion\s*\|\s*Link\s*$", re.IGNORECASE)


def fetch_producthunt(limit: int = 20, topic: str = "marketing",
                      secondary_topic: str | None = "artificial-intelligence") -> Iterable[Dict[str, Any]]:
    urls = [PH_RSS_BASE.format(topic=topic)]
    if secondary_topic:
        urls.append(PH_RSS_BASE.format(topic=secondary_topic))

    seen_urls = set()
    count = 0

    for url in urls:
        if count >= limit:
            break
        log.info("Fetching ProductHunt RSS", extra={"url": url})
        feed = fetch_feed(url)

        for entry in feed.entries:
            if count >= limit:
                break

            link = entry.get("link") or ""
            if link in seen_urls:
                continue
            seen_urls.add(link)

            title = (entry.get("title") or "").strip()
            author = None
            summary = clean_feed_text(entry.get("summary"))
            summary = PH_TRAILING_LINKS_RE.sub("", summary).strip()
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


def scored_rows(limit: int = 20, topic: str = "marketing",
                secondary_topic: str | None = "artificial-intelligence") -> Iterable[Dict[str, Any]]:
    for item in fetch_producthunt(limit=limit, topic=topic, secondary_topic=secondary_topic):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": pf.hash,
        }
