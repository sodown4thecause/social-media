from __future__ import annotations
import json
import sys
import time
import sqlite3
from typing import Optional, List

from .db import connect, init_db


def next_review_item(con: sqlite3.Connection) -> Optional[tuple]:
    cur = con.execute(
        """
        SELECT c.id, p.id, p.source, p.url, substr(p.text,1,220), i.cluster, i.confidence, c.tone, c.angle, c.text, c.total_score
        FROM candidates c
        JOIN posts p ON p.id = c.post_id
        JOIN intents i ON i.post_id = p.id
        LEFT JOIN approvals a ON a.candidate_id = c.id
        WHERE a.candidate_id IS NULL
        ORDER BY c.total_score DESC, i.confidence DESC
        LIMIT 1
        """
    )
    return cur.fetchone()


def stats(con: sqlite3.Connection) -> dict:
    total = con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    pending = con.execute(
        "SELECT COUNT(*) FROM candidates c LEFT JOIN approvals a ON a.candidate_id = c.id WHERE a.candidate_id IS NULL"
    ).fetchone()[0]
    approved = con.execute(
        "SELECT COUNT(DISTINCT candidate_id) FROM approvals WHERE decision = 'approved'"
    ).fetchone()[0]
    rejected = con.execute(
        "SELECT COUNT(DISTINCT candidate_id) FROM approvals WHERE decision = 'rejected'"
    ).fetchone()[0]
    edited = con.execute(
        "SELECT COUNT(DISTINCT candidate_id) FROM approvals WHERE decision = 'edited'"
    ).fetchone()[0]
    return {"total": total, "pending": pending, "approved": approved, "rejected": rejected, "edited": edited}


def listing(con: sqlite3.Connection) -> List[tuple]:
    rows = con.execute(
        """
        SELECT c.id, p.source, substr(p.text,1,80), i.cluster, c.angle, c.total_score
        FROM candidates c
        JOIN posts p ON p.id = c.post_id
        JOIN intents i ON i.post_id = p.id
        LEFT JOIN approvals a ON a.candidate_id = c.id
        WHERE a.candidate_id IS NULL
        ORDER BY c.total_score DESC
        LIMIT 40
        """
    ).fetchall()
    return rows


def approve_flow(db_path: str = "data.sqlite3") -> None:
    con = connect(db_path)
    init_db(con)

    while True:
        print("\n--- Review CLI ---")
        print("[n] Next item  [l] List pending  [s] Stats  [q] Quit")
        cmd = input("> ").strip().lower()

        if cmd == "q":
            break
        elif cmd == "s":
            s = stats(con)
            print(f"\nTotal: {s['total']}  Pending: {s['pending']}  Approved: {s['approved']}  Rejected: {s['rejected']}  Edited: {s['edited']}")
            continue
        elif cmd == "l":
            rows = listing(con)
            if not rows:
                print("No pending items.")
                continue
            print(f"\n{'#':>3} {'ID':>4} {'Source':<12} {'Snippet':<40} {'Cluster':<22} {'Angle':<12} {'Score':>6}")
            print("-" * 110)
            for i, r in enumerate(rows, 1):
                cid, source, snippet, cluster, angle, total = r
                snippet = (snippet or "")[:38]
                cluster = (cluster or "")[:20]
                angle = (angle or "")[:10]
                print(f"{i:>3} {cid:>4} {source:<12} {snippet:<40} {cluster:<22} {angle:<12} {total:>6.2f}")
            continue
        elif cmd == "n":
            row = next_review_item(con)
            if not row:
                print("No items to review.")
                continue

            cid, post_id, source, url, snippet, cluster, conf, tone, angle, text, total = row
            print(f"\nSource: {source}  |  URL: {url}")
            print(f"Cluster: {cluster} ({conf:.2f})  |  Tone: {tone}  |  Angle: {angle}  |  Score: {total}")
            print(f"Post: {snippet}")
            print(f"\nCandidate:")
            print(text)
            print()
            choice = input("Approve [a], Edit [e], Reject [r], Skip [s]: ").strip().lower()
            if choice == "s":
                continue

            decided_at = int(time.time())

            if choice == "a":
                note = input("Note (optional, Enter to skip): ").strip()
                con.execute(
                    "INSERT INTO approvals(candidate_id, decision, decided_at, channel, reviewer_note) VALUES (?, 'approved', ?, ?, ?)",
                    (cid, decided_at, source, note or None),
                )
                con.commit()
                print("Approved.")

                mark_lead = input("Mark author as lead? [y/N]: ").strip().lower()
                if mark_lead == "y":
                    _create_lead_from_review(con, post_id=post_id, source=source, cluster=cluster, conf=conf)
            elif choice == "e":
                edited = input("Edit text (empty to cancel): ").strip()
                if not edited:
                    print("Canceled.")
                    continue
                note = input("Note (optional): ").strip()
                con.execute(
                    "INSERT INTO approvals(candidate_id, decision, edited_text, decided_at, channel, reviewer_note) VALUES (?, 'edited', ?, ?, ?, ?)",
                    (cid, edited, decided_at, source, note or None),
                )
                con.commit()
                print("Saved edited text.")
                print("Copy this for manual posting:\n")
                print(edited)
            elif choice == "r":
                con.execute(
                    "INSERT INTO approvals(candidate_id, decision, decided_at, channel) VALUES (?, 'rejected', ?, ?)",
                    (cid, decided_at, source),
                )
                con.commit()
                print("Rejected.")
        elif cmd == "b":
            # Batch approve by entering IDs
            ids_str = input("Enter candidate IDs (comma-separated or ranges like 1-5): ").strip()
            ids = _parse_ids(ids_str)
            if not ids:
                print("No valid IDs.")
                continue
            decided_at = int(time.time())
            count = 0
            for cid in ids:
                if _is_pending(con, cid):
                    con.execute(
                        "INSERT INTO approvals(candidate_id, decision, decided_at, channel) VALUES (?, 'approved', ?, 'batch')",
                        (cid, decided_at),
                    )
                    count += 1
            con.commit()
            print(f"Batch approved {count} candidates.")
        else:
            print(f"Unknown command: {cmd}")


def _parse_ids(s: str) -> List[int]:
    ids: List[int] = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                ids.extend(range(int(lo.strip()), int(hi.strip()) + 1))
            except ValueError:
                continue
        else:
            try:
                ids.append(int(part))
            except ValueError:
                continue
    return ids


def _is_pending(con: sqlite3.Connection, cid: int) -> bool:
    row = con.execute("SELECT 1 FROM approvals WHERE candidate_id = ?", (cid,)).fetchone()
    return row is None


def _create_lead_from_review(con, post_id, source, cluster, conf):
    from ingestion.db import insert_lead, lead_exists_for_post
    from ingestion.lead_scoring import compute_lead_score

    if post_id and lead_exists_for_post(con, post_id):
        print("Lead already exists for this post.")
        return

    cur = con.execute("SELECT url, author FROM posts WHERE id = ?", (post_id,))
    row = cur.fetchone()
    if not row:
        return
    url, author = row

    score = compute_lead_score(
        intent_cluster=cluster,
        confidence=conf,
        engagement=0,
        source=source,
    )
    now = int(time.time())
    lid = insert_lead(con, post_id, source, author, url, cluster, conf, score, now)
    print(f"Lead created (id={lid}, score={score})")


if __name__ == "__main__":
    approve_flow()
