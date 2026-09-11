#!/usr/bin/env python3
"""
quickstart.py — One-file demo of the full scraper toolkit.
Shows: screenshot, scrape, content extraction, output export.

Usage:
    python quickstart.py https://example.com
    python quickstart.py https://news.ycombinator.com --full
"""

import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("quickstart")

# Ensure output dirs
OUT_DIR = Path("output")
OUT_DIR.mkdir(exist_ok=True)


def demo_screenshot(url: str):
    """Demo: Playwright screenshot."""
    log.info("=== Screenshot (Playwright) ===")
    try:
        from examples.screenshot.playwright_screenshot import screenshot
        path = OUT_DIR / "screenshot.png"
        asyncio.run(screenshot(url, str(path), full_page=True, wait=3))
        log.info("Screenshot: %s", path)
    except Exception as exc:
        log.warning("Screenshot demo failed: %s", exc)


def demo_scrape(url: str):
    """Demo: Camoufox + Selenium scrape."""
    log.info("=== Scrape (Camoufox + Selenium) ===")
    try:
        from scraper import scrape, ScrapeConfig
        result = scrape(ScrapeConfig(
            url=url,
            screenshot_path=str(OUT_DIR / "scrape_screenshot.png"),
            wait_seconds=5,
            headless=True,
            scroll_to_bottom=False,
        ))
        if result.error:
            log.error("Scrape error: %s", result.error)
            return None
        log.info("Title  : %s", result.title)
        log.info("Links  : %d found", len(result.links))
        log.info("Text   : %d chars", len(result.text))
        return result
    except Exception as exc:
        log.warning("Scrape demo failed: %s", exc)
        return None


def demo_extract(url: str, html: str = None):
    """Demo: Trafilatura content extraction."""
    log.info("=== Content extraction (Trafilatura) ===")
    try:
        from examples.content_extract.content_extract import extract_trafilatura
        article = extract_trafilatura(url, html=html)
        if article.error:
            log.error("Extract error: %s", article.error)
            return None
        log.info("Title  : %s", article.title)
        log.info("Author : %s", article.author)
        log.info("Text   : %d chars", len(article.text))
        log.info("Links  : %d found", len(article.links))
        return article
    except Exception as exc:
        log.warning("Extract demo failed: %s", exc)
        return None


def demo_output(url: str):
    """Demo: export results to all formats."""
    log.info("=== Output (all formats) ===")
    try:
        from examples.output.output_formats import ScrapeRecord, export_all
        from datetime import datetime

        record = ScrapeRecord(
            url=url,
            title="Quickstart Demo",
            status_code=200,
            content_type="text/html",
            text="Demo scrape output from quickstart.py",
            links=["https://example.com"],
            timestamp=datetime.utcnow().isoformat(),
        )
        outputs = export_all([record], base_name=str(OUT_DIR / "quickstart"))
        for fmt, path in outputs.items():
            log.info("  %-10s: %s", fmt, path)
    except Exception as exc:
        log.warning("Output demo failed: %s", exc)


def demo_crawl(url: str):
    """Demo: BFS crawler."""
    log.info("=== Crawler (BFS, depth=1) ===")
    try:
        from examples.crawlers.crawlers import bfs_crawl
        results = bfs_crawl(url, max_depth=1, max_pages=10, delay=0.5)
        log.info("Crawled %d pages", len(results))
        for u, data in list(results.items())[:5]:
            log.info("  %s — %s", data.get("status", "?"), u[:80])
    except Exception as exc:
        log.warning("Crawl demo failed: %s", exc)


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    full = "--full" in sys.argv

    log.info("Target: %s", url)

    # Always run
    demo_screenshot(url)
    scrape_result = demo_scrape(url)
    html = scrape_result.html if scrape_result else None
    article = demo_extract(url, html=html)
    demo_output(url)

    # Full mode: also crawl
    if full:
        demo_crawl(url)

    log.info("\nDone. Check output/ for results.")


if __name__ == "__main__":
    main()
