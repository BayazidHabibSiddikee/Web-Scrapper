"""Unified web retrieval with safe defaults and structured results."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from security_utils import assert_public_url


@dataclass(frozen=True)
class ScrapeResult:
    ok: bool
    url: str
    title: str | None = None
    text: str = ""
    links: list[str] | None = None
    metadata: dict[str, Any] | None = None
    backend: str | None = None
    error: str | None = None


def scrape_web(url: str, *, selectors: dict[str, str] | None = None, max_chars: int = 20000, screenshot: bool = False) -> ScrapeResult:
    try:
        url = assert_public_url(url)
        from master_pipeline import run_pipeline
        result = asyncio.run(run_pipeline(url=url, screenshot=screenshot, export=False))
        step = result.get("steps", {}).get("scrape", {})
        return ScrapeResult(True, url, step.get("title"), step.get("text", "")[:max_chars], [], step.get("metadata", {}), result.get("backend"))
    except Exception as exc:
        return ScrapeResult(False, url, error=str(exc))


__all__ = ["ScrapeResult", "scrape_web"]
