from __future__ import annotations
import os
from typing import List, Dict, Any
import requests

from .env_loader import load_local_env
from .logging_config import log
from .retry_utils import http_retry

JINA_URL = "https://api.jina.ai/v1/embeddings"
DEFAULT_BATCH_SIZE = 32


class JinaClient:
    def __init__(self, api_key: str | None = None, model: str = "jina-embeddings-v5-text-nano",
                 dimensions: int | None = 512, task: str = "retrieval.passage",
                 normalized: bool = True, timeout: int = 30, batch_size: int = DEFAULT_BATCH_SIZE):
        load_local_env()
        key = api_key or os.getenv("JINA_API_KEY") or ""
        key = key.strip()
        if not key:
            raise RuntimeError("Missing JINA_API_KEY environment variable.")
        self.api_key = key
        self.model = model
        self.dimensions = dimensions
        self.task = task
        self.normalized = normalized
        self.timeout = timeout
        self.batch_size = batch_size

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        all_vectors: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            if i > 0:
                log.debug("Embedding batch", extra={"offset": i, "batch_size": len(batch)})
            vecs = self._embed_batch(batch)
            all_vectors.extend(vecs)
        return all_vectors

    @http_retry(max_attempts=3, min_wait=1.0, max_wait=15.0)
    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        payload: Dict[str, Any] = {
            "input": texts,
            "model": self.model,
            "task": self.task,
            "normalized": self.normalized,
        }
        if self.dimensions:
            payload["dimensions"] = int(self.dimensions)
        r = requests.post(JINA_URL, headers=headers, json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return [item["embedding"] for item in data.get("data", [])]
