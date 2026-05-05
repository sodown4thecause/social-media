from __future__ import annotations

import itertools
import os
import traceback
from typing import Any, Callable, Iterable

from .config import AppConfig
from .env_loader import load_local_env
from .reddit_rss import scored_rows as reddit_rows
from .x_search import scored_rows as x_rows
from .hackernews_rss import scored_rows as hackernews_rows
from .producthunt_rss import scored_rows as producthunt_rows
from .indiehackers_rss import scored_rows as indiehackers_rows
from .capterra_scraper import scored_rows as capterra_rows
from .g2_scraper import scored_rows as g2_rows


def _key_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _sample(row: dict[str, Any]) -> dict[str, Any]:
    text = row.get("norm_text") or row.get("text") or ""
    return {
        "url": row.get("url"),
        "author": row.get("author"),
        "text": text[:180],
        "score": row.get("prefilter_score"),
    }


def _collect(factory: Callable[[], Iterable[dict[str, Any]]], required_env: str | None = None, limit: int = 2) -> dict[str, Any]:
    if required_env and not _key_present(required_env):
        return {"status": "skipped", "reason": f"missing {required_env}", "count": 0, "samples": []}
    try:
        rows = list(itertools.islice(factory(), limit))
        return {
            "status": "ok" if rows else "empty",
            "count": len(rows),
            "samples": [_sample(row) for row in rows],
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=2),
            "count": 0,
            "samples": [],
        }


def provider_status() -> dict[str, bool]:
    load_local_env()
    return {
        "JINA_API_KEY": _key_present("JINA_API_KEY"),
        "XAI_API_KEY": _key_present("XAI_API_KEY"),
        "PERPLEXITY_API_KEY": _key_present("PERPLEXITY_API_KEY"),
        "FIRECRAWL_API_KEY": _key_present("FIRECRAWL_API_KEY"),
        "BROWSER_USE_API_KEY": _key_present("BROWSER_USE_API_KEY"),
        "BROWSERBASE_API_KEY": _key_present("BROWSERBASE_API_KEY"),
        "SCRAPINGBEE_API_KEY": _key_present("SCRAPINGBEE_API_KEY"),
        "REDDIT_CLIENT_ID": _key_present("REDDIT_CLIENT_ID"),
        "REDDIT_CLIENT_SECRET": _key_present("REDDIT_CLIENT_SECRET"),
        "GETXAPI_API_KEY": _key_present("GETXAPI_API_KEY"),
        "SUPADATA_API_KEY": _key_present("SUPADATA_API_KEY"),
    }


def source_health(limit: int = 2, include_browser_sources: bool = False) -> dict[str, Any]:
    cfg = AppConfig.from_file()
    x_bases = [cfg.x.nitter_base, *cfg.x.nitter_fallbacks]
    x_probe = next((q for q in cfg.x.search_queries if "alternative" in q.lower()), cfg.x.search_queries[0])
    checks: dict[str, Any] = {
        "reddit": _collect(lambda: reddit_rows([cfg.reddit.subreddits[0]], limit), limit=limit),
        "x": _collect(lambda: x_rows(x_bases, [x_probe], limit, max_queries=1, getxapi_product=cfg.x.getxapi_product), limit=limit),
        "hackernews": _collect(lambda: hackernews_rows(limit=limit, min_points=0), limit=limit),
        "producthunt": _collect(
            lambda: producthunt_rows(limit=limit, topic=cfg.producthunt.topic, secondary_topic=None),
            limit=limit,
        ),
        "indiehackers": _collect(lambda: indiehackers_rows(limit=limit), limit=limit),
    }

    if include_browser_sources:
        checks["capterra"] = _collect(
            lambda: capterra_rows(
                categories=[],
                reviews_per_category=1,
                competitor_slugs=cfg.capterra.competitor_slugs[:1],
                max_review_stars=cfg.capterra.max_review_stars,
            ),
            required_env="SCRAPINGBEE_API_KEY",
            limit=1,
        )
        checks["g2"] = _collect(
            lambda: g2_rows(
                categories=[],
                reviews_per_category=1,
                competitor_products=cfg.g2.competitor_products[:1],
                max_review_stars=cfg.g2.max_review_stars,
            ),
            required_env="SCRAPINGBEE_API_KEY",
            limit=1,
        )
    else:
        checks["capterra"] = {"status": "manual", "reason": "review-site source; run deep check to spend ScrapingBee credits", "count": 0, "samples": []}
        checks["g2"] = {"status": "manual", "reason": "review-site source; run deep check to spend ScrapingBee credits", "count": 0, "samples": []}

    return {"providers": provider_status(), "sources": checks}
