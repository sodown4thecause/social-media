from __future__ import annotations
import json
import math
import tempfile
import time
from pathlib import Path

import pytest

import ingestion.prefilter as pf
import ingestion.db as db_mod
from ingestion.compute_intents import dot, norm, cosine, mean_vector


# ── prefilter tests ──

class TestPrefilter:
    def test_normalize_removes_urls(self):
        result = pf.normalize_text("Check out https://example.com/foo this tool www.test.com")
        assert "https://example.com/foo" not in result
        assert "www.test.com" not in result
        assert "Check out" in result
        assert "this tool" in result

    def test_normalize_trims_and_collapses_whitespace(self):
        result = pf.normalize_text("  hello    world  ")
        assert result == "hello world"

    def test_recency_now_decays_to_one(self):
        now = int(time.time())
        score = pf.recency_decay(now)
        assert score == pytest.approx(1.0)

    def test_recency_old_decays_low(self):
        old = int(time.time()) - 48 * 3600  # 48 hours ago
        score = pf.recency_decay(old, half_life_hours=24.0)
        assert score == pytest.approx(0.25)

    def test_engagement_score_zero(self):
        assert pf.engagement_score(0, 0) == pytest.approx(0.0)

    def test_engagement_score_high(self):
        s = pf.engagement_score(1000, 500)
        assert s > 0.9

    def test_prefilter_produces_hash(self):
        now = int(time.time())
        result = pf.prefilter("Test post about SEO tools", now, upvotes=10, comments=3)
        assert len(result.hash) == 40
        assert 0.0 <= result.score <= 1.0
        assert 0.0 <= result.recency_score <= 1.0
        assert 0.0 <= result.engagement <= 1.0

    def test_prefilter_identical_text_different_time_different_hash(self):
        now = int(time.time())
        a = pf.prefilter("Same post", now)
        b = pf.prefilter("Same post", now + 1)
        assert a.hash != b.hash

    def test_prefilter_same_text_same_time_same_hash(self):
        now = int(time.time())
        a = pf.prefilter("Same post", now)
        b = pf.prefilter("Same post", now)
        assert a.hash == b.hash


# ── vector math tests ──

class TestVectorMath:
    def test_dot_product(self):
        assert dot([1, 2, 3], [4, 5, 6]) == pytest.approx(32.0)

    def test_dot_product_empty(self):
        assert dot([], []) == 0.0

    def test_norm(self):
        assert norm([3, 4]) == pytest.approx(5.0)

    def test_norm_zero_vector(self):
        assert norm([0, 0, 0]) == 0.0

    def test_cosine_identical(self):
        assert cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)

    def test_cosine_orthogonal(self):
        assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_cosine_strictly_negative(self):
        assert cosine([1, 0, 0], [-1, 0, 0]) == pytest.approx(-1.0)

    def test_mean_vector(self):
        result = mean_vector([[1, 2], [3, 4], [5, 6]])
        assert result == [3.0, 4.0]

    def test_mean_vector_empty(self):
        assert mean_vector([]) == []


# ── db tests ──

