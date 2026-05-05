from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from html import unescape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ingestion.db import connect, init_db, insert_lead, lead_exists_for_post, update_lead_status
from ingestion.lead_scoring import compute_lead_score
from ingestion.source_health import source_health

ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = Path(__file__).resolve().parent / "static"
DB_PATH = ROOT / "data.sqlite3"
TAG_RE = re.compile(r"<[^>]+>")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
WS_RE = re.compile(r"\s+")
PIPELINE_LOCK = threading.Lock()
PIPELINE_STATE: dict[str, Any] = {
    "running": False,
    "stage": None,
    "last_run": None,
    "last_error": None,
    "history": [],
}
PIPELINE_STAGES = {
    "ingest": ("Ingest sources", lambda: __import__("ingestion.ingest", fromlist=["main"]).main()),
    "intents": ("Compute intents", lambda: __import__("ingestion.compute_intents", fromlist=["classify_posts"]).classify_posts()),
    "generate": ("Generate replies", lambda: __import__("ingestion.generate_and_score", fromlist=["generate_and_score"]).generate_and_score()),
    "enrich": ("Enrich evidence", lambda: __import__("ingestion.enrich", fromlist=["enrich_once"]).enrich_once()),
}
FULL_PIPELINE = ["ingest", "intents", "generate", "enrich"]


