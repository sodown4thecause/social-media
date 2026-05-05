from __future__ import annotations
import logging
import sys
import json
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        payload = {
            "ts": ts,
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            payload["exc"] = repr(record.exc_info[1])
        for key in ("source", "post_id", "batch_size", "count", "cost_cents", "cluster", "label", "url", "error"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        return json.dumps(payload, default=str)


def setup(name: str | None = None, level: int = logging.INFO, json_output: bool = False) -> logging.Logger:
    logger = logging.getLogger(name or "lgi")
    logger.setLevel(level)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        if json_output:
            h.setFormatter(JsonFormatter())
        else:
            h.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s %(error)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
                defaults={"error": ""},
            ))
        logger.addHandler(h)
    return logger


# Module-level loggers
log = setup("lgi.ingest")
prefilter_log = setup("lgi.prefilter")
intent_log = setup("lgi.intent")
enrich_log = setup("lgi.enrich")
review_log = setup("lgi.review")
