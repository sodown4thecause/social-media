from __future__ import annotations
import json
import math
import time
from typing import Dict, List, Tuple

from .config import AppConfig
from .db import (
    connect,
    init_db,
    posts_missing_embeddings,
    insert_embeddings,
    ensure_intent_seed,
    load_intent_seeds,
    upsert_intent,
)
from .jina_client import JinaClient
from .local_embed import LocalEmbedder
from .intent_clusters import SEED_EXEMPLARS
from .logging_config import intent_log as log
from .metrics import incr, record_run


def dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def norm(a: List[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def cosine(a: List[float], b: List[float]) -> float:
    na = norm(a)
    nb = norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return dot(a, b) / (na * nb)


def mean_vector(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            out[i] += x
    return [x / len(vectors) for x in out]


def ensure_seed_embeddings(con, client: JinaClient) -> Dict[str, List[List[float]]]:
    # Check if seeds for this model exist; if not, embed and store.
    seed_vectors: Dict[str, List[List[float]]] = {}
    for cluster, seeds in SEED_EXEMPLARS.items():
        # We will always embed seeds on first run and store; load later for centroid calc.
        embeds = client.embed_texts(seeds)
        seed_vectors[cluster] = embeds
        for seed_text, vec in zip(seeds, embeds):
            ensure_intent_seed(con, cluster, seed_text, client.model, len(vec), json.dumps(vec))
    return seed_vectors


def load_or_embed_seeds(con, client: JinaClient) -> Dict[str, List[List[float]]]:
    # Attempt to load; if empty for this model, embed and store.
    loaded = load_intent_seeds(con, client.model)
    if loaded:
        # Group by cluster
        grouped: Dict[str, List[List[float]]] = {}
        for cluster, vec in loaded:
            grouped.setdefault(cluster, []).append(vec)
        # Ensure all clusters exist (embed missing ones if any)
        missing = [c for c in SEED_EXEMPLARS.keys() if c not in grouped]
        if missing:
            client.embed_texts([" "])  # sanity ping
            for c in missing:
                embeds = client.embed_texts(SEED_EXEMPLARS[c])
                for seed_text, vec in zip(SEED_EXEMPLARS[c], embeds):
                    ensure_intent_seed(con, c, seed_text, client.model, len(vec), json.dumps(vec))
                grouped[c] = embeds
        return grouped
    return ensure_seed_embeddings(con, client)


def classify_posts(db_path: str = "data.sqlite3", min_prefilter: float = 0.35, batch_size: int = 100,
                   model: str = "jina-embeddings-v5-text-nano", dimensions: int | None = 512,
                   embed_batch_size: int = 32,
                   intent_threshold: float = 0.45, intent_avg_top2_threshold: float = 0.42,
                   local_intent_threshold: float = 0.35, local_intent_avg_top2_threshold: float = 0.37) -> None:
    cfg = AppConfig.from_file()
    db_path = cfg.db_path
    min_prefilter = cfg.prefilter_threshold
    batch_size = cfg.intent.batch_size
    model = cfg.jina.model
    dimensions = cfg.jina.dimensions
    embed_batch_size = cfg.jina.batch_size
    intent_threshold = cfg.intent.threshold
    intent_avg_top2_threshold = cfg.intent.avg_top2_threshold
    local_intent_threshold = cfg.intent.local_threshold
    local_intent_avg_top2_threshold = cfg.intent.local_avg_top2_threshold
    con = connect(db_path)
    init_db(con)
    use_local = False
    try:
        client = JinaClient(model=model, dimensions=dimensions, batch_size=embed_batch_size)
        # Load or embed seeds with Jina
        seeds = load_or_embed_seeds(con, client)
        centroids: Dict[str, List[float]] = {c: mean_vector(vecs) for c, vecs in seeds.items()}

        # Fetch posts missing embeddings
        to_embed = posts_missing_embeddings(con, min_prefilter=min_prefilter, limit=batch_size)
        if to_embed:
            texts = [t for (_id, t) in to_embed]
            vectors = client.embed_texts(texts)
            now = int(time.time())
            embed_rows = []
            for (post_id, _text), vec in zip(to_embed, vectors):
                embed_rows.append((post_id, client.model, len(vec), json.dumps(vec), now))
            if embed_rows:
                n = insert_embeddings(con, embed_rows)
                print(f"[Jina] Inserted embeddings for {n} posts.")
        print("Using Jina embeddings.")
    except Exception as e:
        # Jina unavailable: fall back to local TF-IDF embedder (no external cost)
        print(f"Jina unavailable ({e}); falling back to local embeddings.")
        use_local = True
        # Build local embedder from seeds and a small sample of posts
        sample_cur = con.execute(
            "SELECT text FROM posts WHERE prefilter_score >= ? ORDER BY prefilter_score DESC LIMIT ?",
            (min_prefilter, min(200, batch_size * 2)),
        )
        sample_docs = [r[0] for r in sample_cur.fetchall()]
        local = LocalEmbedder(SEED_EXEMPLARS, sample_docs)
        seeds = local.seed_vectors()
        centroids = {c: mean_vector(vecs) for c, vecs in seeds.items()}

        # No persistence of local vectors in embeddings table (since they are vocab-tied); classify directly
        # Load posts to classify (those without an intent yet)
        cur = con.execute(
            """
            SELECT p.id, p.text
            FROM posts p
            LEFT JOIN intents i ON i.post_id = p.id
            WHERE i.post_id IS NULL AND p.prefilter_score >= ?
            ORDER BY p.prefilter_score DESC, p.created_at DESC
            LIMIT ?
            """,
            (min_prefilter, batch_size),
        )
        rows = cur.fetchall()
        inserted = 0
        for post_id, text in rows:
            vec = local.embed_texts([text])[0]
            # similarities
            sims = [(cluster, float(cosine(vec, centroid))) for cluster, centroid in centroids.items() if centroid]
            sims.sort(key=lambda x: x[1], reverse=True)
            if not sims:
                continue
            best_cluster, best_score = sims[0]
            top2_avg = (sims[0][1] + (sims[1][1] if len(sims) > 1 else 0.0)) / (2.0 if len(sims) > 1 else 1.0)
            # Local embedder is weaker; use looser thresholds
            passes = (best_score >= local_intent_threshold) or (top2_avg >= local_intent_avg_top2_threshold)
            chosen_cluster = None
            chosen_conf = None
            if passes:
                chosen_cluster = best_cluster
                chosen_conf = best_score
            else:
                # Keyword heuristic fallback
                text_l = text.lower()
                KEYWORDS = {
                    "pricing transparency": ["price", "pricing", "expensive", "cost", "budget", "cheaper", "overpriced"],
                    "looking for alternative": ["alternative", "replacement", "switch", "competitor", "instead"],
                    "AI curiosity": [" ai ", "gpt", "chatgpt", "llm", "agent", "automation", "ai-"],
                    "agency frustration": ["agency", "retainer", "freelancer", "consultant", "overpromis", "underdeliver"],
                    "SEO stack fatigue": ["stack", "dashboard", "semrush", "ahrefs", "gsc", "overlap", "juggling", "too many tools"],
                    "migration help": ["migrating", "migration", "redirect", "404", "domain change", "move platform"],
                    "ROI/benchmarking": ["roi", "benchmark", "kpi", "timeline", "results", "impact"],
                    "integration friction": ["integration", "api", "zapier", "integromat", "connect", "import", "export"],
                    "too complex": ["complex", "overcomplicated", "confusing", "steep", "learning curve", "hard to use"],
                }
                best_kw = None
                best_count = 0
                for cl, kws in KEYWORDS.items():
                    cnt = 0
                    for kw in kws:
                        if kw in text_l:
                            cnt += 1
                    if cnt > best_count:
                        best_count = cnt
                        best_kw = cl
                if best_kw and best_count > 0:
                    chosen_cluster = best_kw
                    # heuristic confidence scaled
                    chosen_conf = min(0.6, 0.4 + 0.08 * min(5, best_count))
                else:
                    # As a last resort, keep the best cluster with a low confidence to enable downstream review
                    chosen_cluster = best_cluster
                    chosen_conf = 0.36
            decided_at = int(time.time())
            top_alt = [{"cluster": c, "score": s} for c, s in sims[1:4]]
            upsert_intent(con, post_id, chosen_cluster, float(chosen_conf), json.dumps(top_alt), decided_at)
            inserted += 1
        print(f"[Local] Classified {inserted} posts with intents (from {len(rows)} considered).")

    if use_local:
        return

    # Load new embeddings for classification (Jina path)
    cur = con.execute(
        """
        SELECT p.id, e.vector
        FROM posts p
        JOIN embeddings e ON e.post_id = p.id
        LEFT JOIN intents i ON i.post_id = p.id
        WHERE i.post_id IS NULL AND p.prefilter_score >= ?
        ORDER BY p.prefilter_score DESC, p.created_at DESC
        LIMIT ?
        """,
        (min_prefilter, batch_size),
    )
    rows = cur.fetchall()
    if not rows:
        print("No posts to classify.")
        return

    for post_id, vec_json in rows:
        vec = json.loads(vec_json)
        # Compute similarity against centroids
        sims = [(cluster, float(cosine(vec, centroid))) for cluster, centroid in centroids.items() if centroid]
        sims.sort(key=lambda x: x[1], reverse=True)
        best_cluster, best_score = sims[0]
        top2_avg = (sims[0][1] + (sims[1][1] if len(sims) > 1 else 0.0)) / (2.0 if len(sims) > 1 else 1.0)

        passes = (best_score >= intent_threshold) or (top2_avg >= intent_avg_top2_threshold)
        if not passes:
            # Skip storing intents that are too uncertain; you can tune this later
            continue

        decided_at = int(time.time())
        top_alt = [
            {"cluster": c, "score": s}
            for c, s in sims[1:4]
        ]
        upsert_intent(con, post_id, best_cluster, best_score, json.dumps(top_alt), decided_at)

    print(f"Classified {len(rows)} posts with intents.")
    incr("intents_classified", len(rows))
    record_run("compute_intents")


if __name__ == "__main__":
    # Defaults can be overridden by environment or config in later steps
    classify_posts()
