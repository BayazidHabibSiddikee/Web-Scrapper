"""
anti_detection.py
===============
Anti-detection / stealth browsing patterns.

Options demonstrated:
  1. Camoufox + Selenium  (best for Cloudflare, full DOM access)
  2. undetected-chromedriver (Chrome with patched chromedriver)
  3. selenium-stealth (patch Selenium fingerprints)
  4. Playwright stealth (browser context masking)

Install:
    pip install undetected-chromedriver selenium-stealth
    # Camoufox: pip install camoufox
    # Playwright: pip install playwright && playwright install chromium
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Camoufox + Selenium (recommended for Cloudflare)
# ---------------------------------------------------------------------------

def scrape_with_camoufox(url: str, wait: float = 5.0) -> dict:
    """
    Camoufox handles TLS fingerprint, canvas noise, WebGL, and
    other anti-bot signals at the browser level. Best for heavily
    protected sites.
    """
    try:
        import camoufox
        from selenium.webdriver import Firefox
        from selenium.webdriver.firefox.options import Options

        opts = Options()
        opts.add_argument("--headless")
        opts.add_argument("--width=1920")
        opts.add_argument("--height=1080")

        with camoufox.start(headless=True) as fox:
            driver = Firefox(executable_path=fox.executable, options=opts)
            driver.set_page_load_timeout(45)
            driver.get(url)
            time.sleep(wait)  # Cloudflare challenge grace period

            result = {
                "title": driver.title,
                "html": driver.page_source,
                "text": driver.find_element("tag name", "body").text[:2000],
                "url": driver.current_url,
            }
            driver.quit()
            return result

    except ImportError:
        logger.error("camoufox not installed: pip install camoufox")
        return {"error": "camoufox missing"}


# ---------------------------------------------------------------------------
# 2. undetected-chromedriver
# ---------------------------------------------------------------------------

def scrape_with_uc(url: str, wait: float = 3.0) -> dict:
    """
    undetected-chromedriver patches ChromeDriver to avoid the
    navigator.webdriver detection. Good for moderate bot protection.
    """
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By

        opts = uc.ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")

        driver = uc.Chrome(options=opts, version_main=131)
        driver.set_window_size(1920, 1080)
        driver.get(url)
        time.sleep(wait)

        result = {
            "title": driver.title,
            "html": driver.page_source,
            "text": driver.find_element(By.TAG_NAME, "body").text[:2000],
            "url": driver.current_url,
        }
        driver.quit()
        return result

    except ImportError:
        logger.error("undetected-chromedriver not installed: pip install undetected-chromedriver")
        return {"error": "uc missing"}
    except Exception as exc:
        logger.error("UC scrape failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 3. selenium-stealth
# ---------------------------------------------------------------------------

def scrape_with_stealth(url: str, wait: float = 3.0) -> dict:
    """
    selenium-stealth masks Selenium fingerprints on an existing
    Chrome/Firefox driver. Use when you already have a driver setup
    and just want to add stealth patches.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium_stealth import stealth

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-blink-features=AutomationControlled")

        driver = webdriver.Chrome(options=opts)
        driver.set_window_size(1920, 1080)

        # Apply stealth patches
        stealth(driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                fix_hairline=True,
                )

        driver.get(url)
        time.sleep(wait)

        result = {
            "title": driver.title,
            "html": driver.page_source,
            "text": driver.find_element(By.TAG_NAME, "body").text[:2000],
            "url": driver.current_url,
        }
        driver.quit()
        return result

    except ImportError:
        logger.error("selenium-stealth not installed: pip install selenium-stealth")
        return {"error": "stealth missing"}
    except Exception as exc:
        logger.error("Stealth scrape failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 4. Playwright stealth
# ---------------------------------------------------------------------------

async def scrape_with_playwright_stealth(url: str, wait: float = 3.0) -> dict:
    """
    Playwright has strong stealth defaults. Use context.add_init_script
    to mask webdriver and other fingerprints.
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            # Mask automation signals
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            """)

            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(wait * 1000)

            result = {
                "title": await page.title(),
                "html": await page.content(),
                "text": await page.evaluate("document.body.innerText")[:2000],
                "url": page.url,
            }
            await browser.close()
            return result

    except ImportError:
        return {"error": "playwright not installed"}
    except Exception as exc:
        logger.error("Playwright stealth scrape failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Quick comparison helper
# ---------------------------------------------------------------------------

def compare_all(url: str):
    """Run all 4 methods and compare results."""
    print("\n=== 1. Camoufox ===")
    r1 = scrape_with_camoufox(url)
    print(f"  Title: {r1.get('title', 'FAIL')[:60]}")

    print("\n=== 2. undetected-chromedriver ===")
    r2 = scrape_with_uc(url)
    print(f"  Title: {r2.get('title', 'FAIL')[:60]}")

    print("\n=== 3. selenium-stealth ===")
    r3 = scrape_with_stealth(url)
    print(f"  Title: {r3.get('title', 'FAIL')[:60]}")

    print("\n=== 4. Playwright stealth ===")
    import asyncio
    r4 = asyncio.run(scrape_with_playwright_stealth(url))
    print(f"  Title: {r4.get('title', 'FAIL')[:60]}")


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    compare_all(target)
