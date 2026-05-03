from __future__ import annotations
import json
import math
import time
from typing import Dict, List, Tuple
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError, conint, confloat

from .db import connect, init_db


class ScoreBreakdown(BaseModel):
    hook: conint(ge=0, le=30) = 0
    specificity: conint(ge=0, le=25) = 0
    credibility: conint(ge=0, le=25) = 0
    voice_fit: conint(ge=0, le=10) = 0
    risk_inverse: conint(ge=0, le=10) = 0

    def total(self) -> int:
        return int(self.hook + self.specificity + self.credibility + self.voice_fit + self.risk_inverse)


class Candidate(BaseModel):
    tone: str = Field(..., pattern=r"^(casual\s+helpful)$")
    angle: str = Field(..., pattern=r"^(cost|simplicity|ai-shift)$")
    text: str = Field(..., min_length=30, max_length=400)
    score_breakdown: ScoreBreakdown

    def total_score(self) -> int:
        return self.score_breakdown.total()


@dataclass
class Post:
    id: int
    source: str
    text: str
    cluster: str
    confidence: float


def simple_generate(post: Post, angle: str) -> str:
    # Lightweight template-based generator respecting "casual helpful" tone
    base = post.text.strip().split("\n")[0]
    base = base[:240]
    if angle == "cost":
        msg = (
            "If price is the blocker, a lean stack might be enough. "
            "Happy to share how teams trim tooling costs without losing signal. "
            "Quick breakdown?"
        )
    elif angle == "simplicity":
        msg = (
            "Totally get the tool fatigue. "
            "We’ve seen success by focusing on the 2–3 steps that move the needle and dropping the rest. "
            "Want a quick, no-fluff flow?"
        )
    else:  # ai-shift
        msg = (
            "AI helps most when it frames intent, not just keywords. "
            "Can share a lightweight way to cluster pains and reply faster. "
            "Interested?"
        )
    return f"{msg}"


def score_candidate(post: Post, text: str) -> ScoreBreakdown:
    # Heuristic scoring aligned with rubric
    t = text.lower()
    hook = 0
    if any(s in t for s in ["quick", "happy to share", "lightweight", "no-fluff", "interested?"]):
        hook = 20
    if t.endswith("?"):
        hook = min(30, hook + 8)

    specificity = 0
    if any(k in post.text.lower() for k in ["ahrefs", "semrush", "pricing", "alternative", "migrate", "agency", "roi"]):
        specificity = 18

    credibility = 0
    if any(s in t for s in ["we’ve seen", "we've seen", "we’ve helped", "works well when"]):
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
    con = connect(db_path)
    init_db(con)

    # Pull recent intent-qualified posts without candidates
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
                text = simple_generate(post, angle)
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
    print(f"Created {created} candidates across {len(posts)} posts.")


if __name__ == "__main__":
    generate_and_score()
