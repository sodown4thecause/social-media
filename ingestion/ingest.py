from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Tuple

from .config import AppConfig
from .db import connect, init_db, upsert_posts
from .logging_config import log

from .reddit_rss import scored_rows as reddit_rows
from .x_search import scored_rows as x_rows
from .hackernews_rss import scored_rows as hackernews_rows
from .producthunt_rss import scored_rows as producthunt_rows
from .indiehackers_rss import scored_rows as indiehackers_rows
from .capterra_scraper import scored_rows as capterra_rows
from .g2_scraper import scored_rows as g2_rows


def _collect_source(source: str, generator, cfg: AppConfig) -> List[Tuple]:
    """Collect rows from a source generator into the upsert tuple format."""
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
        log.info(f"Fetched from {source}", extra={"count": len(rows)})
    except Exception as e:
        log.warning(f"Source {source} failed", extra={"error": str(e)})
    return rows


def _fetch_all(cfg: AppConfig) -> List[Tuple]:
    """Fetch from all enabled sources in parallel."""
    all_rows: List[Tuple] = []
    tasks: dict = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        # Reddit
        if cfg.reddit.subreddits:
            tasks["reddit"] = pool.submit(
                _collect_source, "reddit",
                reddit_rows(cfg.reddit.subreddits, cfg.reddit.limit_per_feed),
                cfg,
            )

        # X / Nitter
        if cfg.x.search_queries:
            tasks["x"] = pool.submit(
                _collect_source, "x",
                x_rows(cfg.x.nitter_base, cfg.x.search_queries, 20),
                cfg,
            )

        # HackerNews
        if cfg.hackernews.enabled:
            tasks["hackernews"] = pool.submit(
                _collect_source, "hackernews",
                hackernews_rows(limit=cfg.hackernews.limit, min_points=cfg.hackernews.min_points),
                cfg,
            )

        # ProductHunt
        if cfg.producthunt.enabled:
            tasks["producthunt"] = pool.submit(
                _collect_source, "producthunt",
                producthunt_rows(limit=cfg.producthunt.limit, topic=cfg.producthunt.topic),
                cfg,
            )

        # IndieHackers
        if cfg.indiehackers.enabled:
            tasks["indiehackers"] = pool.submit(
                _collect_source, "indiehackers",
                indiehackers_rows(limit=cfg.indiehackers.limit),
                cfg,
            )

        # Capterra
        if cfg.capterra.enabled:
            tasks["capterra"] = pool.submit(
                _collect_source, "capterra",
                capterra_rows(
                    categories=cfg.capterra.categories,
                    reviews_per_category=cfg.capterra.reviews_per_category,
                ),
                cfg,
            )

        # G2
        if cfg.g2.enabled:
            tasks["g2"] = pool.submit(
                _collect_source, "g2",
                g2_rows(
                    categories=cfg.g2.categories,
                    reviews_per_category=cfg.g2.reviews_per_category,
                ),
                cfg,
            )

        for name, future in as_completed(tasks):
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
    log.info("Ingestion complete", extra={"seen": len(all_rows), "inserted": inserted})


if __name__ == "__main__":
    main()
