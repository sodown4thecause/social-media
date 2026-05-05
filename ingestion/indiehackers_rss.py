from __future__ import annotations
from datetime import datetime
import html
import re
import time
from typing import Dict, Any, Iterable, List

import requests

from .prefilter import prefilter
from .logging_config import log
from .feed_utils import fetch_feed

IH_RSS_BASE = "https://www.indiehackers.com/feed.xml"
IH_JSON_FEED = "https://feed.indiehackers.world/posts.json"


def _clean_html(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_json_date(value: str | None) -> int:
    if not value:
        return int(time.time())
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


def _parse_author(author: Any) -> str | None:
    if isinstance(author, dict):
        return (author.get("name") or "").strip() or None
    if isinstance(author, str):
        return author.strip() or None
    return None


def _fetch_indiehackers_json(limit: int) -> Iterable[Dict[str, Any]]:
    log.info("Fetching IndieHackers JSON feed", extra={"url": IH_JSON_FEED})
    response = requests.get(
        IH_JSON_FEED,
        timeout=30,
        headers={
            "User-Agent": "LeanGrowthIntelligence/1.0",
            "Accept": "application/feed+json, application/json, */*",
        },
    )
    response.raise_for_status()
    data = response.json()

    count = 0
    for entry in data.get("items", []) or []:
        if count >= limit:
            break

        title = (entry.get("title") or "").strip()
        raw_body = (
            entry.get("content_text")
            or entry.get("summary")
            or _clean_html(entry.get("content_html") or "")
        )
        body = raw_body.strip() if isinstance(raw_body, str) else ""
        text = f"{title}\n{body}" if title and body else title or body
        if not text:
            continue

        count += 1
        yield {
            "url": entry.get("url") or entry.get("external_url") or IH_JSON_FEED,
            "author": _parse_author(entry.get("author")),
            "text": text,
            "created_at": _parse_json_date(entry.get("date_published") or entry.get("date_modified")),
            "upvotes": None,
            "comments": None,
        }


def fetch_indiehackers(limit: int = 20) -> Iterable[Dict[str, Any]]:
    yielded = False
    try:
        for item in _fetch_indiehackers_json(limit):
            yielded = True
            yield item
        if yielded:
            return
    except Exception as e:
        log.warning("IndieHackers JSON feed failed, falling back to RSS", extra={"error": str(e)})

    url = IH_RSS_BASE
    log.info("Fetching IndieHackers RSS", extra={"url": url})
    feed = fetch_feed(url)

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
