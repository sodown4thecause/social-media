from __future__ import annotations
import hashlib
import time
from typing import Dict, Any, Iterable, List

from pydantic import BaseModel, Field

from .prefilter import prefilter
from .logging_config import log
from .browser_use_client import run_task
from .review_extract import extract_review_nodes
from .scrapingbee_client import fetch_html


class CapterraReview(BaseModel):
    title: str = Field(default="", description="Review title or headline")
    text: str = Field(description="Full review body text")
    rating: int | None = Field(default=None, description="Star rating (1-5)")
    reviewer_name: str | None = Field(default=None, description="Name of the reviewer")


class CapterraResult(BaseModel):
    reviews: List[CapterraReview] = Field(default_factory=list, description="Extracted reviews from Capterra")


def fetch_capterra(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
    competitor_slugs: List[str] | None = None,
    max_review_stars: int = 3,
) -> Iterable[Dict[str, Any]]:
    if categories is None:
        categories = ["seo-software", "marketing-analytics"]
    if competitor_slugs is None:
        competitor_slugs = []

    now = int(time.time())

    for cat in categories:
        url = f"https://www.capterra.com/p/{cat}/reviews/"
        yield from _scrape_capterra_url(url, cat, reviews_per_category, now, max_stars=max_review_stars)

    for slug in competitor_slugs:
        url = f"https://www.capterra.com/p/{slug}/reviews/"
        yield from _scrape_capterra_url(url, slug, reviews_per_category, now, max_stars=max_review_stars)


def _scrape_capterra_url(url: str, label: str, limit: int, now: int, max_stars: int = 5) -> Iterable[Dict[str, Any]]:
    rows = list(_scrape_capterra_with_scrapingbee(url, label, limit, now, max_stars=max_stars))
    if rows:
        yield from rows
        return

    log.info("Fetching Capterra via browser-use", extra={"label": label, "url": url})
    try:
        result = run_task(
            f"Go to {url}. Extract up to {limit} user reviews from the page. "
            f"For each review, get: the title, the full review text, the star rating (1-5), "
            f"and the reviewer name if shown. Only include reviews with {max_stars} stars or fewer. "
            f"Return as structured data.",
            output_schema=CapterraResult,
        )
    except Exception as e:
        log.warning("Capterra browser-use failed", extra={"label": label, "error": str(e)})
        return

    if not isinstance(result, CapterraResult) or not result.reviews:
        log.info("Capterra returned no reviews", extra={"label": label})
        return

    for rev in result.reviews:
        if not rev.text or len(rev.text.strip()) < 30:
            continue
        if rev.rating and rev.rating > max_stars:
            continue
        full_text = f"{rev.title}\n{rev.text}" if rev.title else rev.text
        yield {
            "url": url,
            "author": rev.reviewer_name,
            "text": full_text.strip(),
            "created_at": now,
            "upvotes": rev.rating,
            "comments": None,
        }

    log.info("Capterra reviews fetched via browser-use", extra={"label": label, "count": len(result.reviews)})


def _scrape_capterra_with_scrapingbee(url: str, label: str, limit: int, now: int, max_stars: int = 5) -> Iterable[Dict[str, Any]]:
    try:
        page_html = fetch_html(url, render_js=True, timeout=120, premium_proxy=True, country_code="us")
    except Exception as e:
        log.warning("Capterra ScrapingBee fetch failed", extra={"label": label, "error": str(e)})
        return
    if not page_html:
        return

    count = 0
    for review in extract_review_nodes(page_html):
        if count >= limit:
            break
        rating = review.get("rating")
        if rating and rating > max_stars:
            continue
        text = review.get("text") or ""
        if len(text) < 30:
            continue
        title = review.get("title") or ""
        full_text = f"{title}\n{text}" if title and title != text else text
        count += 1
        yield {
            "url": url,
            "author": review.get("author"),
            "text": full_text.strip(),
            "created_at": now,
            "upvotes": int(rating) if rating else None,
            "comments": None,
        }
    if count:
        log.info("Capterra reviews fetched via ScrapingBee", extra={"label": label, "count": count})


def scored_rows(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
    competitor_slugs: List[str] | None = None,
    max_review_stars: int = 3,
) -> Iterable[Dict[str, Any]]:
    for item in fetch_capterra(
        categories=categories,
        reviews_per_category=reviews_per_category,
        competitor_slugs=competitor_slugs,
        max_review_stars=max_review_stars,
    ):
        pf = prefilter(item["text"], item["created_at"], item.get("upvotes"), item.get("comments"))
        h = hashlib.sha1(f"{pf.text}:{pf.created_at}".encode("utf-8")).hexdigest()
        yield {
            **item,
            "norm_text": pf.text,
            "recency_score": pf.recency_score,
            "prefilter_score": pf.score,
            "hash": h,
            "engagement": float(item.get("upvotes") or 0),
        }
