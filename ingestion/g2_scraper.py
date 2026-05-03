from __future__ import annotations
import hashlib
import html
import re
import time
from typing import Dict, Any, Iterable, List

import requests

from .prefilter import prefilter
from .logging_config import log

G2_REVIEW_URL = "https://www.g2.com/categories/{category}"

STRIP_HTML = re.compile(r"<[^>]+>")
WS_COLLAPSE = re.compile(r"\s+")
REVIEW_BLOCK_RE = re.compile(
    r'<div[^>]*class="[^"]*review[^"]*"[^>]*>.*?</div>\s*</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
REVIEW_PRO_RE = re.compile(
    r'<p[^>]*class="[^"]*(?:formatted-text|review-text|body)[^"]*"[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
REVIEW_CON_RE = re.compile(
    r'<p[^>]*class="[^"]*(?:formatted-text|review-text|cons)[^"]*"[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_html(raw: str) -> str:
    t = STRIP_HTML.sub(" ", raw)
    t = html.unescape(t)
    t = WS_COLLAPSE.sub(" ", t)
    return t.strip()


def _extract_reviews(html_str: str, limit: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    # Look for pros/cons review text blocks
    pros = REVIEW_PRO_RE.findall(html_str)
    cons = REVIEW_CON_RE.findall(html_str)

    for i in range(min(len(pros), limit)):
        pro_text = _clean_html(pros[i])
        con_text = _clean_html(cons[i]) if i < len(cons) else ""
        if len(pro_text) < 30:
            continue
        combined = f"Pros: {pro_text}"
        if con_text:
            combined += f"\nCons: {con_text}"
        results.append({"title": "", "text": combined})

    return results


def fetch_g2(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
    timeout: int = 30,
) -> Iterable[Dict[str, Any]]:
    if categories is None:
        categories = ["seo", "marketing-analytics"]

    session = requests.Session()
    session.headers.update(HEADERS)

    for cat in categories:
        url = G2_REVIEW_URL.format(category=cat)
        log.info("Fetching G2 reviews", extra={"category": cat, "url": url})
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("G2 fetch failed", extra={"category": cat, "error": str(e)})
            continue

        reviews = _extract_reviews(r.text, reviews_per_category)
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

        log.info("G2 reviews fetched", extra={"category": cat, "count": len(reviews)})


def scored_rows(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
) -> Iterable[Dict[str, Any]]:
    for item in fetch_g2(categories=categories, reviews_per_category=reviews_per_category):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        h = hashlib.sha1(f"{pf.text}:{pf.created_at}".encode("utf-8")).hexdigest()
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": h,
        }
