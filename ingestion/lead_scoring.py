from __future__ import annotations
from typing import Optional

from ingestion.config import AppConfig, LeadScoringConfig


def compute_lead_score(
    intent_cluster: str,
    confidence: float,
    engagement: float,
    source: str,
    has_negative_sentiment: bool = False,
    has_enrichment: bool = None,
    lead_scoring: LeadScoringConfig | None = None,
) -> float:
    if lead_scoring is None:
        cfg = AppConfig.from_file()
        lead_scoring = cfg.lead_scoring

    score = 0.0

    # Intent cluster weight (0-30)
    score += lead_scoring.intent_weights.get(intent_cluster, 5)

    # Confidence score (0-25)
    score += min(25.0, confidence * 25.0)

    # Engagement score (0-15)
    score += min(15.0, engagement * 3.0)

    # Source quality (5-15)
    score += lead_scoring.source_quality.get(source, 5)

    # Bonus: negative sentiment toward competitor
    if has_negative_sentiment:
        score += lead_scoring.negative_sentiment_bonus

    # Bonus: enrichment data present
    if has_enrichment:
        score += lead_scoring.enrichment_bonus

    return round(score, 1)


def is_high_value_lead(score: float, lead_scoring: LeadScoringConfig | None = None) -> bool:
    if lead_scoring is None:
        cfg = AppConfig.from_file()
        lead_scoring = cfg.lead_scoring
    return score >= lead_scoring.high_value_threshold
