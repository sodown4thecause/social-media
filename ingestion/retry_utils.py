from __future__ import annotations
import functools
import time
from typing import Dict, Callable, Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from requests.exceptions import RequestException, HTTPError, ConnectionError, Timeout
from .logging_config import log

RETRYABLE_EXCEPTIONS = (RequestException, HTTPError, ConnectionError, Timeout, OSError)


def retry_config():
    """Get retry config lazily to avoid circular imports."""
    from .config import AppConfig
    cfg = AppConfig.from_file()
    return cfg.retry


def http_retry(func: Callable = None, *, max_attempts: int = 3, min_wait: float = 1.0, max_wait: float = 30.0):
    """Decorator: retry HTTP calls with exponential backoff."""
    def decorator(f: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(log, "WARNING"),
            reraise=True,
        )
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper
    if func:
        return decorator(func)
    return decorator


class CircuitBreaker:
    """Per-source circuit breaker that trips after N consecutive failures."""

    def __init__(self, name: str, failure_threshold: int = 3, cooldown_seconds: float = 300.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: int = 0
        self._last_failure: float = 0.0
        self._tripped_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._failures < self.failure_threshold:
            return False
        if time.time() - self._tripped_at > self.cooldown_seconds:
            # Cooldown elapsed, reset to half-open
            self._failures = 0
            log.info("Circuit breaker reset to half-open", extra={"source": self.name})
            return False
        return True

    def success(self) -> None:
        self._failures = 0

    def failure(self) -> None:
        self._failures += 1
        self._last_failure = time.time()
        if self._failures >= self.failure_threshold:
            self._tripped_at = time.time()
            log.warning("Circuit breaker tripped", extra={
                "source": self.name, "failures": self._failures,
                "cooldown_seconds": self.cooldown_seconds,
            })
