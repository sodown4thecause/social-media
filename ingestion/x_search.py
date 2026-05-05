from __future__ import annotations
from collections.abc import Sequence
from email.utils import parsedate_to_datetime
import os
import time
from typing import Iterable, Dict, Any, List
import html
import re
import urllib.parse

import requests

from .env_loader import load_local_env
from .prefilter import prefilter
from .feed_utils import fetch_feed
from .logging_config import log

# We use Nitter RSS as a low-cost, low-friction read-only view for search timelines where available.
# Note: Nitter instances can be rate-limited or down; this is a best-effort v1.
# When Nitter is unavailable, GetXAPI is the reliable paid fallback. Firecrawl is a last resort.
GETXAPI_SEARCH_URL = "https://api.getxapi.com/twitter/tweet/advanced_search"


def nitter_search_rss(base: str, query: str, limit: int = 20) -> str:
    q = urllib.parse.quote(query)
    return f"{base.rstrip('/')}/search/rss?f=tweets&q={q}&count={limit}"


def _base_candidates(base: str | Sequence[str]) -> list[str]:
    bases = [base] if isinstance(base, str) else list(base)
    seen: set[str] = set()
    out: list[str] = []
    for value in bases:
        normalized = value.rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def parse_x_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
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


def fetch_x_search(
    base: str | Sequence[str],
    queries: List[str],
    limit_per: int = 20,
    max_queries: int | None = None,
    getxapi_product: str = "Latest",
):
    load_local_env()
    bases = _base_candidates(base)
    selected_queries = queries[:max_queries] if max_queries else queries
    for q in selected_queries:
        found = False
        for item in _getxapi_x_search(q, limit_per, product=getxapi_product):
            found = True
            yield item
        if found:
            continue

        found = False
        for candidate_base in bases:
            url = nitter_search_rss(candidate_base, q, limit_per)
            try:
                feed = fetch_feed(url, timeout=20)
            except Exception as e:
                log.warning("Nitter RSS fetch failed", extra={"base": candidate_base, "query": q, "error": str(e)})
                continue

            entries = getattr(feed, "entries", []) or []
            if not entries:
                log.info("Nitter RSS returned no entries", extra={"base": candidate_base, "query": q})
                continue

            for entry in entries:
                yield parse_x_entry(entry)
            found = True
            break

        if not found:
            log.info("Nitter unavailable for query, using Firecrawl fallback", extra={"query": q})
            yield from _firecrawl_x_fallback(q, limit_per)


def _parse_created_at(value: str | None) -> int:
    if not value:
        return int(time.time())
    try:
        return int(parsedate_to_datetime(value).timestamp())
    except (TypeError, ValueError):
        return int(time.time())


def _author_name(author: Any) -> str | None:
    if isinstance(author, dict):
        return (
            author.get("userName")
            or author.get("username")
            or author.get("screen_name")
            or author.get("name")
            or None
        )
    if isinstance(author, str):
        return author.strip() or None
    return None


def _getxapi_x_search(query: str, limit: int = 20, product: str = "Latest") -> Iterable[Dict[str, Any]]:
    key = os.getenv("GETXAPI_API_KEY", "").strip()
    if not key:
        return

    params = {"q": query, "product": product}
    try:
        response = requests.get(
            GETXAPI_SEARCH_URL,
            params=params,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        log.warning("GetXAPI X search failed", extra={"query": query, "error": str(e)})
        return

    tweets = data.get("tweets", []) if isinstance(data, dict) else []
    for tweet in tweets[:limit]:
        text = (tweet.get("text") or "").strip()
        if not text:
            continue
        yield {
            "url": tweet.get("url") or tweet.get("twitterUrl") or "",
            "author": _author_name(tweet.get("author")),
            "text": text,
            "created_at": _parse_created_at(tweet.get("createdAt")),
            "upvotes": tweet.get("likeCount"),
            "comments": tweet.get("replyCount"),
        }


def _firecrawl_x_fallback(query: str, limit: int = 20) -> Iterable[Dict[str, Any]]:
    load_local_env()
    fc_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not fc_key:
        log.warning("No FIRECRAWL_API_KEY set, skipping X/Twitter Firecrawl fallback")
        return

    url = "https://api.firecrawl.dev/v1/search"
    headers = {
        "Authorization": f"Bearer {fc_key}",
        "Content-Type": "application/json",
    }
    body = {
        "query": f"site:x.com OR site:twitter.com {query}",
        "limit": min(limit, 10),
        "lang": "en",
        "scrapeOptions": {"formats": ["markdown"]},
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("Firecrawl X search failed", extra={"query": query, "error": str(e)})
        return

    results = []
    if isinstance(data, dict):
        results = data.get("data", data.get("web", []))
    if isinstance(results, list):
        for item in results[:limit]:
            text = item.get("markdown") or item.get("content") or item.get("description") or ""
            title = item.get("title") or ""
            content_text = f"{title}\n{text}".strip() if title else text
            if not content_text or len(content_text) < 20:
                continue
            yield {
                "url": item.get("url", ""),
                "author": item.get("author") or None,
                "text": content_text,
                "created_at": int(time.time()),
                "upvotes": None,
                "comments": None,
            }


def scored_rows(
    base: str | Sequence[str],
    queries: List[str],
    limit_per: int = 20,
    max_queries: int | None = None,
    getxapi_product: str = "Latest",
):
    for item in fetch_x_search(base, queries, limit_per, max_queries=max_queries, getxapi_product=getxapi_product):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": pf.hash,
        }
