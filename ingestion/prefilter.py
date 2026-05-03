import hashlib
import math
import re
from dataclasses import dataclass
from time import time

URL_RE = re.compile(r"https?://\S+|www\.\S+")
WS_RE = re.compile(r"\s+")


@dataclass
class PrefilterResult:
    text: str
    created_at: int
    engagement: float
    recency_score: float
    score: float
    hash: str


def normalize_text(text: str) -> str:
    t = text.strip()
    t = URL_RE.sub("", t)
    t = WS_RE.sub(" ", t)
    return t


def recency_decay(ts: int, half_life_hours: float = 24.0) -> float:
    now = int(time())
    age_h = max(0.0, (now - ts) / 3600.0)
    return 0.5 ** (age_h / half_life_hours)


def engagement_score(upvotes: float | int | None, comments: float | int | None) -> float:
    u = float(upvotes or 0)
    c = float(comments or 0)
    # lightweight saturation
    return math.tanh((u * 0.6 + c * 0.4) / 50.0)


def prefilter(text: str, created_at: int, upvotes: int | None = None, comments: int | None = None) -> PrefilterResult:
    norm = normalize_text(text)
    length_term = min(len(norm) / 280.0, 1.0)  # reward > ~280 chars
    r = recency_decay(created_at)
    e = engagement_score(upvotes, comments)
    score = (0.45 * length_term) + (0.35 * r) + (0.20 * e)
    h = hashlib.sha1(f"{norm}:{created_at}".encode("utf-8")).hexdigest()
    return PrefilterResult(text=norm, created_at=created_at, engagement=e, recency_score=r, score=score, hash=h)
