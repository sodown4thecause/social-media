from __future__ import annotations
import os
from typing import Optional, List

import requests

from .config import AppConfig, ProductConfig
from .env_loader import load_local_env
from .logging_config import log
from .retry_utils import http_retry

XAI_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-3-mini"


def _build_system_prompt(product: ProductConfig) -> str:
    features = "\n".join(f"- {f}" for f in product.key_features[:4])
    diffs = "\n".join(f"- {d}" for d in product.differentiators[:4])
    return (
        f"You are a helpful, casual commenter on SaaS/SEO/marketing forums. "
        f"You genuinely use {product.name} ({product.url}) — {product.tagline}.\n\n"
        f"Key features you can mention naturally:\n{features}\n\n"
        f"Why you prefer it over expensive alternatives:\n{diffs}\n\n"
        f"Pricing: {product.pricing_hint}\n\n"
        f"Tone guidelines: {product.tone_guidelines}\n\n"
        "Write a single short reply (under 400 chars) that is helpful and authentic. "
        "Only mention the product when genuinely relevant to the question. "
        "Never be pushy. Lead with value. If you mention it, frame it as personal experience, not a pitch."
    )


@http_retry(max_attempts=2, min_wait=1.0, max_wait=10.0)
def _call_xai(messages: List[dict], model: str = DEFAULT_MODEL, api_key: str | None = None,
              timeout: int = 30) -> Optional[str]:
    load_local_env()
    key = api_key or os.getenv("XAI_API_KEY", "").strip()
    if not key:
        return None
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": 200,
        "temperature": 0.7,
    }
    r = requests.post(
        XAI_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


class LLMGenerator:
    """LLM-based reply generator using xAI Grok."""

    def __init__(self, prefer: str = "xai", product: ProductConfig | None = None,
                 model: str = DEFAULT_MODEL):
        self.prefer = prefer
        self.product = product or ProductConfig()
        self.model = model

    def generate(self, post_text: str, cluster: str, angle: str) -> Optional[str]:
        prompt = _build_prompt(post_text, cluster, angle, self.product)
        messages = [
            {"role": "system", "content": _build_system_prompt(self.product)},
            {"role": "user", "content": prompt},
        ]

        result = None
        try:
            result = _call_xai(messages, model=self.model)
            if result:
                log.debug("LLM generated reply", extra={"provider": "xai", "length": len(result)})
        except Exception as e:
            log.warning("LLM generation failed with xai", extra={"error": str(e)})

        if not result:
            log.info("No LLM provider available, falling back to template generator")
            return None

        if len(result) > 400:
            result = result[:397] + "..."

        return result


def _build_prompt(post_text: str, cluster: str, angle: str, product: ProductConfig) -> str:
    p = product
    angle_hints = {
        "cost": (
            f"Focus on cost-effectiveness. {p.pricing_hint} — that's a fraction of what tools like "
            f"Ahrefs or Semrush cost ($100+/mo). Mention {p.name} as a budget-friendly option."
        ),
        "simplicity": (
            f"Focus on simplicity and reducing tool fatigue. {p.name} uses a chat interface — no "
            f"complex dashboards to learn. Ask SEO questions naturally and get answers blended from "
            f"DataForSEO, Perplexity, Firecrawl, and Exa."
        ),
        "ai-shift": (
            f"Focus on how AI can transform SEO workflows. {p.name} ({p.url}) uses AI SDK 6 with "
            f"RAG personalization, brand voice extraction for content generation, and weekly updated "
            f"research. It's an AI-first approach to SEO."
        ),
    }
    hint = angle_hints.get(angle, "")

    cluster_context = {
        "too expensive": f"Person is frustrated with high tool costs. {p.name} costs {p.pricing_hint}.",
        "too complex": f"Person finds current tools too complex. {p.name} is chat-based and simple.",
        "looking for alternative": f"Person is actively seeking alternatives. {p.name} could be their solution.",
        "AI curiosity": f"Person is curious about AI for SEO. {p.name} is an AI-powered SEO chat tool.",
        "SEO stack fatigue": f"Person is tired of juggling multiple tools. {p.name} consolidates into one chat interface.",
        "keyword research help": f"Person needs keyword research help. {p.name} blends DataForSEO data in chat.",
        "content optimization": f"Person needs content optimization help. {p.name} offers brand voice AI + content gen.",
        "AI for SEO": f"Person is exploring AI SEO tools. {p.name} is purpose-built for this.",
        "GEO/answer engines": f"Person is asking about GEO/AEO. {p.name} covers answer engine optimization.",
        "brand voice/content gen": f"Person needs content generation help. {p.name} has brand voice AI + image gen.",
        "pricing transparency": f"Person wants clear pricing. {p.name} is {p.pricing_hint}.",
        "agency frustration": f"Person is done with agencies. {p.name} helps bring SEO in-house affordably.",
    }
    context = cluster_context.get(cluster, f"Intent cluster: {cluster}")

    return (
        f"{context}\n"
        f"Angle: {angle} — {hint}\n\n"
        f"Original post:\n{post_text[:1200]}\n\n"
        "Write a single short, casual, helpful reply (under 400 chars). "
        "Do NOT use markdown, hashtags, or emojis. Do NOT sound like an ad. "
        "Only mention the product if it genuinely helps answer the question."
    )