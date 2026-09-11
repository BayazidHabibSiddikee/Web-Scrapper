"""
crawlers.py
=========
Lightweight crawlers / spider patterns:

1. BFS crawler — breadth-first domain crawler with depth limit
2. Sitemap crawler — parse sitemap.xml, fetch all URLs
3. Link spider — recursive link extraction with dedup
4. API endpoint finder — hunt for JSON/API URLs in page source
5. Directory brute-force (light) — common paths wordlist check

Usage:
    python crawlers.py https://example.com --depth 2
    python crawlers.py https://example.com --mode sitemap
    python crawlers.py https://example.com --mode api-hunter
"""

import re
import time
import json
import logging
import argparse
import urllib.parse
from collections import deque
from typing import Set, Dict, List, Optional
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ScrapyKit/1.0; +https://example.com/bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ---------------------------------------------------------------------------
# 1. BFS crawler
# ---------------------------------------------------------------------------

def bfs_crawl(start_url: str, max_depth: int = 2, max_pages: int = 50,
              same_domain: bool = True, delay: float = 1.0) -> Dict[str, dict]:
    """
    Breadth-first crawl starting from start_url.
    Returns {url: {title, status_code, links, text_preview}}.
    """
    seen: Set[str] = set()
    results: Dict[str, dict] = {}
    queue = deque([(start_url, 0)])
    start_domain = urllib.parse.urlparse(start_url).netloc

    while queue and len(results) < max_pages:
        url, depth = queue.popleft()
        if url in seen or depth > max_depth:
            continue
        seen.add(url)

        if same_domain:
            domain = urllib.parse.urlparse(url).netloc
            if domain != start_domain:
                continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
            status = resp.status_code
            if status != 200:
                results[url] = {"status": status, "error": f"HTTP {status}"}
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            title = soup.title.string.strip() if soup.title else ""
            text_preview = soup.get_text(separator=" ", strip=True)[:300]

            # Extract links
            links = []
            for a in soup.find_all("a", href=True):
                href = urllib.parse.urljoin(url, a["href"])
                links.append(href)
                if depth + 1 <= max_depth:
                    queue.append((href, depth + 1))

            results[url] = {
                "title": title,
                "status": status,
                "links": links[:20],
                "text_preview": text_preview,
                "depth": depth,
            }
            logger.info("[%d] %s", len(results), url)

            time.sleep(delay)

        except Exception as exc:
            results[url] = {"status": None, "error": str(exc)}

    return results


# ---------------------------------------------------------------------------
# 2. Sitemap crawler
# ---------------------------------------------------------------------------

def crawl_sitemap(base_url: str) -> List[str]:
    """
    Fetch sitemap.xml (or sitemap index) and return all URLs.
    Handles nested sitemaps.
    """
    urls = []
    sitemap_url = urllib.parse.urljoin(base_url, "/sitemap.xml")

    def _parse_sitemap(xml_content: str) -> List[str]:
        soup = BeautifulSoup(xml_content, "xml")
        found = []
        # Regular sitemap: <url><loc>...</loc></url>
        for loc in soup.find_all("loc"):
            text = loc.get_text(strip=True)
            if text.endswith(".xml"):
                # It's a sitemap index — recurse
                found.extend(_fetch_sitemap_list(text))
            else:
                found.append(text)
        return found

    def _fetch_sitemap_list(sitemap_url: str) -> List[str]:
        try:
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                return _parse_sitemap(resp.text)
        except Exception as exc:
            logger.warning("Sitemap fetch failed %s: %s", sitemap_url, exc)
        return []

    urls = _fetch_sitemap_list(sitemap_url)
    logger.info("Sitemap: found %d URLs", len(urls))
    return urls


# ---------------------------------------------------------------------------
# 3. Link spider (recursive)
# ---------------------------------------------------------------------------

