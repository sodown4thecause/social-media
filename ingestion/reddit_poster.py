from __future__ import annotations
import os
import time
from typing import Optional

import praw
from praw.exceptions import RedditAPIException

from ingestion.config import AppConfig, PostingConfig
from ingestion.db import connect, init_db, insert_post_performance, update_post_performance
from ingestion.env_loader import load_local_env
from ingestion.generate_and_score import Post, apply_tracking_url
from ingestion.logging_config import log


def _get_reddit(config: PostingConfig) -> praw.Reddit:
    load_local_env()
    return praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID", ""),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        username=os.getenv("REDDIT_USERNAME", ""),
        password=os.getenv("REDDIT_PASSWORD", ""),
        user_agent="LeanGrowthIntelligence/1.0",
    )


def post_reply(candidate_id: int, dry_run: bool = True, config: PostingConfig | None = None) -> Optional[str]:
    if config is None:
        cfg = AppConfig.from_file()
        config = cfg.posting

    if not config.enabled and not dry_run:
        log.warning("Posting is disabled in config. Enable posting.enabled to post.")
        return None

    cfg = AppConfig.from_file()
    con = connect(cfg.db_path)
    init_db(con)

    cur = con.execute(
        """
        SELECT COALESCE(a.edited_text, c.text), p.url, p.id as post_id, a.id as approval_id,
               p.source, p.text as post_text, i.cluster, i.confidence, c.angle
        FROM candidates c
        JOIN posts p ON p.id = c.post_id
        JOIN intents i ON i.post_id = p.id
        JOIN approvals a ON a.candidate_id = c.id
        WHERE c.id = ? AND a.decision IN ('approved', 'edited')
        """,
        (candidate_id,),
    )
    row = cur.fetchone()
    if not row:
        log.warning("No approved candidate found", extra={"candidate_id": candidate_id})
        return None

    text, post_url, post_id, approval_id, source, post_text, cluster, confidence, angle = row
    text = apply_tracking_url(text, Post(post_id, source, post_text, cluster, confidence), angle)

    if dry_run:
        log.info("[DRY RUN] Would post reply", extra={
            "candidate_id": candidate_id, "post_url": post_url, "text": text[:100],
        })
        return f"dry_run:{post_url}"

    reddit = _get_reddit(config)
    try:
        submission = reddit.submission(url=post_url)
        comment = submission.reply(text)
        posted_url = f"https://reddit.com{comment.permalink}"

        perf_id = insert_post_performance(con, approval_id, candidate_id, post_id, "reddit", "posted")
        update_post_performance(con, perf_id, post_url=posted_url)

        log.info("Posted to Reddit", extra={
            "candidate_id": candidate_id, "url": posted_url,
        })
        return posted_url

    except RedditAPIException as e:
        log.error("Reddit API error", extra={"error": str(e), "candidate_id": candidate_id})
        perf_id = insert_post_performance(con, approval_id, candidate_id, post_id, "reddit", "failed")
        return None
    except Exception as e:
        log.error("Reddit posting failed", extra={"error": str(e), "candidate_id": candidate_id})
        return None


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Post approved reply to Reddit")
    parser.add_argument("candidate_id", type=int, help="Candidate ID to post")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry run mode")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="Actually post")
    args = parser.parse_args()
    result = post_reply(args.candidate_id, dry_run=args.dry_run)
    if result:
        print(f"Result: {result}")
    else:
        print("Posting failed.")


if __name__ == "__main__":
    main()
