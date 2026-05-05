from __future__ import annotations
import functools
import html
import re
import requests
import feedparser
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .logging_config import log

RETRYABLE = (requests.exceptions.RequestException, requests.exceptions.HTTPError,
             requests.exceptions.ConnectionError, requests.exceptions.Timeout)
BREAK_TAG_RE = re.compile(r"</?(?:p|br|div|li|tr|td|h[1-6])[^>]*>", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type(RETRYABLE),
    reraise=True,
)
def fetch_feed(url: str, timeout: int = 30) -> feedparser.FeedParserDict:
    r = requests.get(url, timeout=timeout, headers={
        "User-Agent": "LeanGrowthIntelligence/1.0",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    r.raise_for_status()
    return feedparser.parse(r.content)


def clean_feed_text(value: str | None) -> str:
    if not value:
        return ""
    text = BREAK_TAG_RE.sub(" ", value)
    text = HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
