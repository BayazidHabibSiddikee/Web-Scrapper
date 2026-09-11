"""
playwright_screenshot.py
=======================
Playwright screenshot examples:
  - Viewport screenshot
  - Full-page screenshot
  - Element screenshot
  - Batch screenshot multiple URLs

Requires: pip install playwright && playwright install chromium
"""

import asyncio
from pathlib import Path
from typing import List, Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext


async def screenshot(url: str, output: str, full_page: bool = True,
                    viewport: tuple = (1920, 1080), wait: int = 3) -> Optional[str]:
    """Take a single screenshot."""
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context: BrowserContext = await browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        # Anti-fingerprinting tweaks
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)

        page: Page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(wait * 1000)

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        if full_page:
            await page.screenshot(path=output, full_page=True)
        else:
            await page.screenshot(path=output)

        print(f"[+] Screenshot saved: {output}")
        await browser.close()
        return output


async def screenshot_element(url: str, selector: str, output: str) -> Optional[str]:
    """Screenshot a specific element."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=45000)
        el = await page.wait_for_selector(selector, timeout=10000)
        if el:
            await el.screenshot(path=output)
            print(f"[+] Element screenshot saved: {output}")
            await browser.close()
            return output
        await browser.close()
        return None


async def batch_screenshots(urls: List[str], out_dir: str = "shots",
                            full_page: bool = True) -> List[str]:
    """Screenshot a list of URLs into out_dir."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        saved = []
        for idx, url in enumerate(urls, 1):
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                slug = re.sub(r"[^a-zA-Z0-9_-]", "_", url)[:40]
                path = str(Path(out_dir) / f"{idx:03d}_{slug}.png")
                if full_page:
                    await page.screenshot(path=path, full_page=True)
                else:
                    await page.screenshot(path=path)
                saved.append(path)
                print(f"[{idx}/{len(urls)}] {url} -> {path}")
            except Exception as exc:
                print(f"[!] Failed {url}: {exc}")
            finally:
                await page.close()

        await browser.close()
        return saved


if __name__ == "__main__":
    import re

    # Example: single screenshot
    asyncio.run(screenshot(
        "https://example.com",
        output="examples/screenshot/example.png",
        full_page=True,
        wait=2,
    ))
