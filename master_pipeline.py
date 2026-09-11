#!/usr/bin/env python3
"""
master_pipeline.py
================
End-to-end scraping pipeline combining all modules:

  1. Proxy rotation — automatic IP rotation with health checks
  2. Stealth profile — Camoufox/Playwright/UC with anti-detection
  3. CAPTCHA solving — auto-detect and solve image/reCAPTCHA/hCaptcha/Turnstile
  4. Traffic capture — HAR export of all network requests
  5. Content extraction — Trafilatura/Readability clean article text
  6. Output — JSON + CSV + Markdown + HTML + SQLite

Usage:
    python master_pipeline.py https://example.com
    python master_pipeline.py https://example.com --use-proxy --capture-har --solve-captcha
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("master_pipeline")

BASE = Path("output")
BASE.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    url: str,
    use_proxy: bool = False,
    proxy_file: str = "config/proxies.txt",
    stealth_profile: str = "cloudflare",
    capture_har: bool = False,
    har_backend: str = "playwright",
    solve_captcha: bool = False,
    captcha_service: str = None,
    captcha_api_key: str = None,
    screenshot: bool = True,
    full_page: bool = True,
    wait_seconds: float = 5.0,
    extract_content: bool = True,
    export: bool = True,
):
    """
    Run the full pipeline on a URL.
    Returns a dict of results and output paths.
    """
    results = {"url": url, "timestamp": datetime.utcnow().isoformat(), "steps": {}}
    html = None

    # ── Step 1: Proxy setup ───────────────────────────────────────────────
    proxy = None
    proxy_mgr = None
    if use_proxy:
        log.info("=== Step 1: Proxy rotation ===")
        try:
            from examples.proxy_rotation.proxy_rotation import ProxyManager
            proxy_mgr = ProxyManager(proxy_file=proxy_file, strategy="best")
            proxy = proxy_mgr.get(urlparse(url).netloc)
            if proxy:
                log.info("  Proxy: %s  (latency %.0fms)", proxy.url, proxy.latency_ms)
                results["steps"]["proxy"] = {"proxy": proxy.url, "latency_ms": proxy.latency_ms}
            else:
                log.warning("  No healthy proxies available, continuing without proxy")
        except Exception as exc:
            log.warning("  Proxy setup failed: %s", exc)

    # ── Step 2: Screenshot + scrape with stealth ──────────────────────────
    log.info("=== Step 2: Screenshot + scrape (profile=%s) ===")
    try:
        from scraper import scrape, ScrapeConfig
        from examples.stealth.stealth_profiles import get_profile

        profile = get_profile(stealth_profile)
        config = ScrapeConfig(
            url=url,
            screenshot_path=str(BASE / "screenshot.png") if screenshot else None,
            screenshot_full_page=full_page,
            wait_seconds=wait_seconds or profile.extra_wait_seconds,
            headless=profile.headless,
            scroll_to_bottom=profile.scroll_to_bottom,
            window_size=profile.window_size,
            user_agent=profile.user_agent or None,
        )
        scrape_result = scrape(config)

        if scrape_result.error:
            log.error("  Scrape failed: %s", scrape_result.error)
            results["steps"]["scrape"] = {"error": scrape_result.error}
        else:
            log.info("  Title  : %s", scrape_result.title)
            log.info("  Links  : %d", len(scrape_result.links))
            log.info("  Text   : %d chars", len(scrape_result.text))
            html = scrape_result.html
            results["steps"]["scrape"] = {
                "title": scrape_result.title,
                "links_count": len(scrape_result.links),
                "text_chars": len(scrape_result.text),
                "screenshot": scrape_result.screenshot_path,
            }
    except Exception as exc:
        log.warning("  Scrape step failed: %s", exc)

    # ── Step 3: CAPTCHA solving ───────────────────────────────────────────
    if solve_captcha and html:
        log.info("=== Step 3: CAPTCHA detection + solving ===")
        try:
            from examples.captcha_solver.captcha_solver import CaptchaSolver
            solver = CaptchaSolver(
                service=captcha_service or os.getenv("CAPTCHA_SERVICE", "2captcha"),
                api_key=captcha_api_key or os.getenv("CAPTCHA_API_KEY", ""),
            )
            log.info("  CAPTCHA solver initialized: %s", solver._solver.__class__.__name__)
            log.info("  (Auto-solving requires a live browser session with CAPTCHA present)")
            results["steps"]["captcha"] = {"solver": "ready", "note": "integrate via selenium_solve_recaptcha()"}
        except Exception as exc:
            log.warning("  CAPTCHA solver setup failed: %s", exc)

    # ── Step 4: Traffic capture ───────────────────────────────────────────
    if capture_har:
        log.info("=== Step 4: Traffic capture (HAR) ===")
        har_path = str(BASE / "traffic.har")
        try:
            entries = asyncio.run(_capture_har(url, har_path, backend=har_backend))
            log.info("  Captured %d entries → %s", len(entries), har_path)

            # Auto-extract endpoints
            from examples.traffic_sniffer.traffic_sniffer import har_to_endpoints, analyze_har
            eps = har_to_endpoints(har_path)
            total_eps = sum(len(v) for v in eps.values())
            log.info("  Extracted %d endpoints", total_eps)
            report = analyze_har(har_path)
            results["steps"]["traffic"] = {
                "har_path": har_path,
                "entries": len(entries),
                "endpoints": {k: len(v) for k, v in eps.items()},
                "total_bytes": report.get("total_bytes", 0),
            }
        except Exception as exc:
            log.warning("  Traffic capture failed: %s", exc)

    # ── Step 5: Content extraction ────────────────────────────────────────
    if extract_content and html:
        log.info("=== Step 5: Content extraction (Trafilatura) ===")
        try:
            from examples.content_extract.content_extract import extract_trafilatura
            article = extract_trafilatura(url, html=html)
            if article.error:
                log.warning("  Extraction: %s", article.error)
            else:
                log.info("  Title  : %s", article.title)
                log.info("  Author : %s", article.author)
                log.info("  Text   : %d chars", len(article.text))
                log.info("  Links  : %d", len(article.links))
                results["steps"]["extraction"] = {
                    "extractor": "trafilatura",
                    "title": article.title,
                    "author": article.author,
                    "text_chars": len(article.text),
                    "links_count": len(article.links),
                }
                html = article.html
        except Exception as exc:
            log.warning("  Extraction failed: %s", exc)

    # ── Step 6: Export ────────────────────────────────────────────────────
    if export:
        log.info("=== Step 6: Export (all formats) ===")
        try:
            from examples.output.output_formats import ScrapeRecord, export_all
            record = ScrapeRecord(
                url=url,
                title=results["steps"].get("scrape", {}).get("title", ""),
                status_code=200,
                content_type="text/html",
                text=results["steps"].get("extraction", {}).get("text_chars", 0),
                links=[],
                timestamp=datetime.utcnow().isoformat(),
                metadata=results,
            )
            outputs = export_all([record], base_name=str(BASE / "pipeline"))
            for fmt, path in outputs.items():
                log.info("  %-10s: %s", fmt, path)
            results["steps"]["output"] = outputs
        except Exception as exc:
            log.warning("  Export failed: %s", exc)

    # ── Proxy stats ───────────────────────────────────────────────────────
    if proxy_mgr:
        stats = proxy_mgr.stats()
        results["proxy_stats"] = stats
        log.info("  Proxy pool: %(healthy)s/%(total)s healthy, %(success_rate)s success",
                 stats)

    log.info("\nPipeline complete. Results in output/")
    return results


async def _capture_har(url: str, output: str, backend: str = "playwright",
                        wait: float = 5.0) -> list:
    """Internal: capture HAR."""
    if backend == "playwright":
        from examples.traffic_sniffer.traffic_sniffer import capture_with_playwright
        return await capture_with_playwright(url, output=output, wait=wait)
    else:
        from examples.traffic_sniffer.traffic_sniffer import capture_har
        return await capture_har(url, output=output, backend="mitmproxy", wait=wait)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Master scraping pipeline")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("--use-proxy", action="store_true", help="Enable proxy rotation")
    parser.add_argument("--proxy-file", default="config/proxies.txt")
    parser.add_argument("--profile", default="cloudflare")
    parser.add_argument("--capture-har", action="store_true", help="Capture network traffic as HAR")
    parser.add_argument("--har-backend", choices=["playwright", "mitmproxy"], default="playwright")
    parser.add_argument("--solve-captcha", action="store_true", help="Enable CAPTCHA solver")
    parser.add_argument("--captcha-service", default=os.getenv("CAPTCHA_SERVICE", "2captcha"))
    parser.add_argument("--captcha-api-key", default=os.getenv("CAPTCHA_API_KEY", ""))
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--no-full-page", action="store_true")
    parser.add_argument("--wait", type=float, default=5.0)
    parser.add_argument("--no-extract", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()

    results = asyncio.run(run_pipeline(
        url=args.url,
        use_proxy=args.use_proxy,
        proxy_file=args.proxy_file,
        stealth_profile=args.profile,
        capture_har=args.capture_har,
        har_backend=args.har_backend,
        solve_captcha=args.solve_captcha,
        captcha_service=args.captcha_service,
        captcha_api_key=args.captcha_api_key,
        screenshot=not args.no_screenshot,
        full_page=not args.no_full_page,
        wait_seconds=args.wait,
        extract_content=not args.no_extract,
        export=not args.no_export,
    ))

    # Print summary
    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    for step, data in results.get("steps", {}).items():
        print(f"\n[{step}]")
        for k, v in data.items():
            print(f"  {k}: {v}")
    if "proxy_stats" in results:
        print(f"\n[proxy_pool]")
        for k, v in results["proxy_stats"].items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
