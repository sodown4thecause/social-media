from __future__ import annotations
import json
import re
import time
from typing import Dict, List, Tuple
from dataclasses import dataclass
from urllib.parse import quote

from pydantic import BaseModel, Field, ValidationError, conint

from .config import AppConfig
from .db import connect, init_db
from .llm_generator import LLMGenerator
from .logging_config import log
from .metrics import incr, record_run


class ScoreBreakdown(BaseModel):
    hook: conint(ge=0, le=30) = 0
    specificity: conint(ge=0, le=25) = 0
    credibility: conint(ge=0, le=25) = 0
    voice_fit: conint(ge=0, le=10) = 0
    risk_inverse: conint(ge=0, le=10) = 0

    def total(self) -> int:
        return int(self.hook + self.specificity + self.credibility + self.voice_fit + self.risk_inverse)

    def model_dump_json(self, **kwargs):
        return super().model_dump_json(**kwargs)


class Candidate(BaseModel):
    tone: str = Field(..., pattern=r"^(casual\s+helpful)$")
    angle: str = Field(..., pattern=r"^(cost|simplicity|ai-shift)$")
    text: str = Field(..., min_length=30, max_length=400)

    def total_score(self) -> int:
        return self.score_breakdown.total()

    score_breakdown: ScoreBreakdown


@dataclass
class Post:
    id: int
    source: str
    text: str
    cluster: str
    confidence: float


def simple_generate(post: Post, angle: str) -> str:
    name = "FlowIntent"
    url = tracking_url(post.source, post.cluster, angle)
    pricing = "$39/mo after a free month"

    templates = {
        "cost": {
            "too expensive": (
                f"If price is the blocker, {name} might work — {pricing}. "
                f"It blends DataForSEO + Perplexity + Firecrawl in a chat interface. "
                f"Way leaner than a $100+ Ahrefs plan. Worth a look? {url}"
            ),
            "pricing transparency": (
                f"{pricing} — simple and transparent. {name} gives you SEO data "
                f"from DataForSEO, Perplexity, and Firecrawl in a chat. "
                f"Free month to try it out. {url}"
            ),
            "looking for alternative": (
                f"For a lean alternative, check {name} — {pricing}. "
                f"Chat-based interface instead of a complex dashboard. "
                f"Covers keyword research, content, technical SEO. {url}"
            ),
            "SEO stack fatigue": (
                f"Totally get the dashboard fatigue. {name} consolidates into one chat — "
                f"ask SEO questions, get answers from DataForSEO + Perplexity + Firecrawl. "
                f"{pricing}. {url}"
            ),
            "default": (
                f"If budget matters, {name} is {pricing}. "
                f"Chat-based SEO tool that's surprisingly capable for the price. {url}"
            ),
        },
        "simplicity": {
            "too complex": (
                f"I had the same issue with bloated SEO tools. {name} is just a chat — "
                f"ask what you need, get SEO answers. No dashboard to learn. "
                f"{pricing}. {url}"
            ),
            "SEO stack fatigue": (
                f"Juggling too many dashboards is exhausting. {name} is one chat interface "
                f"that pulls from DataForSEO, Perplexity, Firecrawl, and Exa. "
                f"Much simpler. {url}"
            ),
            "looking for alternative": (
                f"Something simpler? {name} is chat-based — no complex UI. "
                f"You ask SEO questions, it gives you answers with real data. "
                f"{pricing}. {url}"
            ),
            "agency frustration": (
                f"Bringing it in-house doesn't have to be hard. {name} is a chat interface "
                f"for SEO — ask questions naturally, get data-backed answers. "
                f"Easier than most agency dashboards. {url}"
            ),
            "default": (
                f"If you want simple, {name} is chat-based. Ask SEO questions, get answers. "
                f"No steep learning curve. {url}"
            ),
        },
        "ai-shift": {
            "AI curiosity": (
                f"AI works best for SEO when it frames intent, not just keywords. "
                f"{name} uses AI SDK 6 with RAG to personalize answers from your data. "
                f"Plus brand voice extraction and content gen. {url}"
            ),
            "AI for SEO": (
                f"{name} is built for this — AI-powered SEO chat with DataForSEO, "
                f"Perplexity, Firecrawl, and Exa blended in. Plus RAG from your onboarding. "
                f"{pricing}. {url}"
            ),
            "GEO/answer engines": (
                f"For AEO/GEO, look at {name} — it's updated weekly with latest research "
                f"on answer engine optimization, AI Overviews, and gets you cited. "
                f"Chat-based, not another dashboard. {url}"
            ),
            "brand voice/content gen": (
                f"{name} has brand voice AI — it learns your tone and generates content "
                f"and images that sound like you. Built into the chat interface. {url}"
            ),
            "keyword research help": (
                f"For keyword research, {name} blends DataForSEO data in a chat. "
                f"Ask naturally instead of building complex filters. "
                f"AI helps cluster and prioritize. {url}"
            ),
            "default": (
                f"AI-first SEO is where things are heading. {name} is built on AI SDK 6 "
                f"with RAG personalization. Ask questions, get smart answers. "
                f"Weekly research updates too. {url}"
            ),
        },
    }

    angle_templates = templates.get(angle, templates["cost"])
    text = angle_templates.get(post.cluster, angle_templates.get("default", angle_templates[list(angle_templates.keys())[0]]))
    return text[:400]


