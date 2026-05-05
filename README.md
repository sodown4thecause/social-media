# Lean Growth Intelligence — Ingestion MVP (M1)

This folder contains a minimal ingestion pipeline that fetches Reddit RSS and optional X (via Nitter search RSS), applies a cheap relevance pre-filter, and stores deduped posts in SQLite.

## Quick start

1. Create and activate a Python 3.10+ environment.
2. Install deps:

```bash
pip install -r requirements.txt
```

3. Configure sources in `config.json`.

4. Run ingestion:

```bash
python -m ingestion.ingest
```

- Database: `data.sqlite3` in project root.
- To schedule every 10 minutes, use OS task scheduler or `cron`.

## Config
- `reddit_subreddits`: list of subreddit names (without `r/`).
- `reddit_limit_per_feed`: items per RSS feed pull.
- `x_search_queries`: list of X search terms (optional; uses Nitter RSS).
- `x_nitter_base`: Nitter instance base URL.
- `prefilter_threshold`: currently used for downstream filtering (M2+), not gating inserts.
- `jina_model`, `jina_dimensions`: Jina embeddings model and output dims (MRL) for M2.
- `intent_threshold`, `intent_avg_top2_threshold`, `intent_batch_size`: M2 thresholds and batch size.

## Notes
- Nitter endpoints may rate-limit; disable X by leaving `x_search_queries` empty.
- This is M1 only; embeddings, intent mapping, scoring, and review UI arrive in M2–M4.

---

# M2 — Embeddings + Intent Clusters

## Prereq
- Get a Jina API key: https://jina.ai/embeddings
- Set env var before running (PowerShell example):

```powershell
$env:JINA_API_KEY="<your-key>"
```

## Run intent classification (batch)

```bash
python -m ingestion.compute_intents
```

This will:
- Create seed embeddings for each intent cluster on first run (stored in SQLite).
- Embed recent high-prefilter posts, then assign best-matching intent if thresholds pass.

If the Jina API key is unavailable, the script falls back to a local TF‑IDF embedder and still classifies intents without external cost.

---

# M3 — Candidate Generation + Scoring

Generate three candidates (angles: cost, simplicity, ai-shift) per post needing replies, scored against the rubric.

```bash
python -m ingestion.generate_and_score
```

This writes candidates into SQLite with a score breakdown JSON. Tone is enforced to "casual helpful".

---

# M4 — Human-in-the-loop Review (CLI)

Review the top-scored candidate at a time; approve, edit, or reject. Approved/edited items are logged.

```bash
python -m ingestion.review_cli
```

Copy the approved/edited text for manual posting on the platform.

## Browser Review App

For a normal website-style review UI:

```bash
python -m webapp.server
```

Then open `http://127.0.0.1:8765`. The app keeps human review in the loop: it lets you inspect signals, edit replies, approve/reject candidates, and save review-site opportunities as leads without sending anything automatically.

---

# M3.5 — Selective Enrichment (Perplexity, DataForSEO, Firecrawl)

Configure `config.json` budget and toggles. Put API keys in your ignored `.env` file, or set them as environment variables:

```powershell
# LLM
$env:XAI_API_KEY="<your-key>"

# Embeddings
$env:JINA_API_KEY="<your-key>"

# Perplexity
$env:PERPLEXITY_API_KEY="<your-key>"

# DataForSEO (or set in config.json)
# Username can be provided as DATAFORSEO_LOGIN or DATAFORSEO_USERNAME
$env:DATAFORSEO_LOGIN="<email>"
# or
$env:DATAFORSEO_USERNAME="<email>"
$env:DATAFORSEO_PASSWORD="<password>"

# Firecrawl
$env:FIRECRAWL_API_KEY="<your-key>"

# Browser Use, for dynamic review-site extraction
$env:BROWSER_USE_API_KEY="<your-key>"

# Reddit posting only; Reddit RSS reading does not need these
$env:REDDIT_CLIENT_ID="<client-id>"
$env:REDDIT_CLIENT_SECRET="<client-secret>"
$env:REDDIT_USERNAME="<username>"
$env:REDDIT_PASSWORD="<password>"

# X posting through GetXAPI
$env:GETXAPI_API_KEY="<your-key>"
```

Run enrichment (respects daily budget and allowed intents):

```bash
python -m ingestion.enrich
```

Notes:
- Providers are called only if enabled and budget allows.
- DataForSEO uses Live Google Organic Advanced for 1 query per post (e.g., "ahrefs pricing").
- Firecrawl `/agent` is optional for deeper extraction under cost cap.
