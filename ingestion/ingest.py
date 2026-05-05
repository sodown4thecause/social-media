from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from .config import AppConfig
from .db import connect, init_db, upsert_posts
from .logging_config import log
from .retry_utils import CircuitBreaker
from .metrics import incr, record_run

from .reddit_rss import scored_rows as reddit_rows
from .x_search import scored_rows as x_rows
from .hackernews_rss import scored_rows as hackernews_rows
from .producthunt_rss import scored_rows as producthunt_rows
from .indiehackers_rss import scored_rows as indiehackers_rows
from .capterra_scraper import scored_rows as capterra_rows
from .g2_scraper import scored_rows as g2_rows


def _collect_source(source: str, generator, cfg: AppConfig, breaker: CircuitBreaker | None = None) -> List[Tuple]:
    """Collect rows from a source generator, respecting circuit breaker states."""
    if breaker and breaker.is_open:
        log.warning(f"Circuit breaker open for {source}, skipping")
        return []

    rows: List[Tuple] = []
    try:
        for item in generator:
            rows.append((
                source,
                item["url"],
                item.get("author"),
                item["norm_text"],
                int(item["created_at"]),
                float(item.get("engagement") or 0.0),
                item["hash"],
                float(item["recency_score"]),
                float(item["prefilter_score"]),
            ))
        if breaker:
            breaker.success()
        log.info(f"Fetched from {source}", extra={"count": len(rows)})
    except Exception as e:
        log.warning(f"Source {source} failed", extra={"error": str(e)})
        if breaker:
            breaker.failure()
    return rows


def _fetch_all(cfg: AppConfig) -> List[Tuple]:
    """Fetch from all enabled sources in parallel."""
    all_rows: List[Tuple] = []
    future_to_name: dict = {}
    breakers: Dict[str, CircuitBreaker] = {
        name: CircuitBreaker(name, cfg.retry.circuit_breaker_failures, cfg.retry.circuit_breaker_cooldown_minutes * 60)
        for name in ["reddit", "x", "hackernews", "producthunt", "indiehackers", "capterra", "g2"]
    }

    with ThreadPoolExecutor(max_workers=6) as pool:
        # Reddit
        if cfg.reddit.subreddits:
            future = pool.submit(
                _collect_source, "reddit",
                reddit_rows(cfg.reddit.subreddits, cfg.reddit.limit_per_feed),
                cfg, breakers["reddit"],
            )
            future_to_name[future] = "reddit"

        # X / Nitter
        if cfg.x.search_queries:
            x_bases = [cfg.x.nitter_base, *cfg.x.nitter_fallbacks]
            future = pool.submit(
                _collect_source, "x",
                x_rows(
                    x_bases,
                    cfg.x.search_queries,
                    20,
                    max_queries=cfg.x.max_queries_per_run,
                    getxapi_product=cfg.x.getxapi_product,
                ),
                cfg, breakers["x"],
            )
            future_to_name[future] = "x"

        # HackerNews
        if cfg.hackernews.enabled:
            future = pool.submit(
                _collect_source, "hackernews",
                hackernews_rows(limit=cfg.hackernews.limit, min_points=cfg.hackernews.min_points),
                cfg, breakers["hackernews"],
            )
            future_to_name[future] = "hackernews"

        # ProductHunt
        if cfg.producthunt.enabled:
            future = pool.submit(
                _collect_source, "producthunt",
                producthunt_rows(
                    limit=cfg.producthunt.limit,
                    topic=cfg.producthunt.topic,
                    secondary_topic=cfg.producthunt.secondary_topic,
                ),
                cfg, breakers["producthunt"],
            )
            future_to_name[future] = "producthunt"

        # IndieHackers
        if cfg.indiehackers.enabled:
            future = pool.submit(
                _collect_source, "indiehackers",
                indiehackers_rows(limit=cfg.indiehackers.limit),
                cfg, breakers["indiehackers"],
            )
            future_to_name[future] = "indiehackers"

        # Capterra
        if cfg.capterra.enabled:
            future = pool.submit(
                _collect_source, "capterra",
                capterra_rows(
                    categories=cfg.capterra.categories,
                    reviews_per_category=cfg.capterra.reviews_per_category,
                    competitor_slugs=cfg.capterra.competitor_slugs,
                    max_review_stars=cfg.capterra.max_review_stars,
                ),
                cfg, breakers["capterra"],
            )
            future_to_name[future] = "capterra"

        # G2
        if cfg.g2.enabled:
            future = pool.submit(
                _collect_source, "g2",
                g2_rows(
                    categories=cfg.g2.categories,
                    reviews_per_category=cfg.g2.reviews_per_category,
                    competitor_products=cfg.g2.competitor_products,
                    max_review_stars=cfg.g2.max_review_stars,
                ),
                cfg, breakers["g2"],
            )
            future_to_name[future] = "g2"

        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                rows = future.result()
                all_rows.extend(rows)
                log.info(f"{name} completed", extra={"count": len(rows)})
            except Exception as e:
                log.warning(f"{name} failed", extra={"error": str(e)})

    return all_rows


def main() -> None:
    cfg = AppConfig.from_file()
    con = connect(cfg.db_path)
    init_db(con)

    log.info("Ingestion started", extra={
        "sources": {
            "reddit": bool(cfg.reddit.subreddits),
            "x": bool(cfg.x.search_queries),
            "hackernews": cfg.hackernews.enabled,
            "producthunt": cfg.producthunt.enabled,
            "indiehackers": cfg.indiehackers.enabled,
            "capterra": cfg.capterra.enabled,
            "g2": cfg.g2.enabled,
        }
    })

    all_rows = _fetch_all(cfg)

    if not all_rows:
        log.info("No rows fetched from any source")
        return

    inserted = upsert_posts(con, all_rows)
    incr("posts_ingested", inserted)
    record_run("ingest")
    log.info("Ingestion complete", extra={"seen": len(all_rows), "inserted": inserted})


if __name__ == "__main__":
    main()
