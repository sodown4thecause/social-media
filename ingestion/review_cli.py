from __future__ import annotations
import json
import sys
import time
import sqlite3
from typing import Optional

from .db import connect, init_db


def next_review_item(con: sqlite3.Connection) -> Optional[tuple]:
    cur = con.execute(
        """
        SELECT c.id, p.source, p.url, substr(p.text,1,220), i.cluster, i.confidence, c.tone, c.angle, c.text, c.total_score
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


def approve_flow(db_path: str = "data.sqlite3") -> None:
    con = connect(db_path)
    init_db(con)

    row = next_review_item(con)
    if not row:
        print("No items to review.")
        return

    (cid, source, url, snippet, cluster, conf, tone, angle, text, total) = row

    print("Source:", source)
    print("URL:", url)
    print("Cluster:", cluster, f"({conf:.2f})")
    print("Post snippet:", snippet)
    print("Candidate:")
    print(text)
    print(f"Tone: {tone} | Angle: {angle} | Score: {total}")
    print()
    choice = input("Approve [a], Edit [e], Reject [r], Quit [q]: ").strip().lower()
    if choice == 'q':
        print("Quit.")
        return
    decided_at = int(time.time())

    if choice == 'a':
        con.execute(
            "INSERT INTO approvals(candidate_id, decision, decided_at, channel) VALUES (?, 'approved', ?, ?)",
            (cid, decided_at, source),
        )
        con.commit()
        print("Approved.")
    elif choice == 'e':
        edited = input("Edit text (empty to cancel): ").strip()
        if not edited:
            print("Canceled.")
            return
        con.execute(
            "INSERT INTO approvals(candidate_id, decision, edited_text, decided_at, channel) VALUES (?, 'edited', ?, ?, ?)",
            (cid, edited, decided_at, source),
        )
        con.commit()
        print("Saved edited text.")
        print("Copy this for manual posting:\n")
        print(edited)
    else:
        con.execute(
            "INSERT INTO approvals(candidate_id, decision, decided_at, channel) VALUES (?, 'rejected', ?, ?)",
            (cid, decided_at, source),
        )
        con.commit()
        print("Rejected.")


if __name__ == '__main__':
    approve_flow()
