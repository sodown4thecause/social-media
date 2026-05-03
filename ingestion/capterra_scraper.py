from __future__ import annotations
import hashlib
import html
import re
import time
from typing import Dict, Any, Iterable, List

import requests

from .prefilter import prefilter
from .logging_config import log

CAPERRA_REVIEW_URL = "https://www.capterra.com/p/{category}/reviews/"
# Capterra's page structure: reviews are in divs with class "review-card" or similar
REVIEW_BLOCK_RE = re.compile(r'<div[^>]*class="[^"]*review[^"]*"[^>]*>', re.IGNORECASE)
REVIEW_TEXT_RE = re.compile(r'<p[^>]*class="[^"]*review-text[^"]*"[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
REVIEW_TITLE_RE = re.compile(r'<h[23][^>]*>(.*?)</h[23]>', re.IGNORECASE | re.DOTALL)
STRIP_HTML = re.compile(r"<[^>]+>")
WS_COLLAPSE = re.compile(r"\s+")
CAPERRA_API_URL = "https://www.capterra.com/v2/reviews"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_html(raw: str) -> str:
    t = STRIP_HTML.sub(" ", raw)
    t = html.unescape(t)
    t = WS_COLLAPSE.sub(" ", t)
    return t.strip()


def _extract_reviews_from_html(html_str: str, limit: int) -> List[Dict[str, str]]:
    """Best-effort extraction from Capterra HTML review pages."""
    results: List[Dict[str, str]] = []

    # Try to find review-text paragraphs
    texts = REVIEW_TEXT_RE.findall(html_str)
    titles = REVIEW_TITLE_RE.findall(html_str)

    for i, text in enumerate(texts):
        if len(results) >= limit:
            break
        cleaned = _clean_html(text)
        if len(cleaned) < 40:
            continue
        title = _clean_html(titles[i]) if i < len(titles) else ""
        results.append({"title": title, "text": cleaned})

    return results


def fetch_capterra(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
    timeout: int = 30,
) -> Iterable[Dict[str, Any]]:
    if categories is None:
        categories = ["seo-software", "marketing-analytics"]

    session = requests.Session()
    session.headers.update(HEADERS)

    for cat in categories:
        url = CAPERRA_REVIEW_URL.format(category=cat)
        log.info("Fetching Capterra reviews", extra={"category": cat, "url": url})
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("Capterra fetch failed", extra={"category": cat, "error": str(e)})
            continue

        reviews = _extract_reviews_from_html(r.text, reviews_per_category)
        now = int(time.time())

        for rev in reviews:
            full_text = f"{rev['title']}\n{rev['text']}" if rev["title"] else rev["text"]
            h = hashlib.sha1(f"{full_text}:{now}:{cat}".encode("utf-8")).hexdigest()
            yield {
                "url": url,
                "author": None,
                "text": full_text,
                "created_at": now,
                "upvotes": None,
                "comments": None,
            }

        log.info("Capterra reviews fetched", extra={"category": cat, "count": len(reviews)})


def scored_rows(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
) -> Iterable[Dict[str, Any]]:
    for item in fetch_capterra(categories=categories, reviews_per_category=reviews_per_category):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        h = hashlib.sha1(f"{pf.text}:{pf.created_at}".encode("utf-8")).hexdigest()
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": h,
        }
