import sqlite3
import time as _time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  url TEXT NOT NULL,
  author TEXT,
  text TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  engagement REAL DEFAULT 0,
  hash TEXT NOT NULL UNIQUE,
  recency_score REAL DEFAULT 0,
  prefilter_score REAL DEFAULT 0
);

-- Enrichments (external evidence & cost tracking)
CREATE TABLE IF NOT EXISTS enrichments (
  post_id INTEGER PRIMARY KEY,
  perplexity JSON,
  dataforseo JSON,
  firecrawl JSON,
  browser_use JSON,
  cost_cents INTEGER DEFAULT 0,
  fetched_at INTEGER NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_posts_source_created ON posts(source, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_prefilter ON posts(prefilter_score DESC);

-- Embeddings for posts
CREATE TABLE IF NOT EXISTS embeddings (
  post_id INTEGER PRIMARY KEY,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  vector TEXT NOT NULL, -- JSON array of floats
  created_at INTEGER NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

-- Intent seeds (exemplar texts and their vectors)
CREATE TABLE IF NOT EXISTS intent_seeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster TEXT NOT NULL,
  text TEXT NOT NULL,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  vector TEXT NOT NULL, -- JSON array of floats
  UNIQUE(cluster, text, model)
);
CREATE INDEX IF NOT EXISTS idx_intent_seeds_cluster ON intent_seeds(cluster);

-- Intent classification results
CREATE TABLE IF NOT EXISTS intents (
  post_id INTEGER PRIMARY KEY,
  cluster TEXT NOT NULL,
  confidence REAL NOT NULL,
  top_alt_clusters TEXT NOT NULL, -- JSON array [{cluster, score}]
  decided_at INTEGER NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

-- Reply candidates
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  tone TEXT NOT NULL,
  angle TEXT NOT NULL,
  text TEXT NOT NULL,
  score_breakdown TEXT NOT NULL, -- JSON
  total_score REAL NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_candidates_post ON candidates(post_id);

-- Human approvals
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('approved','edited','rejected')),
  edited_text TEXT,
  decided_at INTEGER NOT NULL,
  channel TEXT,
  reviewer_note TEXT,
  FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
);

-- Sentiment analysis results
CREATE TABLE IF NOT EXISTS sentiment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  target TEXT NOT NULL,
  sentiment TEXT NOT NULL CHECK(sentiment IN ('positive','neutral','negative')),
  confidence REAL NOT NULL,
  extracted_quote TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sentiment_target ON sentiment(target);
CREATE INDEX IF NOT EXISTS idx_sentiment_post ON sentiment(post_id);

-- Leads (high-value prospects)
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  author TEXT,
  url TEXT NOT NULL,
  author_bio TEXT,
  intent_cluster TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  lead_score REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','contacted','converted','lost','ignored')),
  notes TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_post ON leads(post_id);

-- Post performance tracking
CREATE TABLE IF NOT EXISTS post_performance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  approval_id INTEGER NOT NULL,
  candidate_id INTEGER NOT NULL,
  post_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  post_url TEXT,
  posted_at INTEGER,
  upvotes_initial INTEGER,
  upvotes_current INTEGER,
  replies_initial INTEGER,
  replies_current INTEGER,
  clicks INTEGER DEFAULT 0,
  conversions INTEGER DEFAULT 0,
  last_polled_at INTEGER,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','posted','failed','deleted')),
  FOREIGN KEY(approval_id) REFERENCES approvals(id) ON DELETE CASCADE,
  FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pp_candidate ON post_performance(candidate_id);
CREATE INDEX IF NOT EXISTS idx_pp_status ON post_performance(status);
CREATE INDEX IF NOT EXISTS idx_pp_platform ON post_performance(platform);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    # Migration: add browser_use column to enrichments if missing
    try:
        con.execute("ALTER TABLE enrichments ADD COLUMN browser_use JSON")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE approvals ADD COLUMN reviewer_note TEXT")
    except sqlite3.OperationalError:
        pass


def upsert_posts(con: sqlite3.Connection, rows: Sequence[Tuple[str, str, Optional[str], str, int, float, str, float, float]]) -> int:
    """
    Insert-ignore by unique hash. Returns number of rows newly inserted.
    Row = (source, url, author, text, created_at, engagement, hash, recency_score, prefilter_score)
    """
    cur = con.cursor()
    cur.executemany(
        """
        INSERT OR IGNORE INTO posts
          (source, url, author, text, created_at, engagement, hash, recency_score, prefilter_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.commit()
    return cur.rowcount


def top_candidates(con: sqlite3.Connection, limit: int = 50, min_prefilter: float = 0.35) -> Iterable[Tuple]:
    cur = con.execute(
        """
        SELECT id, source, url, author, text, created_at, engagement, recency_score, prefilter_score
        FROM posts
        WHERE prefilter_score >= ?
        ORDER BY prefilter_score DESC, created_at DESC
        LIMIT ?
        """,
        (min_prefilter, limit),
    )
    return cur.fetchall()


def posts_missing_embeddings(con: sqlite3.Connection, min_prefilter: float, limit: int) -> List[Tuple[int, str]]:
    cur = con.execute(
        """
        SELECT p.id, p.text
        FROM posts p
        LEFT JOIN embeddings e ON e.post_id = p.id
        WHERE e.post_id IS NULL AND p.prefilter_score >= ?
        ORDER BY p.prefilter_score DESC, p.created_at DESC
        LIMIT ?
        """,
        (min_prefilter, limit),
    )
    return list(cur.fetchall())


def insert_embeddings(con: sqlite3.Connection, rows: Sequence[Tuple[int, str, int, str, int]]) -> int:
    """
    rows = (post_id, model, dimensions, vector_json, created_at)
    """
    cur = con.cursor()
    cur.executemany(
        """
        INSERT OR REPLACE INTO embeddings(post_id, model, dimensions, vector, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.commit()
    return cur.rowcount


def upsert_intent(con: sqlite3.Connection, post_id: int, cluster: str, confidence: float, top_alt_clusters_json: str, decided_at: int) -> None:
    con.execute(
        """
        INSERT INTO intents(post_id, cluster, confidence, top_alt_clusters, decided_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
          cluster=excluded.cluster,
          confidence=excluded.confidence,
          top_alt_clusters=excluded.top_alt_clusters,
          decided_at=excluded.decided_at
        """,
        (post_id, cluster, confidence, top_alt_clusters_json, decided_at),
    )
    con.commit()


def ensure_intent_seed(con: sqlite3.Connection, cluster: str, text: str, model: str, dimensions: int, vector_json: str) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO intent_seeds(cluster, text, model, dimensions, vector)
        VALUES (?, ?, ?, ?, ?)
        """,
        (cluster, text, model, dimensions, vector_json),
    )
    con.commit()


def load_intent_seeds(con: sqlite3.Connection, model: str) -> List[Tuple[str, List[float]]]:
    cur = con.execute(
        """
        SELECT cluster, vector FROM intent_seeds WHERE model = ?
        """,
        (model,),
    )
    import json as _json
    out: List[Tuple[str, List[float]]] = []
    for cluster, vec_json in cur.fetchall():
        out.append((cluster, _json.loads(vec_json)))
    return out


def today_spend_cents(con: sqlite3.Connection, day_start_ts: int) -> int:
    cur = con.execute(
        "SELECT COALESCE(SUM(cost_cents),0) FROM enrichments WHERE fetched_at >= ?",
        (day_start_ts,),
    )
    v = cur.fetchone()[0]
    return int(v or 0)


def posts_for_enrichment(con: sqlite3.Connection, allowed_clusters: List[str], limit: int = 10) -> List[Tuple[int, str, str, float]]:
    placeholders = ",".join(["?"] * len(allowed_clusters)) if allowed_clusters else "?"
    params: List = allowed_clusters[:] if allowed_clusters else [""]
    sql = f"""
        SELECT p.id, p.source, p.text, i.confidence
        FROM posts p
        JOIN intents i ON i.post_id = p.id
        LEFT JOIN enrichments e ON e.post_id = p.id
        WHERE e.post_id IS NULL
          AND ({'i.cluster IN (' + placeholders + ')' if allowed_clusters else '1=1'})
        ORDER BY i.confidence DESC, p.prefilter_score DESC, p.created_at DESC
        LIMIT ?
    """
    params.append(limit)
    cur = con.execute(sql, tuple(params))
    return list(cur.fetchall())


def insert_enrichment(con: sqlite3.Connection, post_id: int, perplexity_json: str | None, dataforseo_json: str | None,
                      firecrawl_json: str | None, browser_use_json: str | None,
                      cost_cents: int, fetched_at: int) -> None:
    con.execute(
        """
        INSERT INTO enrichments(post_id, perplexity, dataforseo, firecrawl, browser_use, cost_cents, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
          perplexity=excluded.perplexity,
          dataforseo=excluded.dataforseo,
          firecrawl=excluded.firecrawl,
          browser_use=excluded.browser_use,
          cost_cents=excluded.cost_cents,
          fetched_at=excluded.fetched_at
        """,
        (post_id, perplexity_json, dataforseo_json, firecrawl_json, browser_use_json, cost_cents, fetched_at),
    )
    con.commit()


def insert_lead(con: sqlite3.Connection, post_id: int, source: str, author: str | None,
                url: str, intent_cluster: str, confidence: float, lead_score: float,
                created_at: int) -> int:
    cur = con.execute(
        """
        INSERT INTO leads(post_id, source, author, url, intent_cluster, confidence, lead_score, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
        """,
        (post_id, source, author, url, intent_cluster, confidence, lead_score, created_at, created_at),
    )
    con.commit()
    return cur.lastrowid


def update_lead_status(con: sqlite3.Connection, lead_id: int, status: str, notes: str | None = None) -> None:
    now = int(_time.time())
    con.execute(
        "UPDATE leads SET status = ?, notes = COALESCE(?, notes), updated_at = ? WHERE id = ?",
        (status, notes, now, lead_id),
    )
    con.commit()


def load_leads(con: sqlite3.Connection, status: str | None = None, min_score: float = 0,
               limit: int = 50, offset: int = 0) -> List:
    if status:
        cur = con.execute(
            "SELECT * FROM leads WHERE status = ? AND lead_score >= ? ORDER BY lead_score DESC, created_at DESC LIMIT ? OFFSET ?",
            (status, min_score, limit, offset),
        )
    else:
        cur = con.execute(
            "SELECT * FROM leads WHERE lead_score >= ? ORDER BY lead_score DESC, created_at DESC LIMIT ? OFFSET ?",
            (min_score, limit, offset),
        )
    return cur.fetchall()


def lead_exists_for_post(con: sqlite3.Connection, post_id: int) -> bool:
    cur = con.execute("SELECT 1 FROM leads WHERE post_id = ?", (post_id,))
    return cur.fetchone() is not None


def insert_sentiment(con: sqlite3.Connection, post_id: int, target: str, sentiment: str,
                     confidence: float, extracted_quote: str | None, created_at: int) -> None:
    con.execute(
        """
        INSERT INTO sentiment(post_id, target, sentiment, confidence, extracted_quote, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (post_id, target, sentiment, confidence, extracted_quote, created_at),
    )
    con.commit()


def load_sentiment_for_post(con: sqlite3.Connection, post_id: int) -> List:
    cur = con.execute(
        "SELECT target, sentiment, confidence, extracted_quote FROM sentiment WHERE post_id = ? ORDER BY confidence DESC",
        (post_id,),
    )
    return cur.fetchall()


def insert_post_performance(con: sqlite3.Connection, approval_id: int, candidate_id: int,
                            post_id: int, platform: str, status: str = "pending") -> int:
    now = int(_time.time())
    cur = con.execute(
        """
        INSERT INTO post_performance(approval_id, candidate_id, post_id, platform, status, posted_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (approval_id, candidate_id, post_id, platform, status, now if status == "posted" else None),
    )
    con.commit()
    return cur.lastrowid


def update_post_performance(con: sqlite3.Connection, perf_id: int, **kwargs) -> None:
    allowed = {"post_url", "upvotes_current", "replies_current", "clicks", "conversions", "status", "last_polled_at"}
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(perf_id)
    con.execute(f"UPDATE post_performance SET {', '.join(sets)} WHERE id = ?", vals)
    con.commit()


def load_post_performance(con: sqlite3.Connection, status: str | None = None, limit: int = 50) -> List:
    if status:
        cur = con.execute(
            "SELECT * FROM post_performance WHERE status = ? ORDER BY posted_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        cur = con.execute("SELECT * FROM post_performance ORDER BY posted_at DESC LIMIT ?", (limit,))
    return cur.fetchall()


def lead_count_by_status(con: sqlite3.Connection) -> Dict[str, int]:
    cur = con.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")
    return {row[0]: row[1] for row in cur.fetchall()}


def sentiment_summary(con: sqlite3.Connection, target: str | None = None) -> List:
    if target:
        cur = con.execute(
            "SELECT sentiment, COUNT(*) as cnt FROM sentiment WHERE target = ? GROUP BY sentiment ORDER BY cnt DESC",
            (target,),
        )
    else:
        cur = con.execute(
            "SELECT target, sentiment, COUNT(*) as cnt FROM sentiment GROUP BY target, sentiment ORDER BY target, cnt DESC",
        )
    return cur.fetchall()
