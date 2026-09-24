"""Unified web retrieval with safe defaults and structured results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

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
        import httpx
        from bs4 import BeautifulSoup

        response = httpx.get(url, follow_redirects=True, timeout=30, headers={"User-Agent": "web-scraper-toolkit/2.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        text = " ".join(soup.get_text(" ", strip=True).split())[:max_chars]
        links = [urljoin(str(response.url), a["href"]) for a in soup.select("a[href]") if a.get("href")]
        metadata: dict[str, Any] = {"status": response.status_code, "content_type": response.headers.get("content-type", "")}
        if selectors:
            from parsel import Selector
            selected = Selector(text=response.text)
            metadata["selected"] = {name: selected.css(selector).getall() for name, selector in selectors.items()}
        return ScrapeResult(True, str(response.url), title, text, links[:2000], metadata, "httpx")
    except Exception as exc:
        return ScrapeResult(False, url, error=str(exc))


__all__ = ["ScrapeResult", "scrape_web"]