def spider_links(url: str, max_links: int = 100) -> Dict[str, List[str]]:
    """
    Single-page link spider. Returns {url: [outgoing_links]}.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            links.append(urllib.parse.urljoin(url, a["href"]))
        return {url: links[:max_links]}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 4. API endpoint hunter
# ---------------------------------------------------------------------------

def hunt_api_endpoints(url: str, html: Optional[str] = None) -> Dict[str, List[str]]:
    """
    Scan HTML/JS for potential API endpoints:
    - /api/* paths
    - /v1/*, /v2/* paths
    - fetch() / axios calls
    - .json URLs
    """
    if not html:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        html = resp.text

    soup = BeautifulSoup(html, "lxml")
    # Combine inline scripts and external JS references
    scripts = [s.string or "" for s in soup.find_all("script") if s.string]

    patterns = [
        r'["\'](/api/[^"\']+)["\']',
        r'["\'](/v\d+/[^"\']+)["\']',
        r'["\']([^"\']+\.json)["\']',
        r'fetch\(["\']([^"\']+)["\']',
        r'axios\.[a-z]+\(["\']([^"\']+)["\']',
        r'["\'](/graphql)["\']',
        r'["\'](/rest/[^"\']+)["\']',
    ]

    found: Set[str] = set()
    for script in scripts:
        for pat in patterns:
            matches = re.findall(pat, script, re.IGNORECASE)
            found.update(matches)

    # Resolve to absolute URLs
    absolute = [urllib.parse.urljoin(url, p) for p in found]
    return {"api_endpoints": sorted(set(absolute))}


# ---------------------------------------------------------------------------
# 5. Light directory brute-force
# ---------------------------------------------------------------------------

COMMON_PATHS = [
    "admin", "login", "api", "robots.txt", "sitemap.xml",
    "wp-admin", "wp-login.php", ".env", "config.php",
    "backup", "uploads", "files", "download", "static",
    "assets", "js", "css", "images", "img", "fonts",
    "swagger", "docs", "api/docs", "graphql", "health",
    "status", "metrics", "debug", "console", "actuator",
    ".git", ".svn", "web.config", "package.json",
    "composer.json", "Gemfile", "Dockerfile", "docker-compose.yml",
]


def dir_bruteforce(base_url: str, paths: Optional[List[str]] = None,
                   timeout: float = 5.0) -> List[dict]:
    """
    Light path scanner — checks common paths for existence.
    Returns list of {path, status, size}.
    """
    if paths is None:
        paths = COMMON_PATHS

    found = []
    for path in paths:
        test_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path)
        try:
            resp = requests.head(test_url, headers=HEADERS, timeout=timeout,
                                 allow_redirects=True)
            if resp.status_code < 400:
                found.append({
                    "path": test_url,
                    "status": resp.status_code,
                    "size": len(resp.content),
                })
        except Exception:
            pass
        time.sleep(0.1)

    return found


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Web crawler toolkit")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("--mode", choices=["bfs", "sitemap", "spider", "api", "dir"],
                        default="bfs", help="Crawler mode")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    if args.mode == "bfs":
        results = bfs_crawl(args.url, args.depth, args.max_pages, delay=args.delay)
        for url, data in results.items():
            print(f"  {data.get('status', '?')}  {url}")

    elif args.mode == "sitemap":
        urls = crawl_sitemap(args.url)
        for u in urls[:20]:
            print(f"  {u}")

    elif args.mode == "spider":
        result = spider_links(args.url)
        for url, links in result.items():
            print(f"\n  From: {url}")
            for link in links[:20]:
                print(f"    {link}")

    elif args.mode == "api":
        endpoints = hunt_api_endpoints(args.url)
        for ep in endpoints.get("api_endpoints", []):
            print(f"  {ep}")

    elif args.mode == "dir":
        findings = dir_bruteforce(args.url)
        for f in findings:
            print(f"  [{f['status']}] {f['path']}  ({f['size']} bytes)")
