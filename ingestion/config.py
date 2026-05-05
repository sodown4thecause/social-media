from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError


class ProductConfig(BaseModel):
    name: str = "FlowIntent"
    tagline: str = "AI-powered chat interface for SEO/GEO"
    url: str = "https://flowintent.com"
    key_features: List[str] = Field(default_factory=lambda: [
        "SEO/GEO chatbot interface — ask questions naturally instead of learning complex dashboards",
        "Brand voice extraction for AI content generation + image generation",
        "RAG personalization from onboarding data",
        "Weekly updated research, case studies, whitepapers, and SEO/AEO insights",
        "Multi-source data blending: DataForSEO, Perplexity, Firecrawl, Exa, Peec.ai MCPs"
    ])
    differentiators: List[str] = Field(default_factory=lambda: [
        "Chat-based UX — no complex dashboards or steep learning curves",
        "Brand voice AI that learns your tone for content generation",
        "RAG-powered personalization from your onboarding data",
        "Blends DataForSEO, Perplexity, Firecrawl, Exa, and Peec.ai MCPs in one chat",
        "Content generation + image generation with your brand voice",
        "Weekly fresh research, case studies, whitepapers, and SEO/AEO insights",
        "Free month trial, then $39/mo — vs $100+/mo for Ahrefs/Semrush"
    ])
    pricing_hint: str = "Free month trial, then $39/mo"
    competitors: List[str] = Field(default_factory=lambda: [
        "ahrefs", "semrush", "moz", "screaming frog", "surferseo"
    ])
    tone_guidelines: str = (
        "Casual, helpful, authentic. Never salesy. Sound like a real person sharing experience. "
        "Never use exclamation marks. Mention the product naturally when genuinely relevant. "
        "Never disparage competitors. Focus on value first."
    )


class RedditConfig(BaseModel):
    subreddits: List[str] = Field(default_factory=lambda: [
        "SEO", "bigseo", "marketing", "Entrepreneur", "SaaS",
        "startups", "smallbusiness", "digitalmarketing",
        "content_marketing", "PPC", "juststart", "WordPress",
        "webdev", "SideProject"
    ])
    limit_per_feed: int = Field(default=25, ge=1, le=100)


class HackerNewsConfig(BaseModel):
    enabled: bool = False
    limit: int = Field(default=30, ge=1, le=100)
    min_points: int = Field(default=5, ge=0)


class ProductHuntConfig(BaseModel):
    enabled: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    topic: str = "marketing"
    secondary_topic: str = "artificial-intelligence"


class IndieHackersConfig(BaseModel):
    enabled: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class CapterraConfig(BaseModel):
    enabled: bool = False
    categories: List[str] = Field(default_factory=lambda: ["seo-software", "marketing-analytics"])
    competitor_slugs: List[str] = Field(default_factory=list)
    reviews_per_category: int = Field(default=20, ge=1, le=100)
    max_review_stars: int = Field(default=3, ge=1, le=5)


class G2Config(BaseModel):
    enabled: bool = False
    categories: List[str] = Field(default_factory=lambda: ["seo", "marketing-analytics"])
    competitor_products: List[str] = Field(default_factory=list)
    reviews_per_category: int = Field(default=20, ge=1, le=100)
    max_review_stars: int = Field(default=3, ge=1, le=5)


class XConfig(BaseModel):
    search_queries: List[str] = Field(default_factory=lambda: [
        "SEO chat", "AI SEO tool", "keyword research tool",
        "Ahrefs alternative", "Semrush alternative", "Moz alternative",
        "best SEO tool", "SEO help", "SEO advice", "recommend SEO tool",
        "GEO SEO", "AEO answer engine optimization",
        "Google AI Overview SEO", "content optimization tool",
        "technical SEO help", "backlink strategy"
    ])
    nitter_base: str = "https://nitter.net"
    nitter_fallbacks: List[str] = Field(default_factory=lambda: [
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
        "https://nitter.1d4.us"
    ])
    max_queries_per_run: int = Field(default=5, ge=1, le=50)
    getxapi_product: str = "Latest"


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


class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    min_wait_seconds: float = Field(default=1.0, ge=0.1)
    max_wait_seconds: float = Field(default=30.0, ge=1.0)
    circuit_breaker_failures: int = Field(default=3, ge=1)
    circuit_breaker_cooldown_minutes: int = Field(default=5, ge=1)


