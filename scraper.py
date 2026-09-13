#!/usr/bin/env python3
"""
scraper.py
=========
Camoufox (anti-detect Firefox) + Playwright scraper.

Downloads the full page HTML, extracts text/links/metadata,
and saves screenshots (viewport or full-page).

Why Playwright instead of Selenium here:
  - Camoufox sync API exposes a Playwright browser object
  - Playwright has better async, selectors, and screenshot support
  - No Selenium/geckodriver bridge needed

Usage:
    from scraper import scrape, screenshot_page, scrape_text

    result = scrape(ScrapeConfig(
        url="https://example.com",
        screenshot_path="shot.png",
        screenshot_full_page=True,
        wait_seconds=5.0,
    ))
    print(result.title, result.text[:200], result.screenshot_path)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ScrapeResult:
    url: str
    title: str = ""
    html: str = ""
    text: str = ""
    screenshot: Optional[bytes] = None
    screenshot_path: Optional[str] = None
    links: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class ScrapeConfig:
    url: str
    screenshot_path: Optional[str] = None
    screenshot_full_page: bool = True
    screenshot_format: str = "png"          # png | jpeg | webp
    wait_seconds: float = 3.0               # extra sleep after load
    timeout: int = 45                        # page-load timeout (s)
    selectors: Optional[Dict[str, str]] = None  # name → CSS selector
    user_agent: Optional[str] = None
    headless: bool = True
    scroll_to_bottom: bool = False
    click_cookie_buttons: bool = True
    extra_wait_for_cloudflare: bool = True
    window_size: tuple = (1920, 1080)
    proxy: Optional[str] = None             # http://host:port or socks5://...
    cookies: Optional[List[Dict[str, Any]]] = None  # Playwright cookie dicts (cookies.py)


# ---------------------------------------------------------------------------
# Core scraper — Playwright + Camoufox
# ---------------------------------------------------------------------------

async def _scrape_async(config: ScrapeConfig) -> ScrapeResult:
    """Async implementation using Playwright + Camoufox."""
    try:
        from camoufox import AsyncCamoufox
    except ImportError as exc:
        return ScrapeResult(url=config.url, error=f"camoufox not installed: {exc}")

    result = ScrapeResult(url=config.url)

    try:
        # Camoufox manages its own Playwright instance internally.
        # Do NOT wrap it in async_playwright().
        async with AsyncCamoufox(
            headless=config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        ) as browser:
            context = await browser.new_context(
                viewport={"width": config.window_size[0], "height": config.window_size[1]},
                user_agent=config.user_agent or (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
            )

            # Anti-fingerprinting init script
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            """)

            # Auth cookies (from cookies.py / local browser stores)
            if config.cookies:
                try:
                    await context.add_cookies(config.cookies)
                    logger.info("Injected %d cookie(s) into context", len(config.cookies))
                except Exception as exc:
                    logger.warning("Cookie injection failed: %s", exc)

            page = await context.new_page()
            page.set_default_timeout(config.timeout * 1000)

            # Navigate
            await page.goto(config.url, wait_until="domcontentloaded", timeout=config.timeout * 1000)

            # Anti-bot grace period
            if config.extra_wait_for_cloudflare:
                await page.wait_for_timeout(min(int((config.wait_seconds + 2) * 1000), 10000))

            # Extra explicit wait
            await page.wait_for_timeout(int(config.wait_seconds * 1000))

            # Cookie banners
            if config.click_cookie_buttons:
                try:
                    await page.click('button:has-text("Accept")', timeout=2000)
                except Exception:
                    pass

            # Lazy-load scroll
            if config.scroll_to_bottom:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)

            # Collect data
            result.url = page.url
            result.title = await page.title()
            result.html = await page.content()
            result.text = await page.evaluate("document.body.innerText")
            result.links = await page.evaluate(
                "Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
            )
            result.text = re.sub(r"\n{3,}", "\n\n", result.text).strip()

            # Custom selectors
            if config.selectors:
                result.metadata = {}
                for name, sel in config.selectors.items():
                    try:
                        elements = await page.query_selector_all(sel)
                        if len(elements) == 1:
                            result.metadata[name] = (await elements[0].text_content()) or ""
                        else:
                            result.metadata[name] = [
                                ((await e.text_content()) or "").strip() for e in elements
                            ]
                    except Exception as exc:
                        logger.warning("Selector [%s] failed: %s", name, exc)
                        result.metadata[name] = None

            # Screenshot
            if config.screenshot_path:
                shot_path = Path(config.screenshot_path)
                shot_path.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(
                    path=str(shot_path),
                    full_page=config.screenshot_full_page,
                )
                result.screenshot_path = str(shot_path)
                result.screenshot = shot_path.read_bytes()

    except Exception as exc:
        logger.error("Scrape failed: %s", exc)
        result.error = str(exc)

    return result


def scrape(config: ScrapeConfig) -> ScrapeResult:
    """
    High-level scrape entry point (sync wrapper around async impl).
    Safe to call from sync code OR from inside a running event loop
    (spawns a worker thread with its own loop in that case).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running — normal path
        return asyncio.run(_scrape_async(config))

    # Already inside a running loop (e.g. called from run_pipeline or the
    # distributed crawler). asyncio.run() would raise RuntimeError, so run
    # the coroutine in a dedicated thread with its own event loop.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, _scrape_async(config))
        return future.result()


def screenshot_page(
    url: str,
    output_path: str,
    full_page: bool = True,
    headless: bool = True,
    wait: float = 3.0,
) -> Optional[str]:
    """
    Quick helper: just take a screenshot of a page.
    Returns the output path on success, None on failure.
    """
    config = ScrapeConfig(
        url=url,
        screenshot_path=output_path,
        screenshot_full_page=full_page,
        headless=headless,
        wait_seconds=wait,
    )
    result = scrape(config)
    if result.error:
        logger.error("Screenshot failed: %s", result.error)
        return None
    return result.screenshot_path


def scrape_text(url: str, wait: float = 3.0) -> str:
    """Minimal helper: return visible text of a page."""
    result = scrape(ScrapeConfig(url=url, wait_seconds=wait, screenshot_path=None))
    return result.text if result.text else ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Web scraper (Camoufox + Playwright)")
    parser.add_argument("url", help="Target URL to scrape")
    parser.add_argument("-o", "--output", help="Screenshot output path")
    parser.add_argument("-f", "--full-page", action="store_true", default=True)
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("-w", "--wait", type=float, default=3.0)
    args = parser.parse_args()

    cfg = ScrapeConfig(
        url=args.url,
        screenshot_path=args.output,
        screenshot_full_page=args.full_page,
        headless=not args.no_headless,
        wait_seconds=args.wait,
    )
    result = scrape(cfg)

    if result.error:
        print(f"[ERROR] {result.error}")
    else:
        print(f"URL    : {result.url}")
        print(f"Title  : {result.title}")
        print(f"Links  : {len(result.links)} found")
        if result.screenshot_path:
            print(f"Screenshot saved: {result.screenshot_path}")
        print("\n--- Body text (first 500 chars) ---")
        print(result.text[:500])
