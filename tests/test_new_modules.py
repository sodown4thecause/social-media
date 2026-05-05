from __future__ import annotations
import json
import os
from pathlib import Path
import time
import pytest

import ingestion.db as db_mod
from ingestion.sentiment import classify_sentiment, _detect_targets
from ingestion.lead_scoring import compute_lead_score, is_high_value_lead


class TestSentiment:
    def test_negative_sentiment_detected(self):
        text = "I hate ahrefs, it's so expensive and the UI is terrible. Waste of money."
        results = classify_sentiment(text)
        assert len(results) >= 1
        assert results[0][0] == "ahrefs"
        assert results[0][1] == "negative"

    def test_positive_sentiment_detected(self):
        text = "I love semrush, it's a great tool with excellent features."
        results = classify_sentiment(text)
        assert len(results) >= 1
        assert results[0][1] == "positive"

    def test_no_competitor_returns_empty(self):
        text = "I'm looking for any SEO tool recommendation."
        results = classify_sentiment(text)
        assert results == []

    def test_detect_targets_finds_competitors(self):
        text = "Comparing ahrefs vs semrush vs moz for our agency."
        targets = _detect_targets(text)
        assert "ahrefs" in targets
        assert "semrush" in targets
        assert "moz" in targets

    def test_detect_targets_case_insensitive(self):
        text = "We use Ahrefs and SEMRUSH for our SEO work."
        targets = _detect_targets(text)
        assert "ahrefs" in targets
        assert "semrush" in targets

    def test_custom_targets(self):
        text = "WorkflowAI is interesting but I'm not convinced."
        results = classify_sentiment(text, targets=["workflowai"])
        assert len(results) == 0  # no sentiment detected


class TestLeadScoring:
    def test_basic_score(self):
        score = compute_lead_score(
            intent_cluster="looking for alternative",
            confidence=0.8,
            engagement=2.0,
            source="reddit",
        )
        assert score > 0
        # intent_weight(30) + confidence(20) + engagement(6) + source(10) = 66
        assert 50 <= score <= 90

    def test_negative_sentiment_bonus(self):
        score_no_bonus = compute_lead_score(
            intent_cluster="pricing transparency",
            confidence=0.7,
            engagement=1.0,
            source="reddit",
            has_negative_sentiment=False,
        )
        score_with_bonus = compute_lead_score(
            intent_cluster="pricing transparency",
            confidence=0.7,
            engagement=1.0,
            source="reddit",
            has_negative_sentiment=True,
        )
        assert score_with_bonus - score_no_bonus == 20

    def test_enrichment_bonus(self):
        score_no = compute_lead_score(
            "too expensive", 0.6, 1.0, "reddit", has_enrichment=False,
        )
        score_yes = compute_lead_score(
            "too expensive", 0.6, 1.0, "reddit", has_enrichment=True,
        )
        assert score_yes - score_no == 5

    def test_high_value_lead(self):
        assert is_high_value_lead(55) is True
        assert is_high_value_lead(30) is False

    def test_g2_source_higher_quality(self):
        score_reddit = compute_lead_score("AI curiosity", 0.5, 1.0, "reddit")
        score_g2 = compute_lead_score("AI curiosity", 0.5, 1.0, "g2")
        assert score_g2 > score_reddit  # g2=15 vs reddit=10

    def test_unknown_intent_gets_default_weight(self):
        score = compute_lead_score("unknown cluster xyz", 0.5, 1.0, "reddit")
        assert score > 0  # should still get engagement + source points

    def test_max_confidence(self):
        score = compute_lead_score(
            "looking for alternative", 1.0, 5.0, "capterra",
            has_negative_sentiment=True, has_enrichment=True,
        )
        # 30 + 25 + 15 + 15 + 20 + 5 = 110
        assert score >= 100


