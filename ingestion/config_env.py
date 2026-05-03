from __future__ import annotations
import os
from typing import Any, Dict


def _parse_env_val(raw: str) -> Any:
    """Try to parse env string into int/float/bool, fall back to string."""
    lower = raw.strip().lower()
    if lower in ("true", "yes", "1"):
        return True
    if lower in ("false", "no", "0"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    # comma-separated list
    if "," in raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def apply_env_overrides(data: Dict[str, Any], prefix: str = "LGI_") -> Dict[str, Any]:
    """
    Merge environment variables into a config dict.

    Env vars like LGI_DB_PATH → data["db_path"]
    Nested: LGI_REDDIT__SUBREDDITS → data["reddit"]["subreddits"]
    """
    result: Dict[str, Any] = dict(data)

    def _set_nested(d: Dict[str, Any], keys: list, value: Any) -> None:
        for key in keys[:-1]:
            if key not in d or not isinstance(d[key], dict):
                d[key] = {}
            d = d[key]
        d[keys[-1]] = value

    for key, raw_val in os.environ.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        # Split on __ for nesting (e.g. REDDIT__SUBREDDITS → ["reddit", "subreddits"])
        parts = [p.lower() for p in rest.split("__")]
        # Check if the underscore-less join matches a known legacy flat key
        joined = "".join(p.replace("_", "") for p in parts)
        mapped = _LEGACY_KEY_MAP.get(joined)
        if mapped:
            parts = mapped
        val = _parse_env_val(raw_val)
        _set_nested(result, parts, val)

    return result


_LEGACY_KEY_MAP = {
    "redditsubreddits": ["reddit", "subreddits"],
    "redditlimitperfeed": ["reddit", "limit_per_feed"],
    "hackernewslimit": ["hackernews", "limit"],
    "xsearchqueries": ["x", "search_queries"],
    "xnitterbase": ["x", "nitter_base"],
    "jinamodel": ["jina", "model"],
    "jinadimensions": ["jina", "dimensions"],
    "jinabatchsize": ["jina", "batch_size"],
    "intentthreshold": ["intent", "threshold"],
    "intentavgtop2threshold": ["intent", "avg_top2_threshold"],
    "intentbatchsize": ["intent", "batch_size"],
}
