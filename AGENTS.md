# Project: Lean Growth Intelligence

A multi-source ingestion pipeline that fetches Reddit, HackerNews, ProductHunt, IndieHackers, Capterra, and G2 content, classifies intent via embeddings, generates AI-powered replies promoting FlowIntent (flowintent.com), enriches with external data, and supports human-in-the-loop review and automated posting.

## Product: FlowIntent

AI-powered chat interface for SEO/GEO. Uses AI SDK 6, RAG, DataForSEO, Peec.ai MCPs, Firecrawl, Perplexity, Exa. Key differentiators: chat UX (no dashboards), brand voice AI, RAG personalization, content + image gen, weekly research updates. Pricing: free month trial, then $39/mo.

## Commands

- `python -m ingestion.ingest` — fetch and dedupe posts from all enabled sources
- `python -m ingestion.compute_intents` — embed posts, classify into intent clusters
- `python -m ingestion.generate_and_score` — generate LLM-powered reply candidates
- `python -m ingestion.enrich` — enrich candidate posts with Perplexity/DataForSEO/Firecrawl
- `python -m ingestion.review_cli` — human-in-the-loop review (terminal)
- `streamlit run review/app.py` — full review dashboard (browser UI)
- `python -m ingestion.scheduler` — run full pipeline (M1→M4), `--daemon` for continuous, `--once` for single run
- `python -m ingestion.metrics` — dump Prometheus-style counter/gauge/last_run JSON
- `python -m ingestion.export_leads` — export leads to CSV (`--output`, `--status`, `--min-score`, `--since-days`)
- `python -m ingestion.reddit_poster <candidate_id> [--no-dry-run]` — post approved reply to Reddit via PRAW
- `python -m ingestion.x_poster <candidate_id> [--no-dry-run]` — post approved reply to X via GetXAPI
- `python -m pytest tests/ -v` — run test suite

## Architecture

- `ingestion/config.py` — Pydantic AppConfig with nested models (ProductConfig, LeadScoringConfig, PostingConfig, etc.), env var overrides (LGI_*)
- `ingestion/db.py` — SQLite schema, upserts, intent/candidate/enrichment/lead/sentiment/post_performance tables
- `ingestion/ingest.py` — parallel ingestion via ThreadPoolExecutor (6 workers)
- `ingestion/prefilter.py` — text normalization, recency decay, engagement scoring
- `ingestion/reddit_rss.py` / `hackernews_rss.py` / `producthunt_rss.py` / `indiehackers_rss.py` — RSS feed parsers
- `ingestion/capterra_scraper.py` / `g2_scraper.py` — browser-use powered review scrapers with competitor-specific product support
- `ingestion/browser_use_client.py` — async SDK wrapper for Browser Use Cloud API with structured Pydantic output
- `ingestion/x_search.py` — Nitter RSS search for X/Twitter with fallback instances
- `ingestion/jina_client.py` — Jina embeddings API with batch chunking (32/texts per request)
- `ingestion/local_embed.py` — TF-IDF fallback embedder
- `ingestion/intent_clusters.py` — 19 pain-based + SEO/GEO intent clusters with seed exemplars
- `ingestion/compute_intents.py` — embedding + classification pipeline
- `ingestion/llm_generator.py` — dual-provider LLM client (OpenAI + Anthropic) with FlowIntent-aware system prompt
- `ingestion/generate_and_score.py` — candidate generation (LLM-first, FlowIntent-aware template fallback)
- `ingestion/enrich.py` — enrichment pipeline with budget tracking
- `ingestion/review_cli.py` — CLI approval/editing/rejection flow
- `ingestion/lead_scoring.py` — lead scoring based on intent, confidence, engagement, source quality, sentiment
- `ingestion/export_leads.py` — CSV export for leads with filters
- `ingestion/sentiment.py` — competitor sentiment analysis (keyword-based)
- `ingestion/reddit_poster.py` — Reddit posting via PRAW (free, uses existing credentials)
- `ingestion/x_poster.py` — X/Twitter posting via GetXAPI ($0.001/call)

## Key Config

- Config is in `config.json` (Pydantic-validated)
- Env overrides: `LGI_DB_PATH`, `LGI_REDDIT__SUBREDDITS`, `LGI_HACKERNEWS__ENABLED`, etc.
- Product config: `config.json` → `product` section (name, tagline, url, features, differentiators, pricing, tone)
- Lead scoring: `config.json` → `lead_scoring` section (intent weights, source quality, thresholds)
- Posting controls: `config.json` → `posting` section (enabled, dry_run, rate limits)

## API Keys & Credentials

- Reddit (PRAW): `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD` — **Free**
- X/Twitter (GetXAPI): `GETXAPI_API_KEY` — **$0.001/call, $0.10 free credits**
- X OAuth (backup): `TWITTER_CLIENT_ID`, `TWITTER_CLIENT_SECRET`, `TWITTER_ACCESS_TOKEN`, `TWITTER_REFRESH_TOKEN`
- Embeddings: `JINA_API_KEY`
- LLM: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
- Enrichment: `PERPLEXITY_API_KEY`, `FIRECRAWL_API_KEY`, `BROWSER_USE_API_KEY`, `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`

## Posting Costs

| Platform | Method | Cost |
|----------|--------|------|
| Reddit | PRAW (official API) | **$0** (free) |
| X/Twitter | GetXAPI | **~$1-3/month** ($0.001/call) |
| X/Twitter | Official API | $100/month (NOT recommended) |

## Conventions

- Python 3.10+, type hints throughout
- Tests in `tests/`, run with `python -m pytest tests/ -v`
- No hardcoded secrets — everything comes from env or config.json
- LLM generation gracefully falls back to FlowIntent-aware templates when no API key
- New ingestion sources follow the pattern: fetch_raw() -> prefilter() -> scored_rows() -> ingest.py
- Posting always defaults to dry_run mode; use `--no-dry-run` to actually post
- Start with low posting volume (5-10/day) and ramp up to avoid bans
