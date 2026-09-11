"""
httpx_parsel.py
=============
Fast async scraping with httpx (HTTP/2) + parsel (CSS/XPath selectors).

This is the "speed demon" stack for sites that don't need a full browser.
httpx supports HTTP/2, connection pooling, and async/await.
parsel gives you Scrapy-style selectors without the framework overhead.

Install:
    pip install httpx parsel
"""

import asyncio
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass

import httpx
from parsel import Selector

logger = logging.getLogger(__name__)


@dataclass
class FastScrapeResult:
    url: str
    status: int
    title: str = ""
    text: str = ""
    links: List[str] = []
    images: List[str] = []
    html: str = ""
    error: str = ""


HEADERS_POOL = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                       "Version/17.4 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    },
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                       "Gecko/20100101 Firefox/128.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    },
]


async def fast_scrape(url: str, headers: Optional[dict] = None,
                      timeout: float = 15.0, rotate_ua: bool = True) -> FastScrapeResult:
    """
    Fast async scrape using httpx + parsel selectors.
    No browser overhead — pure HTTP.
    """
    if headers is None and rotate_ua:
        import random
        headers = random.choice(HEADERS_POOL)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            http2=True,
            headers=headers,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            sel = Selector(text=resp.text)

            title = sel.css("title::text").get("").strip()
            # Fallback: og:title
            if not title:
                title = sel.css('meta[property="og:title"]::attr(content)').get("").strip()

            text_parts = sel.css("body ::text").getall()
            text = " ".join(t.strip() for t in text_parts if t.strip())
            text = " ".join(text.split())[:5000]

            links = sel.css("a[href]::attr(href)").getall()
            links = list(dict.fromkeys(links))  # deduplicate

            images = sel.css("img[src]::attr(src)").getall()
            images = list(dict.fromkeys(images))

            return FastScrapeResult(
                url=str(resp.url),
                status=resp.status_code,
                title=title,
                text=text,
                links=links,
                images=images,
                html=resp.text,
            )

    except httpx.HTTPStatusError as exc:
        return FastScrapeResult(url=url, status=exc.response.status_code,
                                error=str(exc))
    except Exception as exc:
        logger.error("fast_scrape failed %s: %s", url, exc)
        return FastScrapeResult(url=url, status=0, error=str(exc))


async def batch_fast_scrape(urls: List[str], concurrency: int = 10,
                            timeout: float = 15.0) -> List[FastScrapeResult]:
    """
    Scrape multiple URLs concurrently with httpx.
    Control concurrency to avoid overwhelming servers.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _scrape_one(url: str) -> FastScrapeResult:
        async with semaphore:
            return await fast_scrape(url, timeout=timeout)

    tasks = [_scrape_one(url) for url in urls]
    results = await asyncio.gather(*tasks)
    return list(results)


def scrape_with_xpath(url: str, xpath: str) -> List[str]:
    """
    Quick helper: extract data via XPath.
    Example xpath: "//h1/text()" or "//div[@class='article']//p/text()"
    """
    import requests
    resp = requests.get(url, headers=HEADERS_POOL[0], timeout=15)
    sel = Selector(text=resp.text)
    return sel.xpath(xpath).getall()


def scrape_with_css(url: str, css: str) -> List[str]:
    """
    Quick helper: extract data via CSS selector.
    Example css: "h1::text" or "div.article p::text"
    """
    import requests
    resp = requests.get(url, headers=HEADERS_POOL[0], timeout=15)
    sel = Selector(text=resp.text)
    return sel.css(css).getall()


# ---------------------------------------------------------------------------
# Async link crawler (httpx-based)
# ---------------------------------------------------------------------------

async def async_link_crawler(start_url: str, max_pages: int = 50,
                              same_domain: bool = True,
                              delay: float = 0.5) -> Dict[str, dict]:
    """
    Async link crawler using httpx. Faster than requests-based BFS.
    """
    from urllib.parse import urlparse, urljoin
    import random

    seen: set = set()
    results: dict = {}
    queue = asyncio.Queue()
    await queue.put((start_url, 0))
    start_domain = urlparse(start_url).netloc
    ua_rotation = HEADERS_POOL

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=15.0,
        http2=True,
    ) as client:
        while not queue.empty() and len(results) < max_pages:
            url, depth = await queue.get()

            if url in seen or depth > 10:
                continue
            seen.add(url)

            if same_domain:
                if urlparse(url).netloc != start_domain:
                    continue

            try:
                headers = random.choice(ua_rotation)
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    results[url] = {"status": resp.status_code}
                    continue

                sel = Selector(text=resp.text)
                title = sel.css("title::text").get("").strip()
                text_preview = " ".join(sel.css("body ::text").getall())
                text_preview = " ".join(text_preview.split())[:300]

                links = sel.css("a[href]::attr(href)").getall()
                links = [urljoin(url, l) for l in links]

                results[url] = {
                    "title": title,
                    "status": resp.status_code,
                    "text_preview": text_preview,
                    "links": links[:20],
                    "depth": depth,
                }
                logger.info("[%d] %s", len(results), url)

                # Enqueue new links
                for link in links[:10]:
                    await queue.put((link, depth + 1))

                await asyncio.sleep(delay)

            except Exception as exc:
                results[url] = {"error": str(exc)}

    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"

    # Single fast scrape
    result = asyncio.run(fast_scrape(target))
    print(f"Status : {result.status}")
    print(f"Title  : {result.title}")
    print(f"Links  : {len(result.links)}")
    print(f"Text   : {result.text[:200]}...")

    # Batch
    urls = [target, "https://example.org", "https://httpbin.org/html"]
    results = asyncio.run(batch_fast_scrape(urls, concurrency=3))
    for r in results:
        print(f"  {r.status}  {r.title[:50]}  {r.url}")