def tracking_url(source: str, cluster: str, angle: str) -> str:
    campaign = quote(cluster.lower().replace(" ", "_").replace("/", "_"))
    content = quote(angle.lower().replace(" ", "_"))
    return (
        "https://flowintent.com/"
        f"?utm_source={quote(source)}"
        "&utm_medium=reply"
        f"&utm_campaign={campaign}"
        f"&utm_content={content}"
    )


def apply_tracking_url(text: str, post: Post, angle: str) -> str:
    url = tracking_url(post.source, post.cluster, angle)
    tracked = re.sub(
        r"https?://(?:www\.)?flowintent\.com/?(?:\?[^\s)]*)?",
        url,
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    if tracked == text:
        tracked = re.sub(r"\bflowintent\.com\b", url, text, count=1, flags=re.IGNORECASE)
    if tracked == text and "flowintent" in text.lower():
        candidate = f"{text.rstrip()} {url}"
        if len(candidate) <= 400:
            tracked = candidate
    return tracked[:400]


def generate_with_llm(post: Post, angle: str, product=None) -> str:
    """Try LLM first, fall back to template if no API key or request fails."""
    cfg = AppConfig.from_file()
    llm = LLMGenerator(product=cfg.product if hasattr(cfg, 'product') else None)
    result = llm.generate(post.text, post.cluster, angle)
    if result:
        return result
    return simple_generate(post, angle)


def score_candidate(post: Post, text: str) -> ScoreBreakdown:
    t = text.lower()
    hook = 0
    if any(s in t for s in ["quick", "happy to share", "lightweight", "no-fluff", "interested?", "worth a look"]):
        hook = 20
    if t.endswith("?"):
        hook = min(30, hook + 8)

    specificity = 0
    if any(k in post.text.lower() for k in ["ahrefs", "semrush", "pricing", "alternative", "migrate", "agency", "roi", "keyword", "geo", "aeo"]):
        specificity = 18

    credibility = 0
    if any(s in t for s in ["we've seen", "we have seen", "we've helped", "works well when", "i've used", "i have used"]):
        credibility = 16

    voice_fit = 8 if len(text) <= 220 and "!" not in text else 6

    risk_inverse = 9 if ("free" not in t and "guarantee" not in t) else 6

    return ScoreBreakdown(
        hook=hook,
        specificity=specificity,
        credibility=credibility,
        voice_fit=voice_fit,
        risk_inverse=risk_inverse,
    )


def generate_and_score(db_path: str = "data.sqlite3", top_n_posts: int = 30) -> None:
    cfg = AppConfig.from_file()
    con = connect(cfg.db_path)
    init_db(con)

    cur = con.execute(
        """
        SELECT p.id, p.source, p.text, i.cluster, i.confidence
        FROM posts p
        JOIN intents i ON i.post_id = p.id
        LEFT JOIN candidates c ON c.post_id = p.id
        WHERE c.post_id IS NULL
        ORDER BY i.confidence DESC, p.prefilter_score DESC, p.created_at DESC
        LIMIT ?
        """,
        (top_n_posts,),
    )
    posts = [Post(*r) for r in cur.fetchall()]
    if not posts:
        print("No posts needing candidates.")
        return

    angles = ["cost", "simplicity", "ai-shift"]
    created = 0
    now = int(time.time())

    for post in posts:
        for angle in angles:
            try:
                text = generate_with_llm(post, angle)
                text = apply_tracking_url(text, post, angle)
                sb = score_candidate(post, text)
                cand = Candidate(tone="casual helpful", angle=angle, text=text, score_breakdown=sb)
                total = cand.total_score()
                con.execute(
                    """
                    INSERT INTO candidates(post_id, tone, angle, text, score_breakdown, total_score)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (post.id, cand.tone, cand.angle, cand.text, cand.score_breakdown.model_dump_json(), float(total)),
                )
                created += 1
            except ValidationError as ve:
                print(f"Validation failed for post {post.id} angle {angle}: {ve}")
                continue
    con.commit()
    incr("candidates_generated", created)
    record_run("generate_and_score")
    print(f"Created {created} candidates across {len(posts)} posts.")


if __name__ == "__main__":
    generate_and_score()
