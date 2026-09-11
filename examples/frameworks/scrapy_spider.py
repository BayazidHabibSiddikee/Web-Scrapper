"""
scrapy_spider.py
==============
Production Scrapy spider template.

Scrapy is the heavyweight champion of Python scraping:
  - Built-in concurrency, rate limiting, retry, redirect handling
  - Item pipelines for cleaning/saving data
  - Extensible middleware for proxies/auth/stealth
  - Feed exports (JSON, CSV, XML, FTP, S3)
  - Auto-throttle based on load

Install:
    pip install scrapy

Run:
    scrapy runspider scrapy_spider.py -a url=https://example.com -o results.json
"""

import scrapy
from scrapy.http import Request, Response
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from typing import Optional


class UniversalSpider(scrapy.Spider):
    name = "universal"
    custom_settings = {
        # Politeness
        "DOWNLOAD_DELAY": 1.0,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "CONCURRENT_REQUESTS": 4,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,

        # Retry logic
        "RETRY_TIMES": 3,
        "RETRY_HTTP_CODES": [429, 500, 502, 503, 504, 522, 524],

        # Redirects
        "REDIRECT_ENABLED": True,
        "REDIRECT_MAX_TIMES": 5,

        # User agent rotation (use scrapy-useragents or middleware)
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),

        # Output encoding
        "FEED_EXPORT_ENCODING": "utf-8",

        # Logging
        "LOG_LEVEL": "INFO",
    }

    # Spider args
    start_urls: Optional[list] = None
    max_pages: int = 50
    same_domain: bool = True
    allowed_domains: Optional[list] = None
    selectors: Optional[dict] = None  # {"title": "h1::text", "body": "div.content::text"}
    follow_links: bool = True
    link_selector: str = "a[href]"
    css_content: str = "body"  # default content area
    extract_tables: bool = False

    def __init__(self, url: str = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if url:
            self.start_urls = [url]
        self.pages_crawled = 0
        self.start_domain = None
        if self.start_urls:
            from urllib.parse import urlparse
            self.start_domain = urlparse(self.start_urls[0]).netloc

    def parse(self, response: Response, **kwargs):
        """Default parse — extracts data and follows links."""
        if self.pages_crawled >= self.max_pages:
            self.crawler.engine.close_spider(self, "max_pages_reached")
            return

        self.pages_crawled += 1
        self.logger.info(f"[{self.pages_crawled}/{self.max_pages}] {response.url}")

        item = {
            "url": response.url,
            "status": response.status,
            "title": response.css("title::text").get("").strip(),
            "content_type": response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore"),
        }

        # Custom selectors
        if self.selectors:
            for name, sel in self.selectors.items():
                extracted = response.css(sel).getall()
                item[name] = [s.strip() for s in extracted if s.strip()]

        # Default: body text
        if not self.selectors or "text" not in self.selectors:
            text = " ".join(response.css(f"{self.css_content} ::text").getall())
            text = " ".join(text.split())
            item["text"] = text[:5000]

        # Links
        links = response.css(f"{self.link_selector}::attr(href)").getall()
        item["links"] = list(dict.fromkeys(links))[:50]

        # Images
        images = response.css("img::attr(src)").getall()
        item["images"] = list(dict.fromkeys(images))[:50]

        # Tables (optional)
        if self.extract_tables:
            tables = []
            for table in response.css("table"):
                rows = []
                for tr in table.css("tr"):
                    row = [td.get_text(strip=True) for td in tr.css("td, th")]
                    if any(row):
                        rows.append(row)
                if rows:
                    tables.append(rows)
            item["tables"] = tables

        # Error detection
        if response.status >= 400:
            item["error"] = f"HTTP {response.status}"

        yield item

        # Follow links
        if self.follow_links and self.pages_crawled < self.max_pages:
            for href in item["links"]:
                absolute = response.urljoin(href)
                if self.same_domain and self.start_domain:
                    from urllib.parse import urlparse
                    if urlparse(absolute).netloc != self.start_domain:
                        continue
                yield Request(absolute, callback=self.parse, dont_filter=True)


class ArticleSpider(scrapy.Spider):
    """
    Specialized spider for article/content extraction.
    Uses Readability + trafilatura-style extraction.
    """
    name = "article_spider"
    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS": 2,
        "USER_AGENT": (
            "Mozilla/5.0 (compatible; ArticleBot/1.0; "
            "+https://example.com/bot)"
        ),
    }

    def __init__(self, urls: list = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = urls or []
        self.pages_crawled = 0
        self.max_pages = len(self.start_urls)

    def parse(self, response: Response, **kwargs):
        if self.pages_crawled >= self.max_pages:
            self.crawler.engine.close_spider(self, "max_pages")
            return

        self.pages_crawled += 1

        # Try to find article content
        content_selectors = [
            "article", "main", "#content", ".post-content",
            ".article-body", ".entry-content", '[role="main"]',
        ]

        text = ""
        for sel in content_selectors:
            extracted = response.css(f"{sel} ::text").getall()
            candidate = " ".join(t.strip() for t in extracted if t.strip())
            candidate = " ".join(candidate.split())
            if len(candidate) > len(text):
                text = candidate

        if not text:
            text = " ".join(response.css("body ::text").getall())
            text = " ".join(text.split())[:5000]

        yield {
            "url": response.url,
            "title": response.css("title::text").get("").strip(),
            "author": response.css('[name="author"]::attr(content), .author::text').get("").strip(),
            "publish_date": response.css('[name="date"]::attr(content), time::attr(datetime), .date::text').get("").strip(),
            "text": text,
            "links": list(dict.fromkeys(response.css("a[href]::attr(href)").getall()))[:30],
            "status": response.status,
        }


class APISpider(scrapy.Spider):
    """
    Spider that extracts API endpoints from page source and optionally
    tests them.
    """
    name = "api_spider"
    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS": 4,
    }

    def __init__(self, url: str = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [url] if url else []

    def parse(self, response: Response, **kwargs):
        import re
        from urllib.parse import urljoin

        html = response.text
        patterns = [
            r'["\'](/api/[^"\']+)["\']',
            r'["\'](/v\d+/[^"\']+)["\']',
            r'["\']([^"\']+\.json)["\']',
            r'fetch\(["\']([^"\']+)["\']',
            r'axios\.[a-z]+\(["\']([^"\']+)["\']',
            r'["\'](/graphql)["\']',
        ]

        endpoints = set()
        for pat in patterns:
            matches = re.findall(pat, html, re.IGNORECASE)
            endpoints.update(urljoin(response.url, m) for m in matches)

        yield {
            "source_url": response.url,
            "api_endpoints": sorted(endpoints),
            "count": len(endpoints),
        }

        # Follow links to find more APIs
        for href in response.css("a[href]::attr(href)").getall()[:10]:
            yield response.follow(href, callback=self.parse)


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------

def run_spider(spider_class, **kwargs):
    """Run a Scrapy spider from Python."""
    process = CrawlerProcess(settings={
        "FEEDS": {
            "output/results.json": {"format": "json", "encoding": "utf-8"},
            "output/results.csv": {"format": "csv", "encoding": "utf-8"},
        },
        "LOG_LEVEL": "INFO",
    })
    process.crawl(spider_class, **kwargs)
    process.start()


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Scrapy spider runner")
    parser.add_argument("url", nargs="?", help="Target URL")
    parser.add_argument("--mode", choices=["universal", "article", "api"],
                        default="universal")
    parser.add_argument("--pages", type=int, default=30)
    parser.add_argument("--output", default="output/scrapy_results")
    args = parser.parse_args()

    if not args.url:
        parser.print_help()
        sys.exit(1)

    spider_map = {
        "universal": UniversalSpider,
        "article": ArticleSpider,
        "api": APISpider,
    }
    spider_cls = spider_map[args.mode]

    if args.mode == "article":
        run_spider(spider_cls, urls=[args.url])
    elif args.mode == "api":
        run_spider(spider_cls, url=args.url)
    else:
        run_spider(spider_cls, url=args.url, max_pages=args.pages)
