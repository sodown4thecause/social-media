import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple, List

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
  FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


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
                      firecrawl_json: str | None, cost_cents: int, fetched_at: int) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO enrichments(post_id, perplexity, dataforseo, firecrawl, cost_cents, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (post_id, perplexity_json, dataforseo_json, firecrawl_json, cost_cents, fetched_at),
    )
    con.commit()
