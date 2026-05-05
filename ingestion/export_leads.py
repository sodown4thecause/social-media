from __future__ import annotations
import argparse
import csv
import sys
import time
from pathlib import Path

from ingestion.config import AppConfig
from ingestion.db import connect, init_db, load_leads


def export_leads_csv(output_path: str, status: str | None = None, min_score: float = 0,
                     since_ts: int | None = None, limit: int = 500) -> int:
    cfg = AppConfig.from_file()
    con = connect(cfg.db_path)
    init_db(con)

    rows = load_leads(con, status=status, min_score=min_score, limit=limit)
    if since_ts:
        rows = [r for r in rows if r["created_at"] >= since_ts]

    if not rows:
        print("No leads match the filters.")
        return 0

    fieldnames = ["id", "post_id", "source", "author", "url", "intent_cluster",
                  "confidence", "lead_score", "status", "notes", "created_at", "updated_at"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            d = dict(row)
            d["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(d["created_at"]))
            d["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(d["updated_at"]))
            writer.writerow(d)

    print(f"Exported {len(rows)} leads to {output_path}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export leads to CSV")
    parser.add_argument("--output", "-o", default="leads_export.csv", help="Output CSV path")
    parser.add_argument("--status", "-s", default=None, help="Filter by status (new/contacted/converted/lost/ignored)")
    parser.add_argument("--min-score", type=float, default=0, help="Minimum lead score")
    parser.add_argument("--since-days", type=int, default=None, help="Only leads from last N days")
    parser.add_argument("--limit", type=int, default=500, help="Max leads to export")
    args = parser.parse_args()

    since_ts = None
    if args.since_days:
        since_ts = int(time.time()) - args.since_days * 86400

    export_leads_csv(
        output_path=args.output,
        status=args.status,
        min_score=args.min_score,
        since_ts=since_ts,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
