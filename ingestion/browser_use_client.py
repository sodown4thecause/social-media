from __future__ import annotations
import asyncio
import os
from typing import Optional, Type, TypeVar
from pydantic import BaseModel
from browser_use_sdk.v3 import AsyncBrowserUse

from .env_loader import load_local_env
from .logging_config import log

T = TypeVar("T", bound=BaseModel)

_api_key: str | None = None


def set_api_key(key: str) -> None:
    global _api_key
    _api_key = key


def _get_key() -> str:
    global _api_key
    if _api_key:
        return _api_key
    load_local_env()
    _api_key = os.getenv("BROWSER_USE_API_KEY", "")
    return _api_key


async def _run_task(
    task: str,
    output_schema: type[BaseModel] | None = None,
    workspace_id: str | None = None,
    model: str = "bu-mini",
    max_retries: int = 2,
    timeout_seconds: int = 120,
) -> str | BaseModel:
    key = _get_key()
    if not key:
        log.warning("No BROWSER_USE_API_KEY set, skipping browser-use task")
        return "" if output_schema is None else output_schema()

    client = AsyncBrowserUse(api_key=key)
    for attempt in range(max_retries + 1):
        try:
            result = await client.run(
                task,
                model=model,
                output_schema=output_schema,
                workspace_id=workspace_id,
            )
            return result.output
        except Exception as e:
            log.warning("browser-use task failed", extra={
                "attempt": attempt + 1,
                "max_retries": max_retries + 1,
                "error": str(e),
            })
            if attempt == max_retries:
                raise
            await asyncio.sleep(2 ** attempt)
    return "" if output_schema is None else output_schema()


def run_task(task: str, output_schema: type[BaseModel] | None = None) -> str | BaseModel:
    """Synchronous wrapper for browser-use agent tasks."""
    return asyncio.run(_run_task(task, output_schema=output_schema))


def run_task_async(task: str, output_schema: type[BaseModel] | None = None) -> asyncio.Task:
    return asyncio.create_task(_run_task(task, output_schema=output_schema))
