from __future__ import annotations

import html
import json
import re
from typing import Any, Iterable

SCRIPT_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
ITEMPROP_REVIEW_RE = re.compile(
    r"(?P<chunk>.{0,1400}itemprop=[\"']reviewBody[\"'].{0,2600})",
    re.IGNORECASE | re.DOTALL,
)
RATING_RE = re.compile(r"(?P<rating>[1-5](?:\.\d)?)/5")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_html_text(value: str | None) -> str:
    if not value:
        return ""
    text = TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


def jsonld_blocks(page_html: str) -> list[Any]:
    blocks: list[Any] = []
    for raw in SCRIPT_RE.findall(page_html):
        try:
            blocks.append(json.loads(html.unescape(raw.strip())))
        except json.JSONDecodeError:
            continue
    return blocks


def walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def node_type(value: dict[str, Any]) -> str:
    raw = value.get("@type") or value.get("type") or ""
    if isinstance(raw, list):
        raw = " ".join(str(item) for item in raw)
    return str(raw).lower()


def rating_value(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("ratingValue") or value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def author_name(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("author")
    if isinstance(value, str):
        return value.strip() or None
    return None


def extract_review_nodes(page_html: str) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for block in jsonld_blocks(page_html):
        for node in walk_json(block):
            if not isinstance(node, dict) or "review" not in node_type(node):
                continue
            text = clean_html_text(
                node.get("reviewBody")
                or node.get("description")
                or node.get("text")
                or node.get("name")
            )
            if not text:
                continue
            reviews.append({
                "title": clean_html_text(node.get("name") or node.get("headline")),
                "text": text,
                "rating": rating_value(node.get("reviewRating") or node.get("ratingValue")),
                "author": author_name(node.get("author")),
            })
    if reviews:
        return reviews

    for match in ITEMPROP_REVIEW_RE.finditer(page_html):
        chunk = match.group("chunk")
        cleaned = clean_html_text(chunk)
        if len(cleaned) < 40:
            continue
        rating_match = RATING_RE.search(cleaned)
        reviews.append({
            "title": "",
            "text": cleaned,
            "rating": rating_value(rating_match.group("rating") if rating_match else None),
            "author": None,
        })
    return reviews
