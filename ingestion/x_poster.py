from __future__ import annotations
import os
from typing import Optional

import requests

from ingestion.config import AppConfig
from ingestion.db import connect, init_db, insert_post_performance, update_post_performance
from ingestion.env_loader import load_local_env
from ingestion.generate_and_score import Post, apply_tracking_url
from ingestion.logging_config import log

GETXAPI_BASE = "https://api.getxapi.com/v1"


def _get_key() -> str:
    load_local_env()
    return os.getenv("GETXAPI_API_KEY", "").strip()


def post_tweet(text: str, reply_to_url: str | None = None, dry_run: bool = True) -> Optional[str]:
    if dry_run:
        log.info("[DRY RUN] Would post tweet", extra={"text": text[:100], "reply_to": reply_to_url})
        return "dry_run:tweet"

    key = _get_key()
    if not key:
        log.warning("No GETXAPI_API_KEY set")
        return None

    url = f"{GETXAPI_BASE}/tweets"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"text": text}
    if reply_to_url:
        body["reply_to_url"] = reply_to_url

    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        tweet_url = data.get("url", data.get("data", {}).get("url", ""))
        log.info("Posted tweet", extra={"url": tweet_url})
        return tweet_url
    except Exception as e:
        log.error("GetXAPI tweet failed", extra={"error": str(e)})
        return None


def post_reply(candidate_id: int, dry_run: bool = True) -> Optional[str]:
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
        log.info("[DRY RUN] Would post to X", extra={
            "candidate_id": candidate_id, "post_url": post_url, "text": text[:100],
        })
        return f"dry_run:x:{post_url}"

    tweet_url = post_tweet(text, reply_to_url=post_url if source == "x" else None, dry_run=False)
    status = "posted" if tweet_url else "failed"
    perf_id = insert_post_performance(con, approval_id, candidate_id, post_id, "x", status)
    if tweet_url and perf_id:
        update_post_performance(con, perf_id, post_url=tweet_url)

    return tweet_url


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Post approved reply to X/Twitter via GetXAPI")
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
