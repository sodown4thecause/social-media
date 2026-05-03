from __future__ import annotations
import json
import os
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError


class RedditConfig(BaseModel):
    subreddits: List[str] = Field(default_factory=lambda: ["SEO", "bigseo", "marketing"])
    limit_per_feed: int = Field(default=25, ge=1, le=100)


class HackerNewsConfig(BaseModel):
    enabled: bool = False
    limit: int = Field(default=30, ge=1, le=100)
    min_points: int = Field(default=5, ge=0)


class ProductHuntConfig(BaseModel):
    enabled: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    topic: str = "developer-tools"


class IndieHackersConfig(BaseModel):
    enabled: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class CapterraConfig(BaseModel):
    enabled: bool = False
    categories: List[str] = Field(default_factory=lambda: ["seo-software", "marketing-analytics"])
    reviews_per_category: int = Field(default=20, ge=1, le=100)


class G2Config(BaseModel):
    enabled: bool = False
    categories: List[str] = Field(default_factory=lambda: ["seo", "marketing-analytics"])
    reviews_per_category: int = Field(default=20, ge=1, le=100)


class XConfig(BaseModel):
    search_queries: List[str] = Field(default_factory=list)
    nitter_base: str = "https://nitter.net"


class JinaConfig(BaseModel):
    model: str = "jina-embeddings-v5-text-nano"
    dimensions: int = Field(default=512, ge=1)
    batch_size: int = Field(default=32, ge=1, le=100)


class IntentConfig(BaseModel):
    threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    avg_top2_threshold: float = Field(default=0.42, ge=0.0, le=1.0)
    batch_size: int = Field(default=100, ge=1, le=500)
    local_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    local_avg_top2_threshold: float = Field(default=0.37, ge=0.0, le=1.0)


class EnrichmentConfig(BaseModel):
    daily_budget_cents: int = Field(default=150, ge=0)
    intents: List[str] = Field(
        default_factory=lambda: ["pricing transparency", "looking for alternative", "ROI/benchmarking"]
    )
    perplexity_enabled: bool = True
    perplexity_model: str = "sonar-pro"
    perplexity_cost_cents: int = Field(default=5, ge=0)
    dataforseo_enabled: bool = True
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    dataforseo_location_name: str = "United States"
    dataforseo_language_name: str = "English"
    dataforseo_depth: int = Field(default=10, ge=1, le=100)
    dataforseo_cost_cents: int = Field(default=25, ge=0)
    firecrawl_enabled: bool = True
    firecrawl_model: str = "spark-1-mini"
    firecrawl_max_credits: int = Field(default=50, ge=1)
    firecrawl_cost_cents: int = Field(default=20, ge=0)


class AppConfig(BaseModel):
    db_path: str = "data.sqlite3"
    min_interval_minutes: int = Field(default=10, ge=1)
    prefilter_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    reddit: RedditConfig = Field(default_factory=RedditConfig)
    hackernews: HackerNewsConfig = Field(default_factory=HackerNewsConfig)
    producthunt: ProductHuntConfig = Field(default_factory=ProductHuntConfig)
    indiehackers: IndieHackersConfig = Field(default_factory=IndieHackersConfig)
    capterra: CapterraConfig = Field(default_factory=CapterraConfig)
    g2: G2Config = Field(default_factory=G2Config)
    x: XConfig = Field(default_factory=XConfig)
    jina: JinaConfig = Field(default_factory=JinaConfig)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)

    @classmethod
    def from_file(cls, path: str = "config.json", env_prefix: str = "LGI_") -> AppConfig:
        """Load config from JSON file, with env var overrides."""
        data: dict = {}
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)

        # Apply env var overrides (e.g. LGI_DB_PATH, LGI_REDDIT__SUBREDDITS)
        from .config_env import apply_env_overrides
        data = apply_env_overrides(data, env_prefix)

        return cls(**data)

    def model_dump_flat(self) -> dict:
        """Produce a backwards-compatible flat dict for code that expects old config.json shape."""
        return {
            "db_path": self.db_path,
            "min_interval_minutes": self.min_interval_minutes,
            "prefilter_threshold": self.prefilter_threshold,
            "reddit_subreddits": self.reddit.subreddits,
            "reddit_limit_per_feed": self.reddit.limit_per_feed,
            "hackernews_enabled": self.hackernews.enabled,
            "hackernews_limit": self.hackernews.limit,
            "hackernews_min_points": self.hackernews.min_points,
            "producthunt_enabled": self.producthunt.enabled,
            "producthunt_limit": self.producthunt.limit,
            "producthunt_topic": self.producthunt.topic,
            "indiehackers_enabled": self.indiehackers.enabled,
            "indiehackers_limit": self.indiehackers.limit,
            "capterra_enabled": self.capterra.enabled,
            "capterra_categories": self.capterra.categories,
            "capterra_reviews_per_category": self.capterra.reviews_per_category,
            "g2_enabled": self.g2.enabled,
            "g2_categories": self.g2.categories,
            "g2_reviews_per_category": self.g2.reviews_per_category,
            "x_search_queries": self.x.search_queries,
            "x_nitter_base": self.x.nitter_base,
            "jina_model": self.jina.model,
            "jina_dimensions": self.jina.dimensions,
            "jina_batch_size": self.jina.batch_size,
            "intent_threshold": self.intent.threshold,
            "intent_avg_top2_threshold": self.intent.avg_top2_threshold,
            "intent_batch_size": self.intent.batch_size,
            "local_intent_threshold": self.intent.local_threshold,
            "local_intent_avg_top2_threshold": self.intent.local_avg_top2_threshold,
            "enrichment_daily_budget_cents": self.enrichment.daily_budget_cents,
            "enrichment_intents": self.enrichment.intents,
            "perplexity_enabled": self.enrichment.perplexity_enabled,
            "perplexity_model": self.enrichment.perplexity_model,
            "perplexity_cost_cents": self.enrichment.perplexity_cost_cents,
            "dataforseo_enabled": self.enrichment.dataforseo_enabled,
            "dataforseo_login": self.enrichment.dataforseo_login,
            "dataforseo_password": self.enrichment.dataforseo_password,
            "dataforseo_location_name": self.enrichment.dataforseo_location_name,
            "dataforseo_language_name": self.enrichment.dataforseo_language_name,
            "dataforseo_depth": self.enrichment.dataforseo_depth,
            "dataforseo_cost_cents": self.enrichment.dataforseo_cost_cents,
            "firecrawl_enabled": self.enrichment.firecrawl_enabled,
            "firecrawl_model": self.enrichment.firecrawl_model,
            "firecrawl_max_credits": self.enrichment.firecrawl_max_credits,
            "firecrawl_cost_cents": self.enrichment.firecrawl_cost_cents,
        }
