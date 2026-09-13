#!/usr/bin/env python3
"""
scrapling_backend.py
===================
Adapter that fuses the Scrapling library (https://github.com/D4Vinci/Scrapling)
into the master pipeline as a first-class backend.

Scrapling brings three things the toolkit previously lacked:
  1. TLS-impersonating HTTP (curl_cffi spoofs real Chrome/Firefox/Safari JA3
     fingerprints — beats raw httpx against bot detectors that check TLS).
  2. StealthyFetcher — Camoufox-backed browser fetching with Scrapling's
     battle-tested anti-detection defaults (real Firefox geometry, humanized
     mouse movement, ad-blocking, geo-matching).
  3. DynamicFetcher — Playwright/Chromium with Scrapling's stealth patches and
     an "adaptive" selector layer that auto-relocates elements when a site
     changes its DOM (great for re-scrapes after redesigns).

Modes:
    mode="http"      curl_cffi HTTP/2 with browser TLS impersonation  (fast)
    mode="stealth"   Camoufox stealth Firefox                         (heavy WAF)
    mode="dynamic"   Playwright Chromium + Scrapling stealth          (medium WAF)

Usage:
    from scrapling_backend import scrape_scrapling, ScrapeResult
    result = scrape_scrapling("https://example.com", mode="http")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScraplingResult:
    url: str
    status: int = 0
    title: str = ""
    html: str = ""
    text: str = ""
    links: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    mode: str = ""
    error: Optional[str] = None


def _response_to_result(response, mode: str, t0: float) -> ScraplingResult:
    """Map a Scrapling Response (Selector subclass) into ScraplingResult."""
    result = ScraplingResult(url=getattr(response, "url", "?"),
                             status=getattr(response, "status", 0) or 0,
                             mode=mode,
                             html=response.html_content or "",
                             elapsed_ms=(time.time() - t0) * 1000)
    try:
        result.text = response.get_all_text(ignore_elements=["script", "style"]) or ""
    except TypeError:
        result.text = response.get_all_text() or ""
    result.text = " ".join(result.text.split())

    title_sel = response.css("title::text")
    result.title = (title_sel.get() or "").strip() if title_sel is not None else ""

    hrefs = response.css("a::attr(href)")
    result.links = [h for h in (hrefs.getall() if hasattr(hrefs, "getall") else []) if h]
    return result


def _extract_selectors(response, selectors: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Run a {name: css-selector} map against a Scrapling response.

    Values become plain strings: a single match is used directly, multiple
    matches are joined with " | ". Element selects fall back to their text.
    """
    out: Dict[str, str] = {}
    if not selectors:
        return out
    for name, sel in selectors.items():
        try:
            found = response.css(sel)
            values: List[str] = []
            # Pseudo-elements (::text/::attr) yield strings; plain CSS yields
            # element wrappers whose .text/.get_all_text() hold the visible text.
            try:
                raw = found.getall()
            except Exception:
                raw = []
            if all(isinstance(v, str) and not v.lstrip().startswith("<") for v in raw) and raw:
                values = [v.strip() for v in raw if v.strip()]
            else:
                try:
                    values = [(el.text or "").strip() for el in found if el is not None]
                    values = [v for v in values if v]
                except Exception:
                    values = []
            out[name] = " | ".join(values)
        except Exception as exc:  # selector failures shouldn't kill the scrape
            logger.warning("Selector [%s] failed: %s", name, exc)
            out[name] = ""
    return out


def available() -> bool:
    try:
        import scrapling  # noqa: F401
        return True
    except ImportError:
        return False


def scrape_scrapling(
    url: str,
    mode: str = "http",
    wait: float = 0.0,
    headless: bool = True,
    proxy: Optional[str] = None,
    impersonate: str = "chrome",
    solve_cloudflare: bool = False,
    selectors: Optional[Dict[str, str]] = None,
    disable_resources: bool = True,
    os_randomize: bool = True,
) -> ScraplingResult:
    """
    Fetch a URL through Scrapling.

    proxy:      "http://host:port" / "socks5://host:port" / "http://user:pass@host:port"
    impersonate: browser TLS fingerprint to spoof in http mode
                ("chrome", "firefox", "safari", "edge", or versioned like "chrome124")
    solve_cloudflare: stealth mode only — actively solve CF challenges (needs camoufox bin)
    """
    t0 = time.time()
    if mode == "http":
        from scrapling.fetchers import Fetcher
        kwargs = {"impersonate": impersonate, "follow_redirects": True, "timeout": 30}
        if proxy:
            kwargs["proxy"] = proxy
        response = Fetcher.get(url, **kwargs)
        result = _response_to_result(response, mode, t0)

    elif mode == "stealth":
        from scrapling.fetchers import StealthyFetcher
        kwargs = {"headless": headless, "disable_resources": disable_resources,
                  "solve_cloudflare": solve_cloudflare, "os_randomize": os_randomize,
                  "google_search": True}
        if wait:
            kwargs["wait"] = wait
        if proxy:
            kwargs["proxy"] = {"server": proxy}
        response = StealthyFetcher.fetch(url, **kwargs)
        result = _response_to_result(response, mode, t0)

    elif mode == "dynamic":
        from scrapling.fetchers import DynamicFetcher
        kwargs = {"headless": headless, "disable_resources": disable_resources,
                  "google_search": True}
        if wait:
            kwargs["wait"] = wait
        if proxy:
            kwargs["proxy"] = {"server": proxy}
        response = DynamicFetcher.fetch(url, **kwargs)
        result = _response_to_result(response, mode, t0)

    else:
        return ScraplingResult(url=url, mode=mode, error=f"unknown scrapling mode: {mode}")

    result.metadata = _extract_selectors(response, selectors)
    return result


def async_scrape_scrapling(url: str, mode: str = "http", **kwargs):
    """Coroutine variant for use inside the async master pipeline."""
    import asyncio
    # Scrapling's fetchers are sync; run them in a thread so we never
    # block the pipeline's event loop (same pattern as scraper.scrape).
    return asyncio.to_thread(scrape_scrapling, url, mode=mode, **kwargs)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Scrapling backend (TLS-impersonating stealth fetch)")
    ap.add_argument("url")
    ap.add_argument("--mode", choices=["http", "stealth", "dynamic"], default="http")
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--impersonate", default="chrome")
    ap.add_argument("--solve-cloudflare", action="store_true")
    ap.add_argument("-s", "--selector", action="append", default=[],
                    help="name=css-selector (repeatable)")
    args = ap.parse_args()

    sels = dict(s.split("=", 1) for s in args.selector) if args.selector else None
    r = scrape_scrapling(args.url, mode=args.mode, proxy=args.proxy,
                         impersonate=args.impersonate,
                         solve_cloudflare=args.solve_cloudflare, selectors=sels)
    if r.error:
        print(f"[ERROR] {r.error}")
    else:
        print(f"URL    : {r.url}")
        print(f"Status : {r.status}  ({r.elapsed_ms:.0f} ms, mode={r.mode})")
        print(f"Title  : {r.title}")
        print(f"Links  : {len(r.links)}")
        for k, v in (r.metadata or {}).items():
            print(f"  [{k}] {v[:120]}")
        print("\n--- Text (first 500 chars) ---")
        print(r.text[:500])
