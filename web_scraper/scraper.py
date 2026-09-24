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


def scrape_web(
    url: str,
    *,
    selectors: dict[str, str] | None = None,
    max_chars: int = 20000,
    screenshot: bool = False,
    render_js: bool = True,
    timeout: float = 30.0,
) -> ScrapeResult:
    try:
        url = assert_public_url(url)
        import httpx

        current_url = url
        for _ in range(6):
            response = httpx.get(
                current_url,
                follow_redirects=False,
                timeout=timeout,
                headers={"User-Agent": "web-scraper-toolkit/2.0"},
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect response has no Location header")
                current_url = assert_public_url(str(httpx.URL(current_url).join(location)))
                continue
            break
        else:
            raise ValueError("Too many redirects")
        response.raise_for_status()
        html = response.text
        backend = "httpx"
        if render_js and _should_render(html):
            try:
                html = _playwright_html(current_url, int(timeout * 1000))
                backend = "playwright-fallback"
            except Exception as exc:
                response.headers["x-scraper-render-warning"] = str(exc)
        title, text, links, metadata = _parse_html(html, current_url, selectors, max_chars)
        metadata.update({"status": response.status_code, "content_type": response.headers.get("content-type", "")})
        return ScrapeResult(True, current_url, title, text, links, metadata, backend)
    except Exception as exc:
        return ScrapeResult(False, url, error=str(exc))

def _parse_html(html: str, base_url: str, selectors: dict[str, str] | None, max_chars: int) -> tuple[str | None, str, list[str], dict[str, Any]]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    text = " ".join(soup.get_text(" ", strip=True).split())[:max_chars]
    links = [urljoin(base_url, a["href"]) for a in soup.select("a[href]") if a.get("href")]
    metadata: dict[str, Any] = {"content_type": "text/html"}
    if selectors:
        from parsel import Selector
        selected = Selector(text=html)
        metadata["selected"] = {name: selected.css(selector).getall() for name, selector in selectors.items()}
    return title, text, links[:2000], metadata


def _should_render(html: str) -> bool:
    lowered = html.lower()
    return len(html) < 1000 or any(token in lowered for token in ("__next_data__", "id=\"root\"", "id='root'", "enable javascript"))


def _allow_browser_request(route) -> None:
    from security_utils import UnsafeURLError, assert_public_url
    request_url = route.request.url
    if request_url.startswith(("http://", "https://")):
        try:
            assert_public_url(request_url)
        except UnsafeURLError:
            route.abort()
            return
    route.continue_()


def _playwright_html(url: str, timeout_ms: int) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", _allow_browser_request)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(750)
            return page.content()
        finally:
            browser.close()



__all__ = ["ScrapeResult", "scrape_web"]