class TestLaunchFixes:
    @pytest.fixture
    def con(self):
        conn = db_mod.connect(":memory:")
        db_mod.init_db(conn)
        yield conn
        conn.close()

    def test_fetch_all_maps_completed_futures_to_source_names(self, monkeypatch):
        from ingestion.config import AppConfig, HackerNewsConfig, RedditConfig, XConfig
        from ingestion.ingest import _fetch_all
        import ingestion.ingest as ingest_mod

        now = int(time.time())
        item = {
            "url": "https://news.ycombinator.com/item?id=1",
            "author": "hnuser",
            "norm_text": "Looking for an AI SEO tool",
            "created_at": now,
            "engagement": 1.0,
            "hash": "h1",
            "recency_score": 1.0,
            "prefilter_score": 0.9,
        }
        monkeypatch.setattr(ingest_mod, "hackernews_rows", lambda limit, min_points: [item])

        cfg = AppConfig(
            reddit=RedditConfig(subreddits=[]),
            x=XConfig(search_queries=[]),
            hackernews=HackerNewsConfig(enabled=True),
        )
        rows = _fetch_all(cfg)

        assert len(rows) == 1
        assert rows[0][0] == "hackernews"
        assert rows[0][3] == "Looking for an AI SEO tool"

    def test_capterra_fetch_yields_from_category_and_competitor_scrapes(self, monkeypatch):
        import ingestion.capterra_scraper as capterra

        def fake_scrape(url, label, limit, now, max_stars=5):
            yield {
                "url": url,
                "author": label,
                "text": "This SEO tool review has enough text to pass filters.",
                "created_at": now,
                "upvotes": 2,
                "comments": None,
            }

        monkeypatch.setattr(capterra, "_scrape_capterra_url", fake_scrape)
        rows = list(capterra.fetch_capterra(categories=["seo"], competitor_slugs=["123/ahrefs"]))

        assert len(rows) == 2
        assert rows[0]["author"] == "seo"
        assert rows[1]["author"] == "123/ahrefs"

    def test_g2_fetch_yields_from_category_and_product_scrapes(self, monkeypatch):
        import ingestion.g2_scraper as g2

        def fake_scrape(url, label, limit, now, max_stars=5):
            yield {
                "url": url,
                "author": label,
                "text": "This G2 review has enough text to pass filters.",
                "created_at": now,
                "upvotes": 2,
                "comments": None,
            }

        monkeypatch.setattr(g2, "_scrape_g2_url", fake_scrape)
        rows = list(g2.fetch_g2(categories=["seo"], competitor_products=["ahrefs"]))

        assert len(rows) == 2
        assert rows[0]["author"] == "seo"
        assert rows[1]["author"] == "ahrefs"

    def test_insert_enrichment_persists_and_updates_spend(self, con):
        now = int(time.time())
        db_mod.upsert_posts(con, [
            ("reddit", "https://reddit.com/r/seo/1", "user", "Need an SEO chatbot", now, 1.0, "h1", 1.0, 0.9)
        ])

        db_mod.insert_enrichment(
            con,
            1,
            json.dumps({"facts": ["one"]}),
            None,
            None,
            json.dumps({"tools_found": ["FlowIntent"]}),
            35,
            now,
        )

        row = con.execute("SELECT perplexity, browser_use, cost_cents FROM enrichments WHERE post_id = 1").fetchone()
        assert json.loads(row[0])["facts"] == ["one"]
        assert json.loads(row[1])["tools_found"] == ["FlowIntent"]
        assert row[2] == 35
        assert db_mod.today_spend_cents(con, now - 1) == 35

    def test_review_item_exposes_post_id_and_lead_creation_uses_it(self, con):
        from ingestion.review_cli import _create_lead_from_review, next_review_item

        now = int(time.time())
        db_mod.upsert_posts(con, [
            ("reddit", "https://reddit.com/r/seo/1", "ignore", "Older post", now, 0, "h1", 1.0, 0.9),
            ("reddit", "https://reddit.com/r/seo/2", "prospect", "Need a Semrush alternative", now, 2, "h2", 1.0, 0.95),
        ])
        db_mod.upsert_intent(con, 2, "looking for alternative", 0.8, json.dumps([]), now)
        con.execute(
            """
            INSERT INTO candidates(post_id, tone, angle, text, score_breakdown, total_score)
            VALUES (2, 'casual helpful', 'cost', 'FlowIntent could fit here.', '{}', 80)
            """
        )
        con.commit()

        candidate_id, post_id, *_ = next_review_item(con)
        assert candidate_id == 1
        assert post_id == 2

        _create_lead_from_review(con, post_id, "reddit", "looking for alternative", 0.8)
        lead = con.execute("SELECT post_id, author, url FROM leads").fetchone()
        assert lead == (2, "prospect", "https://reddit.com/r/seo/2")

    def test_tracking_url_tags_source_intent_and_angle(self):
        from ingestion.generate_and_score import Post, apply_tracking_url

        post = Post(1, "reddit", "Need SEO help", "looking for alternative", 0.8)
        text = apply_tracking_url("I use FlowIntent for this: flowintent.com", post, "cost")

        assert "utm_source=reddit" in text
        assert "utm_medium=reply" in text
        assert "utm_campaign=looking_for_alternative" in text
        assert "utm_content=cost" in text

    def test_indiehackers_reads_json_feed(self, monkeypatch):
        import ingestion.indiehackers_rss as indiehackers

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "items": [
                        {
                            "title": "Need better SEO research",
                            "content_html": "<p>Looking for something simpler than dashboards.</p>",
                            "url": "https://feed.indiehackers.world/post/abc",
                            "date_modified": "2026-05-02T14:14:54.103Z",
                            "author": {"name": "founder"},
                        }
                    ]
                }

        monkeypatch.setattr(indiehackers.requests, "get", lambda *args, **kwargs: FakeResponse())
        rows = list(indiehackers.fetch_indiehackers(limit=1))

        assert len(rows) == 1
        assert rows[0]["author"] == "founder"
        assert "Need better SEO research" in rows[0]["text"]
        assert "simpler than dashboards" in rows[0]["text"]

    def test_x_search_tries_configured_fallbacks(self, monkeypatch):
        from types import SimpleNamespace
        import ingestion.x_search as x_search

        calls = []

        monkeypatch.setattr(x_search, "_getxapi_x_search", lambda *args, **kwargs: [])

        def fake_fetch_feed(url, timeout=20):
            calls.append(url)
            if "bad.example" in url:
                raise RuntimeError("instance down")
            return SimpleNamespace(entries=[
                {
                    "title": "Need an AI SEO tool",
                    "summary": "",
                    "link": "https://nitter.example/user/status/1",
                    "author": "user",
                }
            ])

        monkeypatch.setattr(x_search, "fetch_feed", fake_fetch_feed)
        rows = list(x_search.fetch_x_search(["https://bad.example", "https://ok.example"], ["seo help"], 1))

        assert len(rows) == 1
        assert "bad.example" in calls[0]
        assert "ok.example" in calls[1]
        assert rows[0]["text"] == "Need an AI SEO tool"

    def test_x_search_uses_getxapi_before_nitter(self, monkeypatch):
        import ingestion.x_search as x_search

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "tweets": [
                        {
                            "url": "https://x.com/founder/status/1",
                            "text": "Looking for an Ahrefs alternative that is easier for founders.",
                            "createdAt": "Sat Apr 18 00:34:21 +0000 2026",
                            "likeCount": 3,
                            "replyCount": 1,
                            "author": {"userName": "founder"},
                        }
                    ]
                }

        monkeypatch.setenv("GETXAPI_API_KEY", "test-key")
        monkeypatch.setattr(x_search.requests, "get", lambda *args, **kwargs: FakeResponse())
        monkeypatch.setattr(x_search, "fetch_feed", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Nitter should not run")))

        rows = list(x_search.fetch_x_search(["https://nitter.example"], ["Ahrefs alternative"], 2))

        assert len(rows) == 1
        assert rows[0]["author"] == "founder"
        assert rows[0]["upvotes"] == 3
        assert rows[0]["comments"] == 1

    def test_x_search_caps_queries_per_run(self, monkeypatch):
        import ingestion.x_search as x_search

        calls = []

        class FakeResponse:
            def __init__(self, query):
                self.query = query

            def raise_for_status(self):
                return None

            def json(self):
                return {"tweets": [{"url": f"https://x.com/search/{self.query}", "text": f"{self.query} lead"}]}

        def fake_get(url, params, headers, timeout):
            calls.append(params["q"])
            return FakeResponse(params["q"])

        monkeypatch.setenv("GETXAPI_API_KEY", "test-key")
        monkeypatch.setattr(x_search.requests, "get", fake_get)

        rows = list(x_search.fetch_x_search("https://nitter.example", ["one", "two", "three"], 1, max_queries=2))

        assert calls == ["one", "two"]
        assert len(rows) == 2

    def test_browser_use_review_results_default_to_empty_lists(self):
        from ingestion.capterra_scraper import CapterraResult
        from ingestion.g2_scraper import G2Result

        assert CapterraResult().reviews == []
        assert G2Result().reviews == []

    def test_review_jsonld_extraction_finds_reviews(self):
        from ingestion.review_extract import extract_review_nodes

        page = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "review": [{
            "@type": "Review",
            "name": "Too complex",
            "reviewBody": "The platform has useful SEO data, but our team found it too complex.",
            "reviewRating": {"@type": "Rating", "ratingValue": "3"},
            "author": {"@type": "Person", "name": "A reviewer"}
          }]
        }
        </script>
        """

        reviews = extract_review_nodes(page)

        assert len(reviews) == 1
        assert reviews[0]["title"] == "Too complex"
        assert reviews[0]["rating"] == 3
        assert reviews[0]["author"] == "A reviewer"

    def test_review_extraction_falls_back_to_itemprop_html(self):
        from ingestion.review_extract import extract_review_nodes

        page = """
        <div><label>2/5</label></div>
        <div itemprop="reviewBody">
          <section><div>What do you dislike?</div>
          <p>The keyword data is good, but the workflow is too complicated for a small team.</p></section>
        </div>
        """

        reviews = extract_review_nodes(page)

        assert len(reviews) == 1
        assert reviews[0]["rating"] == 2
        assert "too complicated" in reviews[0]["text"]

    def test_capterra_uses_scrapingbee_before_browser_use(self, monkeypatch):
        import ingestion.capterra_scraper as capterra

        page = """
        <script type="application/ld+json">
        {"@type":"Review","reviewBody":"Useful SEO data but too much dashboard work for us.","reviewRating":{"ratingValue":3}}
        </script>
        """
        monkeypatch.setattr(capterra, "fetch_html", lambda *args, **kwargs: page)
        monkeypatch.setattr(capterra, "run_task", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Browser Use should not run")))

        rows = list(capterra._scrape_capterra_url("https://example.com", "example", 1, 1, max_stars=3))

        assert len(rows) == 1
        assert "too much dashboard" in rows[0]["text"]

    def test_g2_uses_scrapingbee_before_browser_use(self, monkeypatch):
        import ingestion.g2_scraper as g2

        page = """
        <script type="application/ld+json">
        {"@type":"Review","reviewBody":"Good keyword data, but pricing and complexity were hard to justify.","reviewRating":{"ratingValue":3}}
        </script>
        """
        monkeypatch.setattr(g2, "fetch_html", lambda *args, **kwargs: page)
        monkeypatch.setattr(g2, "run_task", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Browser Use should not run")))

        rows = list(g2._scrape_g2_url("https://example.com", "example", 1, 1, max_stars=3))

        assert len(rows) == 1
        assert "pricing and complexity" in rows[0]["text"]

    def test_feed_text_cleaning_removes_markup(self):
        from ingestion.feed_utils import clean_feed_text

        text = clean_feed_text('<!-- SC_OFF --><div class="md"><p>Need SEO help &amp; a simpler UI.</p></div>')

        assert text == "Need SEO help & a simpler UI."

    def test_reddit_parser_uses_clean_summary_text(self):
        from ingestion.reddit_rss import parse_reddit_entry

        row = parse_reddit_entry({
            "title": "Short title",
            "summary": '<!-- SC_OFF --><div class="md"><p>Looking for an Ahrefs alternative with chat UX.</p></div>',
            "link": "https://reddit.example/post",
        })

        assert row["text"] == "Looking for an Ahrefs alternative with chat UX."

    def test_hackernews_removes_feed_metadata(self, monkeypatch):
        from types import SimpleNamespace
        import ingestion.hackernews_rss as hackernews

        monkeypatch.setattr(hackernews, "fetch_feed", lambda url: SimpleNamespace(entries=[
            {
                "title": "Ask HN: Best SEO research workflow?",
                "summary": "<p>Article URL: <a href=\"https://example.com\">x</a></p><p>Points: 5</p>",
                "link": "https://news.ycombinator.com/item?id=1",
            }
        ]))

        rows = list(hackernews.fetch_hackernews(limit=1, min_points=0))

        assert rows[0]["text"] == "Ask HN: Best SEO research workflow?"

    def test_producthunt_removes_discussion_link_suffix(self, monkeypatch):
        from types import SimpleNamespace
        import ingestion.producthunt_rss as producthunt

        monkeypatch.setattr(producthunt, "fetch_feed", lambda url: SimpleNamespace(entries=[
            {
                "title": "FlowIntent",
                "summary": "<p>Chat UX for SEO teams</p><p>Discussion | Link</p>",
                "link": "https://producthunt.example/flowintent",
            }
        ]))

        rows = list(producthunt.fetch_producthunt(limit=1, topic="marketing", secondary_topic=None))

        assert rows[0]["text"] == "FlowIntent\nChat UX for SEO teams"

    def test_local_env_loader_reads_ignored_env_file(self, monkeypatch):
        from ingestion.env_loader import load_local_env

        tmp_dir = Path("pytest-tmp")
        tmp_dir.mkdir(exist_ok=True)
        env_path = tmp_dir / "env-loader.env"
        monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
        monkeypatch.setenv("EXISTING", "from-env")

        try:
            env_path.write_text("BROWSER_USE_API_KEY='local-key'\nEXISTING=from-file\n", encoding="utf-8")
            load_local_env(env_path)
        finally:
            env_path.unlink(missing_ok=True)

        assert os.environ["BROWSER_USE_API_KEY"] == "local-key"
        assert os.environ["EXISTING"] == "from-env"

    def test_scrapingbee_boolean_params_are_lowercase(self, monkeypatch):
        import ingestion.scrapingbee_client as scrapingbee

        captured = {}

        class FakeResponse:
            text = "<html></html>"

            def raise_for_status(self):
                return None

        def fake_get(url, params, timeout):
            captured.update(params)
            return FakeResponse()

        monkeypatch.setenv("SCRAPINGBEE_API_KEY", "test-key")
        monkeypatch.setattr(scrapingbee.requests, "get", fake_get)

        scrapingbee.fetch_html("https://example.com", premium_proxy=True)

        assert captured["premium_proxy"] == "true"
        assert captured["render_js"] == "true"