def get_con() -> sqlite3.Connection:
    con = connect(str(DB_PATH))
    init_db(con)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def db_conn():
    con = get_con()
    try:
        yield con
    finally:
        con.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def clean_signal_text(text: str | None) -> str:
    if not text:
        return ""
    cleaned = COMMENT_RE.sub(" ", text)
    cleaned = TAG_RE.sub(" ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = cleaned.replace(" submitted by ", " ")
    cleaned = WS_RE.sub(" ", cleaned)
    return cleaned.strip()


def contact_strategy(source: str) -> dict[str, str]:
    strategies = {
        "reddit": ("Public reply", "Reply in-thread after human review. Avoid cold DMs unless invited."),
        "x": ("Public reply", "Reply publicly or save for manual profile research."),
        "producthunt": ("Community comment", "Comment on the launch/review when it helps the discussion."),
        "indiehackers": ("Community comment", "Comment publicly or research the founder profile manually."),
        "hackernews": ("Public comment", "Comment only when the thread is active and the answer is useful."),
        "g2": ("Research lead", "No native DM. Use reviewer/company clues for account research."),
        "capterra": ("Research lead", "No native DM. Use review context to shape account outreach elsewhere."),
    }
    label, guidance = strategies.get(source, ("Research lead", "Save the signal and research a contact path manually."))
    return {"label": label, "guidance": guidance}


def source_list(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT DISTINCT source FROM posts ORDER BY source").fetchall()
    return [r[0] for r in rows]


def summary() -> dict[str, Any]:
    with db_conn() as con:
        stats = {
            "posts": con.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
            "pending": con.execute(
                """
                SELECT COUNT(*)
                FROM candidates c
                LEFT JOIN approvals a ON a.candidate_id = c.id
                WHERE a.candidate_id IS NULL
                """
            ).fetchone()[0],
            "approved": con.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'approved'").fetchone()[0],
            "edited": con.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'edited'").fetchone()[0],
            "rejected": con.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'rejected'").fetchone()[0],
            "leads": con.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "high_value_leads": con.execute("SELECT COUNT(*) FROM leads WHERE lead_score >= 50").fetchone()[0],
            "enrichment_cost_cents": con.execute("SELECT COALESCE(SUM(cost_cents), 0) FROM enrichments").fetchone()[0],
        }
        by_source = [dict(r) for r in con.execute(
            "SELECT source, COUNT(*) AS count FROM posts GROUP BY source ORDER BY count DESC"
        ).fetchall()]
        by_intent = [dict(r) for r in con.execute(
            """
            SELECT cluster, COUNT(*) AS count, AVG(confidence) AS confidence
            FROM intents
            GROUP BY cluster
            ORDER BY count DESC, confidence DESC
            LIMIT 10
            """
        ).fetchall()]
        return {"stats": stats, "sources": source_list(con), "by_source": by_source, "by_intent": by_intent}


def pipeline_status() -> dict[str, Any]:
    with PIPELINE_LOCK:
        return {
            "running": PIPELINE_STATE["running"],
            "stage": PIPELINE_STATE["stage"],
            "last_run": PIPELINE_STATE["last_run"],
            "last_error": PIPELINE_STATE["last_error"],
            "history": list(PIPELINE_STATE["history"][-8:]),
        }


def _record_pipeline_event(stage: str, status: str, started_at: int, error: str | None = None) -> None:
    event = {
        "stage": stage,
        "status": status,
        "started_at": started_at,
        "finished_at": int(time.time()),
        "error": error,
    }
    with PIPELINE_LOCK:
        PIPELINE_STATE["history"].append(event)
        PIPELINE_STATE["history"] = PIPELINE_STATE["history"][-20:]
        PIPELINE_STATE["last_run"] = event
        PIPELINE_STATE["last_error"] = error


def _run_pipeline_job(stages: list[str]) -> None:
    try:
        for stage in stages:
            label, fn = PIPELINE_STAGES[stage]
            started_at = int(time.time())
            with PIPELINE_LOCK:
                PIPELINE_STATE["stage"] = label
            try:
                fn()
                _record_pipeline_event(label, "ok", started_at)
            except Exception as exc:
                _record_pipeline_event(label, "error", started_at, f"{type(exc).__name__}: {exc}")
                break
    finally:
        with PIPELINE_LOCK:
            PIPELINE_STATE["running"] = False
            PIPELINE_STATE["stage"] = None


def start_pipeline(action: str) -> dict[str, Any]:
    if action == "full":
        stages = FULL_PIPELINE
    elif action in PIPELINE_STAGES:
        stages = [action]
    else:
        raise ValueError("unknown pipeline action")

    with PIPELINE_LOCK:
        if PIPELINE_STATE["running"]:
            raise RuntimeError(f"pipeline already running: {PIPELINE_STATE['stage']}")
        PIPELINE_STATE["running"] = True
        PIPELINE_STATE["stage"] = "Starting"
        PIPELINE_STATE["last_error"] = None

    thread = threading.Thread(target=_run_pipeline_job, args=(stages,), daemon=True)
    thread.start()
    return {"ok": True, "action": action, "stages": stages}


def inbox(params: dict[str, list[str]]) -> list[dict[str, Any]]:
    source = params.get("source", ["all"])[0]
    q = params.get("q", [""])[0].strip()
    min_score = float(params.get("min_score", ["0"])[0] or 0)
    limit = min(int(params.get("limit", ["60"])[0] or 60), 200)

    where = ["a.candidate_id IS NULL", "c.total_score >= ?"]
    values: list[Any] = [min_score]
    if source != "all":
        where.append("p.source = ?")
        values.append(source)
    if q:
        where.append("(p.text LIKE ? OR i.cluster LIKE ? OR c.text LIKE ? OR p.author LIKE ?)")
        needle = f"%{q}%"
        values.extend([needle, needle, needle, needle])
    values.append(limit)

    with db_conn() as con:
        rows = con.execute(
            f"""
            SELECT c.id AS candidate_id, p.id AS post_id, p.source, p.author, p.url,
                   p.text AS post_text, p.created_at, p.engagement,
                   i.cluster, i.confidence, c.angle, c.total_score,
                   EXISTS(SELECT 1 FROM enrichments e WHERE e.post_id = p.id) AS enriched,
                   EXISTS(SELECT 1 FROM sentiment s WHERE s.post_id = p.id AND s.sentiment = 'negative') AS negative_sentiment
            FROM candidates c
            JOIN posts p ON p.id = c.post_id
            JOIN intents i ON i.post_id = p.id
            LEFT JOIN approvals a ON a.candidate_id = c.id
            WHERE {" AND ".join(where)}
            ORDER BY
              CASE i.cluster
                WHEN 'looking for alternative' THEN 0
                WHEN 'SEO stack fatigue' THEN 1
                WHEN 'pricing transparency' THEN 2
                WHEN 'AI for SEO' THEN 3
                WHEN 'GEO/answer engines' THEN 4
                ELSE 9
              END,
              c.total_score DESC,
              i.confidence DESC
            LIMIT ?
            """,
            tuple(values),
        ).fetchall()

    out = []
    for row in rows:
        item = dict(row)
        item["snippet"] = clean_signal_text(item.pop("post_text"))[:220]
        item["contact"] = contact_strategy(item["source"])
        item["created_label"] = time.strftime("%b %d, %Y", time.localtime(item["created_at"]))
        out.append(item)
    return out


def candidate_detail(candidate_id: int) -> dict[str, Any]:
    with db_conn() as con:
        row = con.execute(
            """
            SELECT c.id AS candidate_id, c.post_id, c.tone, c.angle, c.text AS candidate_text,
                   c.score_breakdown, c.total_score,
                   p.source, p.url, p.author, p.text AS post_text, p.created_at, p.engagement,
                   p.prefilter_score, i.cluster, i.confidence, i.top_alt_clusters,
                   e.perplexity, e.dataforseo, e.firecrawl, e.browser_use, e.cost_cents,
                   l.id AS lead_id, l.status AS lead_status, l.notes AS lead_notes, l.lead_score
            FROM candidates c
            JOIN posts p ON p.id = c.post_id
            JOIN intents i ON i.post_id = p.id
            LEFT JOIN enrichments e ON e.post_id = p.id
            LEFT JOIN leads l ON l.post_id = p.id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError("candidate not found")
        candidates = [dict(r) for r in con.execute(
            "SELECT id, angle, text, total_score FROM candidates WHERE post_id = ? ORDER BY total_score DESC",
            (row["post_id"],),
        ).fetchall()]
        sentiment = [dict(r) for r in con.execute(
            "SELECT target, sentiment, confidence, extracted_quote FROM sentiment WHERE post_id = ? ORDER BY confidence DESC",
            (row["post_id"],),
        ).fetchall()]

    data = dict(row)
    for field in ["score_breakdown", "top_alt_clusters", "perplexity", "dataforseo", "firecrawl", "browser_use"]:
        if data.get(field):
            try:
                data[field] = json.loads(data[field])
            except (TypeError, json.JSONDecodeError):
                pass
    data["post_text"] = clean_signal_text(data.get("post_text"))
    data["contact"] = contact_strategy(data["source"])
    data["other_candidates"] = candidates
    data["sentiment"] = sentiment
    data["created_label"] = time.strftime("%b %d, %Y %H:%M", time.localtime(data["created_at"]))
    return data


def lead_rows(params: dict[str, list[str]]) -> list[dict[str, Any]]:
    status = params.get("status", ["all"])[0]
    min_score = float(params.get("min_score", ["0"])[0] or 0)
    limit = min(int(params.get("limit", ["80"])[0] or 80), 200)
    where = ["l.lead_score >= ?"]
    values: list[Any] = [min_score]
    if status != "all":
        where.append("l.status = ?")
        values.append(status)
    values.append(limit)

    with db_conn() as con:
        rows = con.execute(
            f"""
            SELECT l.*, p.text AS post_text, p.source, p.url, i.cluster
            FROM leads l
            JOIN posts p ON p.id = l.post_id
            LEFT JOIN intents i ON i.post_id = p.id
            WHERE {" AND ".join(where)}
            ORDER BY l.lead_score DESC, l.created_at DESC
            LIMIT ?
            """,
            tuple(values),
        ).fetchall()
    return [
        dict(r) | {"post_text": clean_signal_text(r["post_text"]), "contact": contact_strategy(r["source"])}
        for r in rows
    ]


def record_decision(candidate_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    decision = payload.get("decision")
    if decision not in {"approved", "edited", "rejected"}:
        raise ValueError("decision must be approved, edited, or rejected")

    edited_text = (payload.get("edited_text") or "").strip() if decision == "edited" else None
    note = (payload.get("note") or "").strip() or None
    create_lead = bool(payload.get("create_lead"))
    now = int(time.time())

    with db_conn() as con:
        row = con.execute(
            """
            SELECT c.id AS candidate_id, c.post_id, p.source, p.author, p.url, p.engagement,
                   i.cluster, i.confidence,
                   EXISTS(SELECT 1 FROM enrichments e WHERE e.post_id = p.id) AS has_enrichment,
                   EXISTS(SELECT 1 FROM sentiment s WHERE s.post_id = p.id AND s.sentiment = 'negative') AS has_negative_sentiment
            FROM candidates c
            JOIN posts p ON p.id = c.post_id
            JOIN intents i ON i.post_id = p.id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError("candidate not found")

        con.execute(
            """
            INSERT INTO approvals(candidate_id, decision, edited_text, decided_at, channel, reviewer_note)
            VALUES (?, ?, ?, ?, 'webapp', ?)
            """,
            (candidate_id, decision, edited_text, now, note),
        )

        lead_id = None
        if create_lead and not lead_exists_for_post(con, row["post_id"]):
            score = compute_lead_score(
                intent_cluster=row["cluster"],
                confidence=row["confidence"],
                engagement=row["engagement"] or 0,
                source=row["source"],
                has_negative_sentiment=bool(row["has_negative_sentiment"]),
                has_enrichment=bool(row["has_enrichment"]),
            )
            lead_id = insert_lead(
                con,
                row["post_id"],
                row["source"],
                row["author"],
                row["url"],
                row["cluster"],
                row["confidence"],
                score,
                now,
            )
        con.commit()
    return {"ok": True, "candidate_id": candidate_id, "lead_id": lead_id}


def change_lead_status(lead_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    if status not in {"new", "contacted", "converted", "lost", "ignored"}:
        raise ValueError("invalid lead status")
    with db_conn() as con:
        update_lead_status(con, lead_id, status, (payload.get("notes") or "").strip() or None)
    return {"ok": True, "lead_id": lead_id}


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/summary":
                self.send_json(summary())
            elif parsed.path == "/api/source-health":
                params = parse_qs(parsed.query)
                deep = params.get("deep", ["false"])[0].lower() in {"1", "true", "yes"}
                self.send_json(source_health(include_browser_sources=deep))
            elif parsed.path == "/api/pipeline/status":
                self.send_json(pipeline_status())
            elif parsed.path == "/api/inbox":
                self.send_json(inbox(parse_qs(parsed.query)))
            elif parsed.path.startswith("/api/candidates/"):
                candidate_id = int(parsed.path.rsplit("/", 1)[-1])
                self.send_json(candidate_detail(candidate_id))
            elif parsed.path == "/api/leads":
                self.send_json(lead_rows(parse_qs(parsed.query)))
            else:
                self.send_static(parsed.path)
        except KeyError as exc:
            self.send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/decision"):
                candidate_id = int(parsed.path.split("/")[-2])
                self.send_json(record_decision(candidate_id, payload))
            elif parsed.path.startswith("/api/leads/") and parsed.path.endswith("/status"):
                lead_id = int(parsed.path.split("/")[-2])
                self.send_json(change_lead_status(lead_id, payload))
            elif parsed.path == "/api/pipeline/run":
                self.send_json(start_pipeline(str(payload.get("action") or "")))
            else:
                self.send_error_json("route not found", HTTPStatus.NOT_FOUND)
        except RuntimeError as exc:
            self.send_error_json(str(exc), HTTPStatus.CONFLICT)
        except ValueError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except KeyError as exc:
            self.send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: HTTPStatus) -> None:
        self.send_json({"error": message}, status)

    def send_static(self, path: str) -> None:
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        file_path = (STATIC_ROOT / rel).resolve()
        if STATIC_ROOT.resolve() not in file_path.parents and file_path != STATIC_ROOT.resolve():
            self.send_error_json("invalid path", HTTPStatus.BAD_REQUEST)
            return
        if not file_path.exists() or not file_path.is_file():
            file_path = STATIC_ROOT / "index.html"
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the FlowIntent lead radar web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    try:
        print(f"FlowIntent Lead Radar running at http://{args.host}:{args.port}", flush=True)
    except Exception:
        pass
    server.serve_forever()


if __name__ == "__main__":
    main()
