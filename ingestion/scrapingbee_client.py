from __future__ import annotations

import os
from typing import Any

import requests

from .env_loader import load_local_env

SCRAPINGBEE_URL = "https://app.scrapingbee.com/api/v1/"


def _param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def fetch_html(url: str, *, render_js: bool = True, timeout: int = 60, **params: Any) -> str | None:
    load_local_env()
    key = os.getenv("SCRAPINGBEE_API_KEY", "").strip()
    if not key:
        return None

    query = {
        "api_key": key,
        "url": url,
        "render_js": "true" if render_js else "false",
        "block_ads": "true",
        "block_resources": "false",
        **{key: _param_value(value) for key, value in params.items()},
    }
    response = requests.get(SCRAPINGBEE_URL, params=query, timeout=timeout)
    response.raise_for_status()
    return response.text
