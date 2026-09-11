#!/usr/bin/env python3
"""
grab_images.py — Scrape pages and download all images from them.

Usage:
    python grab_images.py <url>... [--out output/images] [--max N] [--min-size 10000]
"""

import asyncio
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("grab_images")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://github.com/",
}

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif"}


async def collect_image_urls(page, base_url: str) -> list:
    """Extract image URLs from a loaded page: <img>, srcset, og:image, CSS bg not covered."""
    urls = await page.evaluate("""
        () => {
            const found = new Set();
            // <img> tags
            document.querySelectorAll('img').forEach(img => {
                if (img.src) found.add(img.src);
                if (img.currentSrc) found.add(img.currentSrc);
            });
            // Open Graph images
            document.querySelectorAll('meta[property="og:image"], meta[name="twitter:image"]').forEach(m => {
                const c = m.getAttribute('content');
                if (c) found.add(c);
            });
            // favicon
            document.querySelectorAll('link[rel*="icon"]').forEach(l => {
                if (l.href) found.add(l.href);
            });
            return Array.from(found);
        }
    """)
    # Resolve relative URLs
    from urllib.parse import urljoin
    return [urljoin(base_url, u) for u in urls if u]


async def download_images(urls: list, out_dir: Path, max_images: int,
                          min_size: int, referer: str) -> list:
    """Download images concurrently. Returns list of saved paths."""
    saved = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=20.0,
        headers={**HEADERS, "Referer": referer},
        http2=True,
    ) as client:
        sem = asyncio.Semaphore(6)

        async def _dl(url: str, idx: int) -> None:
            parsed = urlparse(url)
            ext = Path(parsed.path).suffix.lower() or ".png"
            name = f"{idx:03d}_{parsed.netloc.replace(':', '_')}{ext}"
            path = out_dir / name

            # Skip obvious icons unless nothing else
            if ext == ".ico" and idx > 5:
                return

            async with sem:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        return
                    data = resp.content
                    if len(data) < min_size:
                        return
                    path.write_bytes(data)
                    saved.append((path, len(data)))
                    log.info("  saved %s (%.1f KB)", path.name, len(data) / 1024)
                except Exception as exc:
                    log.debug("  failed %s: %s", url[:80], exc)

        tasks = [_dl(u, i) for i, u in enumerate(urls[:max_images * 3])]
        await asyncio.gather(*tasks)

    return saved


async def scrape_and_grab(url: str, out_dir: Path, max_images: int = 25,
                          min_size: int = 5000, screenshot: bool = True) -> dict:
    """Load a page with the toolkit scraper, screenshot it, download its images."""
    from scraper import scrape, ScrapeConfig

    result = scrape(ScrapeConfig(
        url=url,
        screenshot_path=str(out_dir / "_page_screenshot.png") if screenshot else None,
        screenshot_full_page=True,
        wait_seconds=4.0,
        scroll_to_bottom=True,   # trigger lazy-loaded images
    ))

    if result.error:
        log.error("[%s] scrape failed: %s", url, result.error)
        return {"url": url, "error": result.error}

    log.info("[%s] title=%r, %d links", url, result.title[:60], len(result.links))

    # Screenshot may be duplicate of what grabber saves — keep it as page shot

    # Re-open page to collect image URLs via browser (needs JS-rendered DOM)
    # Simplest: parse the HTML we already downloaded with BeautifulSoup
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    soup = BeautifulSoup(result.html, "lxml")
    img_urls = set()
    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "currentSrc"):
            v = img.get(attr)
            if v:
                img_urls.add(urljoin(url, v))
        # srcset first candidate
        ss = img.get("srcset")
        if ss:
            img_urls.add(urljoin(url, ss.split(",")[0].split()[0]))
    for m in soup.find_all("meta", attrs={"property": "og:image"}):
        if m.get("content"):
            img_urls.add(urljoin(url, m["content"]))
    for l in soup.find_all("link", rel=lambda r: r and "icon" in r):
        if l.get("href"):
            img_urls.add(urljoin(url, l["href"]))
    # CSS background-image (galleries, hero banners, cards)
    bg_re = re.compile(r"url\(['\"]?([^'\")]+)['\"]?\)")
    for el in soup.find_all(style=True):
        for m in bg_re.finditer(el["style"]):
            img_urls.add(urljoin(url, m.group(1)))
    for tag in soup.find_all("style"):
        for m in bg_re.finditer(tag.get_text() or ""):
            img_urls.add(urljoin(url, m.group(1)))

    log.info("[%s] found %d image URLs", url, len(img_urls))

    saved = await download_images(
        sorted(img_urls), out_dir, max_images, min_size, referer=url
    )
    total_kb = sum(sz for _, sz in saved) / 1024
    log.info("[%s] saved %d images (%.1f KB total)", url, len(saved), total_kb)

    return {
        "url": url,
        "title": result.title,
        "screenshot": str(out_dir / "_page_screenshot.png"),
        "images_found": len(img_urls),
        "images_saved": len(saved),
        "total_kb": round(total_kb, 1),
    }


async def main():
    targets = [
        ("https://github.com", "output/images/github"),
        ("https://www.python.org", "output/images/pythonorg"),
        ("https://news.ycombinator.com", "output/images/hackernews"),
    ]

    all_results = []
    for url, out_dir in targets:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        log.info("=== %s → %s ===", url, out)
        r = await scrape_and_grab(url, out, max_images=25, min_size=5000)
        all_results.append(r)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in all_results:
        if r.get("error"):
            print(f"  ✗ {r['url']}: {r['error'][:60]}")
        else:
            print(f"  ✓ {r['url']}: {r['images_saved']} images, {r['total_kb']} KB, shot: {r['screenshot']}")


if __name__ == "__main__":
    asyncio.run(main())
