from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import webapp.server as server


def test_contact_strategy_handles_review_sites():
    g2 = server.contact_strategy("g2")
    capterra = server.contact_strategy("capterra")

    assert g2["label"] == "Research lead"
    assert "No native DM" in g2["guidance"]
    assert capterra["label"] == "Research lead"


def test_clean_signal_text_strips_reddit_html():
    raw = '<!-- SC_OFF --><div class="md"><p>Need an Ahrefs alternative.</p></div><!-- SC_ON --> submitted by <a href="/u/x">/u/x</a>'

    cleaned = server.clean_signal_text(raw)

    assert cleaned == "Need an Ahrefs alternative. /u/x"


def test_source_health_endpoint_uses_light_check_by_default(monkeypatch):
    monkeypatch.setattr(server, "source_health", lambda include_browser_sources=False: {
        "deep": include_browser_sources,
        "providers": {"GETXAPI_API_KEY": True},
        "sources": {"x": {"status": "ok", "count": 1, "samples": []}},
    })

    payload = server.source_health(include_browser_sources=False)

    assert payload["deep"] is False
    assert payload["sources"]["x"]["status"] == "ok"


def test_record_decision_can_create_lead(monkeypatch):
    from ingestion.db import connect, init_db, upsert_intent, upsert_posts

    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        monkeypatch.setattr(server, "DB_PATH", db_path)
        con = connect(str(db_path))
        init_db(con)
        now = int(time.time())
        upsert_posts(con, [
            (
                "g2",
                "https://www.g2.com/products/semrush/reviews/example",
                "Reviewer",
                "Semrush is powerful but expensive and too complex for our team.",
                now,
                3.0,
                "webapp-h1",
                1.0,
                0.9,
            )
        ])
        upsert_intent(con, 1, "looking for alternative", 0.82, json.dumps([]), now)
        con.execute(
            """
            INSERT INTO candidates(post_id, tone, angle, text, score_breakdown, total_score)
            VALUES (1, 'casual helpful', 'cost', 'FlowIntent may fit this.', '{}', 82)
            """
        )
        con.commit()
        con.close()

        result = server.record_decision(1, {"decision": "approved", "create_lead": True})

        con = connect(str(db_path))
        init_db(con)
        lead = con.execute("SELECT source, author, intent_cluster, status FROM leads").fetchone()
        approval = con.execute("SELECT decision, channel FROM approvals").fetchone()
        con.close()

        assert result["ok"] is True
        assert result["lead_id"] == 1
        assert lead == ("g2", "Reviewer", "looking for alternative", "new")
        assert approval == ("approved", "webapp")
    finally:
        db_path.unlink(missing_ok=True)