class EnrichmentConfig(BaseModel):
    daily_budget_cents: int = Field(default=150, ge=0)
    intents: List[str] = Field(default_factory=lambda: [
        "pricing transparency", "looking for alternative", "ROI/benchmarking",
        "AI for SEO", "AI curiosity", "SEO stack fatigue",
        "GEO/answer engines", "brand voice/content gen"
    ])
    perplexity_enabled: bool = True
    perplexity_model: str = "sonar-pro"
    perplexity_cost_cents: int = Field(default=5, ge=0)
    dataforseo_enabled: bool = True
    dataforseo_location_name: str = "United States"
    dataforseo_language_name: str = "English"
    dataforseo_depth: int = Field(default=10, ge=1, le=100)
    dataforseo_cost_cents: int = Field(default=25, ge=0)
    firecrawl_enabled: bool = True
    firecrawl_model: str = "spark-1-mini"
    firecrawl_max_credits: int = Field(default=50, ge=1)
    firecrawl_cost_cents: int = Field(default=20, ge=0)
    browser_use_enabled: bool = True
    browser_use_cost_cents: int = Field(default=30, ge=0)


class LeadScoringConfig(BaseModel):
    intent_weights: Dict[str, int] = Field(default_factory=lambda: {
        "looking for alternative": 30,
        "SEO stack fatigue": 28,
        "pricing transparency": 25,
        "AI for SEO": 25,
        "GEO/answer engines": 25,
        "brand voice/content gen": 22,
        "too expensive": 20,
        "ROI/benchmarking": 18,
        "agency frustration": 18,
        "too complex": 15,
        "AI curiosity": 15,
        "keyword research help": 12,
        "content optimization": 12,
        "technical SEO": 10,
        "SERP analysis": 10,
        "link building": 8,
        "local SEO": 8,
        "migration help": 5,
        "integration friction": 5,
    })
    source_quality: Dict[str, int] = Field(default_factory=lambda: {
        "reddit": 10, "capterra": 15, "g2": 15,
        "x": 8, "producthunt": 12, "indiehackers": 10, "hackernews": 5
    })
    negative_sentiment_bonus: int = 20
    enrichment_bonus: int = 5
    auto_create_threshold: float = 0.6
    high_value_threshold: int = 50


class PostingConfig(BaseModel):
    enabled: bool = False
    dry_run_default: bool = True
    auto_post: bool = False
    reddit_max_per_hour: int = Field(default=3, ge=1)
    reddit_max_per_day: int = Field(default=20, ge=1)
    reddit_cooldown_seconds: int = Field(default=600, ge=0)
    x_max_per_hour: int = Field(default=5, ge=1)
    x_max_per_day: int = Field(default=30, ge=1)
    x_cooldown_seconds: int = Field(default=720, ge=0)


class AppConfig(BaseModel):
    db_path: str = "data.sqlite3"
    min_interval_minutes: int = Field(default=10, ge=1)
    prefilter_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    product: ProductConfig = Field(default_factory=ProductConfig)
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
    lead_scoring: LeadScoringConfig = Field(default_factory=LeadScoringConfig)
    posting: PostingConfig = Field(default_factory=PostingConfig)

    @classmethod
    def from_file(cls, path: str = "config.json", env_prefix: str = "LGI_") -> AppConfig:
        from .env_loader import load_local_env
        load_local_env()

        data: dict = {}
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
        from .config_env import apply_env_overrides
        data = apply_env_overrides(data, env_prefix)
        return cls(**data)

    def model_dump_flat(self) -> dict:
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
            "x_max_queries_per_run": self.x.max_queries_per_run,
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
            "dataforseo_location_name": self.enrichment.dataforseo_location_name,
            "dataforseo_language_name": self.enrichment.dataforseo_language_name,
            "dataforseo_depth": self.enrichment.dataforseo_depth,
            "dataforseo_cost_cents": self.enrichment.dataforseo_cost_cents,
            "firecrawl_enabled": self.enrichment.firecrawl_enabled,
            "firecrawl_model": self.enrichment.firecrawl_model,
            "firecrawl_max_credits": self.enrichment.firecrawl_max_credits,
            "firecrawl_cost_cents": self.enrichment.firecrawl_cost_cents,
            "browser_use_enabled": self.enrichment.browser_use_enabled,
            "browser_use_cost_cents": self.enrichment.browser_use_cost_cents,
        }
