#!/usr/bin/env python3
"""
master_pipeline.py
================
End-to-end scraping pipeline with WAF-aware auto-configuration.

Pipeline:
  1. WAF fingerprinting → auto-select backend
  2. Proxy rotation (optional)
  3. Fingerprint generator → consistent per-session noise
  4. Stealth scrape (Camoufox / Playwright / httpx)
  5. CAPTCHA solving (optional)
  6. Traffic capture → HAR (optional)
  7. Content extraction → clean text
  8. Export → JSON + CSV + Markdown + HTML + SQLite

Auto-selection logic:
  - Cloudflare / heavy WAF → Camoufox + Selenium
  - Akamai / Imperva / Sucuri / AWS WAF / Azure → Playwright + stealth patches
  - Bot protection / unknown → Playwright + stealth + fingerprint noise
  - No WAF / simple → httpx + parsel (fast, no browser)
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
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
# Backend selection logic
# ---------------------------------------------------------------------------

def decide_backend(waf_report: dict) -> dict:
    """
    Choose scrape backend and profile from WAF fingerprint report.

    Returns:
        {
            "backend": "camoufox" | "playwright" | "httpx",
            "profile": "cloudflare" | "bot_detected" | "stealth_max" | "crawler",
            "reason": "...",
            "waf": waf_report["waf"],
        }
    """
    waf = waf_report.get("waf", {})
    name = (waf.get("name") or "").lower()
    confidence = (waf.get("confidence") or "").lower()

    heavy = {"cloudflare", "akamai", "imperva", "sucuri", "aws waf", "azure / microsoft"}
    medium = {"fastly", "f5 big-ip / asm", "barracuda", "fortinet fortiweb", "ddos-guard"}

    status = waf_report.get("status_code", 0)

    # Non-200 on first touch = hostile target. Never trust the fast backend.
    if status == 0 or status >= 400:
        return {
            "backend": "camoufox",
            "profile": "cloudflare",
            "reason": f"Target returned HTTP {status or 'no response'} — escalating to stealth browser",
            "waf": waf,
        }

    if name == "unknown":
        return {
            "backend": "httpx",
            "profile": "crawler",
            "reason": "No WAF detected — using fast HTTP backend",
            "waf": waf,
        }

    if name in heavy or confidence in {"high", "certain"}:
        return {
            "backend": "camoufox",
            "profile": "cloudflare",
            "reason": f"Heavy WAF ({name}) detected — using Camoufox stealth Firefox",
            "waf": waf,
        }

    if name in medium:
        return {
            "backend": "playwright",
            "profile": "stealth_max",
            "reason": f"Medium WAF ({name}) detected — using Playwright + max stealth",
            "waf": waf,
        }

    # Generic bot protection / captchas / unknown
    return {
        "backend": "playwright",
        "profile": "bot_detected",
        "reason": f"Bot protection ({name}) detected — using Playwright + stealth",
        "waf": waf,
    }


def build_fingerprint(backend: str, profile_name: str):
    """
    Build a FingerprintProfile for the chosen backend.
    Returns (profile, init_script_or_None).
    """
    try:
        from examples.fingerprint_generator.fingerprint_generator import (
            FingerprintProfile, apply_to_playwright, apply_to_selenium,
            Platform, BrowserType,
        )
        platform_map = {
            "camoufox": Platform.WINDOWS,
            "playwright": Platform.WINDOWS,
            "httpx": Platform.LINUX,
        }
        browser_map = {
            "camoufox": BrowserType.FIREFOX,
            "playwright": BrowserType.CHROME,
            "httpx": BrowserType.CHROME,
        }
        fp = FingerprintProfile(
            platform=platform_map.get(backend, Platform.WINDOWS),
            browser=browser_map.get(backend, BrowserType.CHROME),
            locale="en-US",
        )
        if backend == "playwright":
            ctx = apply_to_playwright(fp)
            return fp, ctx["init_script"]
        if backend == "camoufox":
            return fp, None  # Camoufox handles its own fingerprint
        return fp, None
    except Exception as exc:
        log.warning("Fingerprint generation failed: %s", exc)
        return None, None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    url: str,
    use_proxy: bool = False,
    proxy_file: str = "config/proxies.txt",
    stealth_profile: str = "auto",
    capture_har: bool = False,
    har_backend: str = "playwright",
    solve_captcha: bool = False,
    captcha_service: str = None,
    captcha_api_key: str = None,
    screenshot: bool = True,
    full_page: bool = True,
    wait_seconds: float = None,
    extract_content: bool = True,
    export: bool = True,
    auto_backend: bool = True,
):
    """
    Run the full pipeline on a URL.

    If auto_backend=True and stealth_profile="auto", runs WAF detection first
    and chooses backend automatically.
    """
    results = {
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "steps": {},
        "waf": None,
        "backend": None,
    }
    html = None
    backend = None
    profile_name = stealth_profile
    wait = wait_seconds
    fingerprint, init_script = None, None

    # ── Step 0: WAF fingerprinting + auto backend selection ───────────────
    if auto_backend and stealth_profile == "auto":
        log.info("=== Step 0: WAF fingerprinting ===")
        try:
            from examples.waf_fingerprint.waf_fingerprint import fingerprint_target
            waf_report = fingerprint_target(url)
            results["waf"] = {
                "name": waf_report.get("waf", {}).get("name"),
                "confidence": waf_report.get("waf", {}).get("confidence"),
                "signals": waf_report.get("waf", {}).get("signals", []),
            }
            log.info("  WAF   : %s (%s)", results["waf"]["name"], results["waf"]["confidence"])
            log.info("  TLS   : %s", waf_report.get("tls", {}).get("waf_hint", "n/a"))
            log.info("  Status: %s  %.0fms", waf_report.get("status_code"), waf_report.get("response_time_ms"))

            decision = decide_backend(waf_report)
            backend = decision["backend"]
            profile_name = decision["profile"]
            results["backend"] = decision
            log.info("  Backend: %s (%s)", backend, decision["reason"])

            # Build fingerprint for this backend
            fingerprint, init_script = build_fingerprint(backend, profile_name)
            if fingerprint:
                log.info("  Fingerprint: %s/%s", fingerprint.platform.value, fingerprint.browser.value)

            # Wait from profile if not explicitly set
            if wait is None:
                from examples.stealth.stealth_profiles import get_profile
                wait = get_profile(profile_name).extra_wait_seconds

        except Exception as exc:
            log.warning("  WAF detection failed: %s — falling back to Playwright", exc)
            backend = "playwright"
            profile_name = "bot_detected"
            results["backend"] = {"backend": "playwright", "profile": "bot_detected",
                                  "reason": f"WAF detection failed: {exc}"}
            fingerprint, init_script = build_fingerprint("playwright", "bot_detected")
            if wait is None:
                wait = 3.0
    else:
        # Manual profile selection
        backend = "camoufox" if stealth_profile == "cloudflare" else "playwright"
        fingerprint, init_script = build_fingerprint(backend, profile_name)
        if wait is None:
            wait = 5.0 if profile_name == "cloudflare" else 3.0

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
                log.warning("  No healthy proxies available")
        except Exception as exc:
            log.warning("  Proxy setup failed: %s", exc)

    # ── Step 2: Scrape with selected backend ──────────────────────────────
    log.info("=== Step 2: Scrape (backend=%s, profile=%s) ===", backend, profile_name)
    try:
        if backend == "camoufox":
            html = await _scrape_camoufox(url, results, screenshot=screenshot,
                                          full_page=full_page, wait=wait, proxy=proxy)
        elif backend == "playwright":
            html = await _scrape_playwright(url, results, profile_name=profile_name,
                                            fingerprint= fingerprint, init_script=init_script,
                                            screenshot=screenshot, full_page=full_page,
                                            wait=wait, proxy=proxy)
        else:  # httpx
            html = await _scrape_httpx(url, results, wait=wait)
    except Exception as exc:
        log.warning("  Scrape failed: %s", exc)
        results["steps"]["scrape"] = {"backend": backend, "error": str(exc)}
        html = None

    # ── Step 3: CAPTCHA solving ───────────────────────────────────────────
    if solve_captcha and html:
        log.info("=== Step 3: CAPTCHA detection + solving ===")
        try:
            from examples.captcha_solver.captcha_solver import CaptchaSolver
            solver = CaptchaSolver(
                service=captcha_service or os.getenv("CAPTCHA_SERVICE", "2captcha"),
                api_key=captcha_api_key or os.getenv("CAPTCHA_API_KEY", ""),
            )
            log.info("  Solver ready: %s", solver._solver.__class__.__name__)
            log.info("  (Live solving requires a live browser session with CAPTCHA present)")
            results["steps"]["captcha"] = {"solver": "ready", "service": solver._solver.__class__.__name__}
        except Exception as exc:
            log.warning("  CAPTCHA setup failed: %s", exc)

    # ── Step 4: Traffic capture ───────────────────────────────────────────
    if capture_har:
        log.info("=== Step 4: Traffic capture (HAR) ===")
        har_path = str(BASE / "traffic.har")
        try:
            # run_pipeline is already async — just await the coroutine directly
            entries = await _capture_har(url, har_path, backend=backend, wait=wait)
            log.info("  Captured %d entries → %s", len(entries), har_path)
            from examples.traffic_sniffer.traffic_sniffer import har_to_endpoints, analyze_har
            eps = har_to_endpoints(har_path)
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
    extracted_text = ""
    if extract_content and html:
        log.info("=== Step 5: Content extraction (Trafilatura) ===")
        try:
            from examples.content_extract.content_extract import extract_trafilatura
            article = extract_trafilatura(url, html=html)
            if article.error:
                log.warning("  Extraction: %s", article.error)
            else:
                extracted_text = article.text
                log.info("  Title  : %s", article.title)
                log.info("  Author : %s", article.author)
                log.info("  Text   : %d chars", len(article.text))
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
    scrape_ok = not results["steps"].get("scrape", {}).get("error")
    if export and scrape_ok:
        log.info("=== Step 6: Export (all formats) ===")
        try:
            from examples.output.output_formats import ScrapeRecord, export_all
            record = ScrapeRecord(
                url=url,
                title=results["steps"].get("scrape", {}).get("title", ""),
                status_code=200,
                content_type="text/html",
                text=extracted_text,
                links=[],
                timestamp=datetime.now(timezone.utc).isoformat(),
                metadata=results,
            )
            outputs = export_all([record], base_name=str(BASE / "pipeline"))
            for fmt, path in outputs.items():
                log.info("  %-10s: %s", fmt, path)
            results["steps"]["output"] = outputs
        except Exception as exc:
            log.warning("  Export failed: %s", exc)
    elif export and not scrape_ok:
        log.warning("=== Step 6: Skipped export — scrape failed, nothing to export ===")

    # ── Proxy stats ───────────────────────────────────────────────────────
    if proxy_mgr:
        results["proxy_stats"] = proxy_mgr.stats()
        log.info("  Proxy pool: %(healthy)s/%(total)s healthy, %(success_rate)s success",
                 proxy_mgr.stats())

    log.info("\nPipeline complete. Results in output/")
    return results


# ---------------------------------------------------------------------------
# Backend-specific scrapers
# ---------------------------------------------------------------------------

async def _scrape_camoufox(url: str, results: dict, screenshot: bool = True,
                            full_page: bool = True, wait: float = 5.0, proxy=None):
    from scraper import scrape, ScrapeConfig
    from examples.stealth.stealth_profiles import get_profile
    profile = get_profile("cloudflare")
    config = ScrapeConfig(
        url=url,
        screenshot_path=str(BASE / "screenshot.png") if screenshot else None,
        screenshot_full_page=full_page,
        wait_seconds=wait or profile.extra_wait_seconds,
        headless=profile.headless,
        scroll_to_bottom=profile.scroll_to_bottom,
        window_size=profile.window_size,
        user_agent=profile.user_agent or None,
    )
    result = scrape(config)
    if result.error:
        results["steps"]["scrape"] = {"backend": "camoufox", "error": result.error}
        log.error("  Camoufox scrape failed: %s", result.error)
        return None
    results["steps"]["scrape"] = {
        "backend": "camoufox",
        "title": result.title,
        "links_count": len(result.links),
        "text_chars": len(result.text),
        "screenshot": result.screenshot_path,
    }
    log.info("  Title  : %s", result.title)
    return result.html


async def _scrape_playwright(url: str, results: dict, profile_name: str = "bot_detected",
                              fingerprint=None, init_script: str = None,
                              screenshot: bool = True, full_page: bool = True,
                              wait: float = 3.0, proxy=None):
    from examples.stealth.stealth_profiles import get_profile
    from playwright.async_api import async_playwright
    profile = get_profile(profile_name)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=profile.headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx_kwargs = {
            "viewport": {"width": profile.window_size[0], "height": profile.window_size[1]},
            "user_agent": profile.user_agent,
            "locale": profile.locale,
            "timezone_id": profile.timezone,
        }
        if proxy:
            proxy_cfg = {"server": f"http://{proxy.host}:{proxy.port}"}
            if proxy.username:
                proxy_cfg["username"] = proxy.username
                proxy_cfg["password"] = proxy.password
            ctx_kwargs["proxy"] = proxy_cfg

        context = await browser.new_context(**ctx_kwargs)

        # Merge fingerprint init script
        scripts = []
        if init_script:
            scripts.append(init_script)
        if profile.playwright_init_script:
            scripts.append(profile.playwright_init_script)
        if scripts:
            await context.add_init_script("\n".join(scripts))

        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout((wait or profile.extra_wait_seconds) * 1000)

        if profile.click_cookies:
            try:
                await page.click('button:has-text("Accept")', timeout=2000)
            except Exception:
                pass

        if profile.scroll_to_bottom:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

        title = await page.title()
        html = await page.content()
        text = await page.evaluate("document.body.innerText")
        links = await page.evaluate("Array.from(document.querySelectorAll('a[href]')).map(a => a.href)")

        if screenshot:
            shot_path = BASE / "screenshot.png"
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(shot_path), full_page=full_page)
            results["steps"]["scrape"] = {
                "backend": "playwright",
                "title": title,
                "links_count": len(links),
                "text_chars": len(text),
                "screenshot": str(shot_path),
            }
        else:
            results["steps"]["scrape"] = {
                "backend": "playwright",
                "title": title,
                "links_count": len(links),
                "text_chars": len(text),
            }

        log.info("  Title  : %s", title)
        await browser.close()
        return html


async def _scrape_httpx(url: str, results: dict, wait: float = 2.0):
    import httpx
    from parsel import Selector
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; ScrapyKit/1.0; +https://example.com/bot)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, http2=True,
                                 headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        await asyncio.sleep(wait)

    sel = Selector(text=resp.text)
    title = sel.css("title::text").get("").strip()
    text = " ".join(sel.css("body ::text").getall())
    text = " ".join(text.split())[:5000]
    links = sel.css("a[href]::attr(href)").getall()

    results["steps"]["scrape"] = {
        "backend": "httpx",
        "title": title,
        "links_count": len(links),
        "text_chars": len(text),
    }
    log.info("  Title  : %s", title)
    return resp.text


async def _capture_har(url: str, output: str, backend: str = "playwright",
                        wait: float = 5.0) -> list:
    if backend == "playwright":
        from examples.traffic_sniffer.traffic_sniffer import capture_with_playwright
        return await capture_with_playwright(url, output=output, wait=wait)
    else:
        from examples.traffic_sniffer.traffic_sniffer import MitmproxyHarvester
        hv = MitmproxyHarvester(output=output)
        hv.start()
        try:
            import httpx
            async with httpx.AsyncClient(proxies={"all://": hv.get_proxy_url()}, verify=False, timeout=30) as client:
                await client.get(url)
                await asyncio.sleep(wait)
        finally:
            hv.stop()
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Master scraping pipeline")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("--use-proxy", action="store_true", help="Enable proxy rotation")
    parser.add_argument("--proxy-file", default="config/proxies.txt")
    parser.add_argument("--profile", default="auto",
                        help="Stealth profile or 'auto' for WAF-based selection")
    parser.add_argument("--no-auto", action="store_true", help="Disable auto backend selection")
    parser.add_argument("--capture-har", action="store_true")
    parser.add_argument("--har-backend", choices=["playwright", "mitmproxy"], default="playwright")
    parser.add_argument("--solve-captcha", action="store_true")
    parser.add_argument("--captcha-service", default=os.getenv("CAPTCHA_SERVICE", "2captcha"))
    parser.add_argument("--captcha-api-key", default=os.getenv("CAPTCHA_API_KEY", ""))
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--no-full-page", action="store_true")
    parser.add_argument("--wait", type=float, default=None)
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
        auto_backend=args.profile == "auto" and not args.no_auto,
    ))

    # Summary
    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    if results.get("waf"):
        w = results["waf"]
        print(f"\n[WAF] {w.get('name')} ({w.get('confidence')})")
        for s in w.get("signals", [])[:5]:
            print(f"      signal: {s}")
    if results.get("backend"):
        b = results["backend"]
        print(f"\n[Backend] {b.get('backend')} — {b.get('reason')}")
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