class TestDB:
    @pytest.fixture
    def con(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            tmp_path = tmp.name
        conn = db_mod.connect(tmp_path)
        db_mod.init_db(conn)
        yield conn
        conn.close()
        Path(tmp_path).unlink(missing_ok=True)

    def test_empty_db_has_tables(self, con):
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        assert "posts" in tables
        assert "embeddings" in tables
        assert "intent_seeds" in tables
        assert "intents" in tables
        assert "candidates" in tables
        assert "approvals" in tables
        assert "enrichments" in tables

    def test_upsert_inserts_one(self, con):
        now = int(time.time())
        rows = [("reddit", "http://x.com/1", "user1", "Test post", now, 2.5, "abc123", 0.9, 0.7)]
        n = db_mod.upsert_posts(con, rows)
        assert n == 1

    def test_upsert_dedup_by_hash(self, con):
        now = int(time.time())
        row = ("reddit", "http://x.com/1", "user1", "Test post", now, 2.5, "abc123", 0.9, 0.7)
        n1 = db_mod.upsert_posts(con, [row])
        n2 = db_mod.upsert_posts(con, [row])
        assert n1 == 1
        assert n2 == 0

    def test_top_candidates_filter(self, con):
        now = int(time.time())
        rows = [
            ("reddit", "http://x.com/1", "a", "High score", now, 5.0, "h1", 0.9, 0.8),
            ("reddit", "http://x.com/2", "b", "Low score", now, 0.0, "h2", 0.9, 0.1),
        ]
        db_mod.upsert_posts(con, rows)
        results = list(db_mod.top_candidates(con, min_prefilter=0.35))
        assert len(results) == 1
        assert results[0][4] == "High score"

    def test_posts_missing_embeddings(self, con):
        now = int(time.time())
        db_mod.upsert_posts(con, [
            ("reddit", "http://x.com/1", "a", "Needs embedding", now, 0.0, "h1", 0.9, 0.5)
        ])
        missing = db_mod.posts_missing_embeddings(con, min_prefilter=0.35, limit=10)
        assert len(missing) == 1

    def test_insert_embeddings(self, con):
        now = int(time.time())
        db_mod.upsert_posts(con, [
            ("reddit", "http://x.com/1", "a", "Has embedding", now, 0.0, "h1", 0.9, 0.5)
        ])
        vec_json = json.dumps([0.1, 0.2, 0.3])
        n = db_mod.insert_embeddings(con, [(1, "test-model", 3, vec_json, now)])
        assert n == 1
        missing = db_mod.posts_missing_embeddings(con, min_prefilter=0.35, limit=10)
        assert len(missing) == 0

    def test_intent_upsert_and_seed(self, con):
        now = int(time.time())
        db_mod.upsert_posts(con, [
            ("reddit", "http://x.com/1", "a", "Intent post", now, 0.0, "h1", 0.9, 0.5)
        ])
        db_mod.upsert_intent(con, 1, "pricing transparency", 0.85, json.dumps([]), now)
        db_mod.ensure_intent_seed(con, "pricing transparency", "seed text", "test-model", 3, json.dumps([0.1, 0.2, 0.3]))

        seeds = db_mod.load_intent_seeds(con, "test-model")
        assert len(seeds) == 1
        assert seeds[0][0] == "pricing transparency"


# ── config tests ──

class TestConfig:
    def test_from_file_falls_back_to_defaults(self, tmp_path, monkeypatch):
        from ingestion.config import AppConfig
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text('{"db_path": "custom.db"}')
        monkeypatch.chdir(tmp_path)
        cfg = AppConfig.from_file(path=str(cfg_path))
        assert cfg.db_path == "custom.db"
        assert "SEO" in cfg.reddit.subreddits
        assert "bigseo" in cfg.reddit.subreddits
        assert "Entrepreneur" in cfg.reddit.subreddits
        assert cfg.jina.batch_size == 32
        assert cfg.prefilter_threshold == 0.35

    def test_env_override(self, tmp_path, monkeypatch):
        from ingestion.config import AppConfig
        monkeypatch.setenv("LGI_DB_PATH", "env_override.db")
        monkeypatch.setenv("LGI_HACKERNEWS__ENABLED", "true")
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{}")
        monkeypatch.chdir(tmp_path)
        cfg = AppConfig.from_file(path=str(cfg_path))
        assert cfg.db_path == "env_override.db"
        assert cfg.hackernews.enabled is True

    def test_flat_dump_produces_backwards_compat_keys(self):
        from ingestion.config import AppConfig
        cfg = AppConfig()
        flat = cfg.model_dump_flat()
        assert "reddit_subreddits" in flat
        assert "SEO" in flat["reddit_subreddits"]
        assert "intent_threshold" in flat
        assert flat["intent_threshold"] == 0.45


# ── local embed tests ──

class TestLocalEmbedder:
    def test_embedding_dimensions(self):
        from ingestion.local_embed import LocalEmbedder
        seeds = {
            "test": ["hello world", "foo bar"],
        }
        le = LocalEmbedder(seeds)
        assert le.dim > 0
        vecs = le.embed_texts(["hello world test"])
        assert len(vecs) == 1
        assert len(vecs[0]) == le.dim

    def test_cosine_zero_for_empty_vocab(self):
        from ingestion.local_embed import LocalEmbedder
        seeds = {"test": ["a"]}  # single-char terms filtered by len>2
        le = LocalEmbedder(seeds)
        if le.dim == 0:
            assert cosine([], []) == 0.0


# ── retry / circuit breaker tests ──

class TestRetryUtils:
    def test_circuit_breaker_starts_closed(self):
        from ingestion.retry_utils import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=300)
        assert not cb.is_open

    def test_circuit_breaker_trips_after_failures(self):
        from ingestion.retry_utils import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=99999)
        cb.failure()
        cb.failure()
        assert not cb.is_open  # not yet
        cb.failure()
        assert cb.is_open  # tripped

    def test_circuit_breaker_resets_on_success(self):
        from ingestion.retry_utils import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=99999)
        cb.failure()
        cb.failure()
        cb.success()
        assert not cb.is_open

    def test_circuit_breaker_cooldown(self):
        from ingestion.retry_utils import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=0)
        cb.failure()
        cb.failure()
        cb.failure()
        # cooldown_seconds=0 means it resets immediately
        assert not cb.is_open


# ── metrics tests ──

class TestMetrics:
    def test_counter_increment(self):
        from ingestion.metrics import incr, dump
        incr("test_counter", 1)
        incr("test_counter", 2)
        d = dump()
        assert d["counters"]["test_counter"] == 3

    def test_gauge_set(self):
        from ingestion.metrics import set_gauge, dump
        set_gauge("test_gauge", 42.0)
        d = dump()
        assert d["gauges"]["test_gauge"] == 42.0

    def test_record_run(self):
        from ingestion.metrics import record_run, dump
        record_run("test_stage")
        d = dump()
        assert "test_stage" in d["last_runs"]

    def test_dump_json(self):
        from ingestion.metrics import dump_json
        s = dump_json()
        assert "counters" in s
        assert "gauges" in s
        assert "last_runs" in s


# ── config: retry section ──

class TestConfigRetry:
    def test_retry_config_defaults(self):
        from ingestion.config import RetryConfig
        rc = RetryConfig()
        assert rc.max_attempts == 3
        assert rc.circuit_breaker_failures == 3
        assert rc.circuit_breaker_cooldown_minutes == 5
