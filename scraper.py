#!/usr/bin/env python3
"""
Web Scraper Module
==================
Combines Camoufox (anti-detect headless Firefox) + Selenium for:
  - Cloudflare / anti-bot bypass
  - Full DOM scraping with Selenium selectors
  - Page screenshots (PNG / JPEG / full-page)
  - Extensible extraction pipeline

Usage:
    from scraper import scrape
    data = scrape("https://example.com", screenshot="out.png")
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)
from PIL import Image
import io

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
    timeout: int = 30                        # page-load timeout (s)
    selectors: Optional[Dict[str, str]] = None  # name → CSS selector
    user_agent: Optional[str] = None
    headless: bool = True
    scroll_to_bottom: bool = False
    click_cookie_buttons: bool = True
    extra_wait_for_cloudflare: bool = True
    window_size: Tuple[int, int] = (1920, 1080)


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def _make_driver(config: ScrapeConfig):
    """
    Build a Camoufox-backed Selenium driver.
    Camoufox handles stealth (TLS fingerprint, canvas noise, WebGL, etc.)
    """
    try:
        import camoufox
        from selenium.webdriver import Firefox
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service

        opts = Options()
        if config.headless:
            opts.add_argument("--headless")

        # Window size
        opts.add_argument(f"--width={config.window_size[0]}")
        opts.add_argument(f"--height={config.window_size[1]}")

        # Privacy / noise reduction
        opts.set_preference("dom.webnotifications.enabled", False)
        opts.set_preference("media.autoplay.default", 0)
        opts.set_preference("browser.cache.disk.enable", False)
        opts.set_preference("browser.cache.memory.enable", False)

        if config.user_agent:
            opts.set_preference("general.useragent.override", config.user_agent)

        # Start Camoufox context (manages Firefox binary internally)
        # camoufox 1.x API: use as a context manager for automatic cleanup
        fox = camoufox.start(headless=config.headless)
        driver = Firefox(
            executable_path=fox.executable,
            options=opts,
            service=Service(fox.executable),
        )
        driver.set_page_load_timeout(config.timeout)
        return driver, fox

    except ImportError as exc:
        raise ImportError(
            "camoufox package not installed. Run: pip install camoufox"
        ) from exc


def _dismiss_cookies(driver) -> None:
    """Try common cookie-consent button selectors."""
    selectors = [
        "button[id*='cookie' i]",
        "button[class*='cookie' i]",
        "#accept-cookies",
        ".cookie-accept",
        "[data-testid='cookie-accept']",
        "button:contains('Accept')",
        "button:contains('同意')",
    ]
    for sel in selectors:
        try:
            btn = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            btn.click()
            logger.debug("Dismissed cookie banner via: %s", sel)
            return
        except TimeoutException:
            continue


def _scroll_to_bottom(driver) -> None:
    """Incrementally scroll to trigger lazy-load content."""
    last_h = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h
    driver.execute_script("window.scrollTo(0, 0);")


def _collect_screenshot(driver, config: ScrapeConfig) -> Tuple[Optional[bytes], Optional[str]]:
    """Take a screenshot and optionally save to disk."""
    if config.screenshot_path is None:
        return driver.get_screenshot_as_png(), None

    path = Path(config.screenshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if config.screenshot_full_page:
        try:
            # Scroll capture stitch
        except Exception:
            driver.save_screenshot(str(path))
    else:
        driver.save_screenshot(str(path))

    with open(path, "rb") as f:
        blob = f.read()
    return blob, str(path)


def _extract_links(driver, base_url: str) -> List[str]:
    """Pull all href values from anchor tags, resolved to absolute URLs."""
    from urllib.parse import urljoin
    links = []
    for el in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = el.get_attribute("href")
            if href:
                links.append(urljoin(base_url, href))
        except WebDriverException:
            continue
    return links


def _extract_text(driver) -> str:
    """Get visible text of the <body> element, normalised."""
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        raw = body.text
        return re.sub(r"\n{3,}", "\n\n", raw).strip()
    except NoSuchElementException:
        return ""


def _apply_selectors(driver, selectors: Dict[str, str]) -> Dict[str, Any]:
    """Extract structured data using a dict of name → CSS selector."""
    data: Dict[str, Any] = {}
    for name, sel in selectors.items():
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            if len(elements) == 1:
                data[name] = elements[0].text.strip()
            else:
                data[name] = [e.text.strip() for e in elements]
        except WebDriverException as exc:
            logger.warning("Selector [%s] failed: %s", name, exc)
            data[name] = None
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape(config: ScrapeConfig) -> ScrapeResult:
    """
    High-level scrape entry point.
    Returns a ScrapeResult dataclass with all collected data.
    """
    driver = None
    fox = None
    try:
        driver, fox = _make_driver(config)
        logger.info("Loading: %s", config.url)
        driver.get(config.url)

        # Anti-bot grace period
        if config.extra_wait_for_cloudflare:
            time.sleep(min(config.wait_seconds + 2, 10))

        # Extra explicit wait
        time.sleep(config.wait_seconds)

        # Cookie banners
        if config.click_cookie_buttons:
            _dismiss_cookies(driver)

        # Lazy-load scroll
        if config.scroll_to_bottom:
            _scroll_to_bottom(driver)

        # Screenshot
        screenshot_blob, screenshot_path = _collect_screenshot(driver, config)

        # Page basics
        title = driver.title or ""
        html = driver.page_source
        text = _extract_text(driver)
        links = _extract_links(driver, config.url)

        # Custom selectors
        metadata: Dict[str, Any] = {}
        if config.selectors:
            metadata = _apply_selectors(driver, config.selectors)

        return ScrapeResult(
            url=driver.current_url,
            title=title,
            html=html,
            text=text,
            screenshot=screenshot_blob,
            screenshot_path=screenshot_path,
            links=links,
            metadata=metadata,
        )

    except Exception as exc:
        logger.error("Scrape failed: %s", exc)
        return ScrapeResult(
            url=config.url,
            error=str(exc),
        )

    finally:
        if driver:
            try:
                driver.quit()
            except WebDriverException:
                pass
        if fox:
            try:
                fox.stop()
            except Exception:
                pass


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

    parser = argparse.ArgumentParser(description="Web scraper (Camoufox + Selenium)")
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
        print(f"Title  : {result.title}")
        print(f"Links  : {len(result.links)} found")
        if result.screenshot_path:
            print(f"Screenshot saved: {result.screenshot_path}")
        print("\n--- Body text (first 500 chars) ---")
        print(result.text[:500])
