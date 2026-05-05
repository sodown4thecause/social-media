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


class G2Review(BaseModel):
    pros: str = Field(default="", description="What the reviewer liked / pros")
    cons: str = Field(default="", description="What the reviewer disliked / cons")
    overall_comment: str = Field(default="", description="Overall review comment")
    rating: float | None = Field(default=None, description="Star rating (1-5)")
    reviewer_name: str | None = Field(default=None, description="Name of the reviewer")
    reviewer_title: str | None = Field(default=None, description="Job title of the reviewer")


class G2Result(BaseModel):
    reviews: List[G2Review] = Field(default_factory=list, description="Extracted reviews from G2")


def fetch_g2(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
    competitor_products: List[str] | None = None,
    max_review_stars: int = 3,
) -> Iterable[Dict[str, Any]]:
    if categories is None:
        categories = ["seo", "marketing-analytics"]
    if competitor_products is None:
        competitor_products = []

    now = int(time.time())

    for cat in categories:
        url = f"https://www.g2.com/categories/{cat}"
        yield from _scrape_g2_url(url, cat, reviews_per_category, now, max_review_stars)

    for prod in competitor_products:
        url = f"https://www.g2.com/products/{prod}/reviews"
        yield from _scrape_g2_url(url, prod, reviews_per_category, now, max_review_stars)


def _scrape_g2_url(url: str, label: str, limit: int, now: int, max_stars: int = 5) -> Iterable[Dict[str, Any]]:
    rows = list(_scrape_g2_with_scrapingbee(url, label, limit, now, max_stars=max_stars))
    if rows:
        yield from rows
        return

    log.info("Fetching G2 via browser-use", extra={"label": label, "url": url})
    try:
        result = run_task(
            f"Go to {url}. Extract up to {limit} user reviews from the page. "
            f"For each review, get: pros (what they liked), cons (what they disliked), "
            f"overall comment, star rating (1-5), reviewer name, and reviewer job title if shown. "
            f"Prioritize reviews with {max_stars} stars or fewer. Return as structured data.",
            output_schema=G2Result,
        )
    except Exception as e:
        log.warning("G2 browser-use failed", extra={"label": label, "error": str(e)})
        return

    if not isinstance(result, G2Result) or not result.reviews:
        log.info("G2 returned no reviews", extra={"label": label})
        return

    for rev in result.reviews:
        parts = []
        if rev.pros:
            parts.append(f"Pros: {rev.pros}")
        if rev.cons:
            parts.append(f"Cons: {rev.cons}")
        if rev.overall_comment:
            parts.append(rev.overall_comment)
        full_text = "\n".join(parts)
        if not full_text or len(full_text.strip()) < 30:
            continue
        if rev.rating and rev.rating > max_stars:
            continue
        yield {
            "url": url,
            "author": rev.reviewer_name,
            "text": full_text.strip(),
            "created_at": now,
            "upvotes": int(rev.rating) if rev.rating else None,
            "comments": None,
        }

    log.info("G2 reviews fetched via browser-use", extra={"label": label, "count": len(result.reviews)})


def _scrape_g2_with_scrapingbee(url: str, label: str, limit: int, now: int, max_stars: int = 5) -> Iterable[Dict[str, Any]]:
    try:
        page_html = fetch_html(url, render_js=True, timeout=120, premium_proxy=True, country_code="us")
    except Exception as e:
        log.warning("G2 ScrapingBee fetch failed", extra={"label": label, "error": str(e)})
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
        log.info("G2 reviews fetched via ScrapingBee", extra={"label": label, "count": count})


def scored_rows(
    categories: List[str] | None = None,
    reviews_per_category: int = 20,
    competitor_products: List[str] | None = None,
    max_review_stars: int = 3,
) -> Iterable[Dict[str, Any]]:
    for item in fetch_g2(
        categories=categories,
        reviews_per_category=reviews_per_category,
        competitor_products=competitor_products,
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
