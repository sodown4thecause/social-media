from __future__ import annotations
import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .db import (
    connect,
    init_db,
    today_spend_cents,
    posts_for_enrichment,
    insert_enrichment,
)
from .config import AppConfig
from .logging_config import enrich_log as log
from .metrics import incr, record_run
from .browser_use_client import run_task, set_api_key


def load_config(path: str | None = None) -> Dict[str, Any]:
    p = Path(path or 'config.json')
    with p.open('r', encoding='utf-8') as f:
        return json.load(f)


def day_start_ts() -> int:
    now = int(time.time())
    # midnight UTC for simplicity
    return now - (now % 86400)


# -------- Providers --------


def call_perplexity(text: str, model: str, api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not api_key:
        return None
    url = 'https://api.perplexity.ai/v1/sonar'
    headers = {
        'Authorization': f'Bearer {api_key.strip()}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    prompt = (
        "Based on the discussion below, produce 2 concise facts with source URLs that would help craft a credible, "
        "casual helpful reply. If pricing or alternatives are mentioned, prioritize concrete, current details.\n\n"
        f"Post:\n{text[:1500]}"
    )
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': 'You are precise and cite sources. Keep it short.'},
            {'role': 'user', 'content': prompt},
        ],
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def call_dataforseo(keyword: str, location_name: str, language_name: str, depth: int,
                    login: Optional[str], password: Optional[str]) -> Optional[Dict[str, Any]]:
    if not login or not password:
        return None
    auth = base64.b64encode(f"{login}:{password}".encode('utf-8')).decode('ascii')
    url = 'https://api.dataforseo.com/v3/serp/google/organic/live/advanced'
    headers = {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    task = {
        'location_name': location_name,
        'language_name': language_name,
        'keyword': keyword,
        'depth': int(depth),
    }
    body = [task]
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def call_firecrawl_agent(prompt: str, model: str, max_credits: int, api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not api_key:
        return None
    url = 'https://api.firecrawl.dev/v2/agent'
    headers = {
        'Authorization': f'Bearer {api_key.strip()}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    body = {
        'prompt': prompt,
        'model': model,
        'maxCredits': int(max_credits),
    }
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def call_browser_use_enrich(text: str, source: str) -> dict:
    """Use browser-use agent to research the topic live on the web."""
    task = (
        f"Research the following post from {source} and find relevant information about the tools, "
        f"pricing, competitors, and alternatives mentioned. Return a JSON with fields: "
        f"tools_found (list of tool names), pricing_info (string summary), "
        f"competitors (list of competing tools), and key_insights (string).\n\n"
        f"Post content: {text[:1000]}"
    )
    raw = run_task(task)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        return {"raw_output": raw}
    return {"error": "No output"}


# -------- Strategy --------

COMPETITOR_KEYWORDS = [
    'ahrefs', 'semrush', 'moz', 'screaming frog', 'surfer', 'similarweb', 'spyfu', 'rank math', 'yoast', 'serpstat'
]


def derive_queries(text: str) -> List[str]:
    t = text.lower()
    q: List[str] = []
    hits = [kw for kw in COMPETITOR_KEYWORDS if kw in t]
    for h in hits[:2]:
        q.append(f"{h} pricing")
    if not q:
        # broad fallback around pricing/alternatives
        q.append('AI SEO tools pricing')
        q.append('best ahrefs alternative')
    return q


def enrich_once() -> None:
    cfg = AppConfig.from_file()
    con = connect(cfg.db_path)
    init_db(con)

    daily_cap = cfg.enrichment.daily_budget_cents
    spent = today_spend_cents(con, day_start_ts())
    remaining = max(0, daily_cap - spent)
    if remaining <= 0:
        log.info("Enrichment skipped: daily budget exhausted", extra={"spent": spent, "cap": daily_cap})
        return

    allowed_clusters = cfg.enrichment.intents
    rows = posts_for_enrichment(con, allowed_clusters, limit=5)
    if not rows:
        log.info("No posts eligible for enrichment")
        return

    perplexity_enabled = cfg.enrichment.perplexity_enabled
    perplexity_model = cfg.enrichment.perplexity_model
    perplexity_cost = cfg.enrichment.perplexity_cost_cents
    px_key = os.getenv("PERPLEXITY_API_KEY")

    dataforseo_enabled = cfg.enrichment.dataforseo_enabled
    dfs_login = os.getenv("DATAFORSEO_LOGIN") or os.getenv("DATAFORSEO_USERNAME")
    dfs_password = os.getenv("DATAFORSEO_PASSWORD")
    dfs_loc = cfg.enrichment.dataforseo_location_name
    dfs_lang = cfg.enrichment.dataforseo_language_name
    dfs_depth = cfg.enrichment.dataforseo_depth
    dfs_cost = cfg.enrichment.dataforseo_cost_cents

    firecrawl_enabled = cfg.enrichment.firecrawl_enabled
    fc_model = cfg.enrichment.firecrawl_model
    fc_max = cfg.enrichment.firecrawl_max_credits
    fc_cost = cfg.enrichment.firecrawl_cost_cents
    fc_key = os.getenv("FIRECRAWL_API_KEY")

    bu_enabled = cfg.enrichment.browser_use_enabled
    bu_cost = cfg.enrichment.browser_use_cost_cents
    bu_key = os.getenv("BROWSER_USE_API_KEY")
    if bu_key:
        set_api_key(bu_key)

    for post_id, source, text, conf in rows:
        cost_cents = 0
        px_json = None
        dfs_json = None
        fc_json = None
        bu_json = None

        # Perplexity (claim/evidence summary)
        if perplexity_enabled and px_key and remaining - cost_cents >= perplexity_cost:
            try:
                px_json = call_perplexity(text, perplexity_model, px_key)
                cost_cents += perplexity_cost
            except Exception as e:
                px_json = {"error": str(e)}

        # DataForSEO (SERP context)
        if dataforseo_enabled and dfs_login and dfs_password and remaining - cost_cents >= dfs_cost:
            try:
                for q in derive_queries(text)[:1]:  # one query per post to cap cost
                    dfs_json = call_dataforseo(q, dfs_loc, dfs_lang, dfs_depth, dfs_login, dfs_password)
                    cost_cents += dfs_cost
                    break
            except Exception as e:
                dfs_json = {"error": str(e)}

        # Firecrawl Agent (optional deep extraction)
        if firecrawl_enabled and fc_key and remaining - cost_cents >= fc_cost:
            try:
                prompt = (
                    "Extract concise pricing and positioning details for any tools mentioned in this discussion. "
                    "Return a short JSON with fields: tool, pricing_highlights, positioning."
                )
                fc_json = call_firecrawl_agent(prompt, fc_model, fc_max, fc_key)
                cost_cents += fc_cost
            except Exception as e:
                fc_json = {"error": str(e)}

        # Browser Use Agent (optional: live web research)
        if bu_enabled and bu_key and remaining - cost_cents >= bu_cost:
            try:
                bu_json = call_browser_use_enrich(text, source)
                cost_cents += bu_cost
            except Exception as e:
                bu_json = {"error": str(e)}

        if cost_cents == 0:
            print(f'No providers called for post {post_id} (missing keys or budget).')
            continue

        insert_enrichment(con, post_id, json.dumps(px_json) if px_json else None,
                          json.dumps(dfs_json) if dfs_json else None,
                          json.dumps(fc_json) if fc_json else None,
                          json.dumps(bu_json) if bu_json else None,
                          cost_cents, int(time.time()))
        incr("enrichment_cost_cents", cost_cents)
        remaining -= cost_cents
        print(f'Enriched post {post_id} with cost {cost_cents}c. Remaining today: {remaining}c')
        if remaining <= 0:
            print('Daily budget reached.')
            break

    record_run("enrich")


if __name__ == '__main__':
    enrich_once()
