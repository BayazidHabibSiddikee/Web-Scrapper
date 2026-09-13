#!/usr/bin/env python3
"""
saas_extract.py
==============
Multi-page website content extractor — merged from openshorts/saasshorts.py.

Purpose-built for "understand this whole site in one call" jobs (product
research, marketing analysis, video scripting): fetches a homepage, pulls the
marketing-relevant content, then follows same-host links whose URL hints at
pricing/features/about/product pages and scrapes up to N subpages.

Every request is re-validated through an SSRF guard (no redirect auto-follow;
each 30x hop is checked against private/link-local/metadata ranges) so scraped
data can never point this fetcher at internal hosts.

Usage:
    python saas_extract.py https://stripe.com
    python saas_extract.py https://stripe.com --subpages 5 --max-chars 20000

    from saas_extract import scrape_website
    data = scrape_website("https://stripe.com")
    print(data["title"], data["meta_description"], data["pages_scraped"])
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urljoin
from typing import Dict, List

# Allow running directly from examples/ as well as importing from the repo root
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("saas_extract")

DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36")
}

# Subpage discovery keywords (marketing-relevant paths)
SUBPAGE_KEYWORDS = ["pricing", "features", "about", "product", "why",
                    "how-it-works", "use-case", "customers", "platform",
                    "solutions", "tour", "demo"]

STRIP_TAGS = ["script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"]


def _get_guarded(client, url: str, max_hops: int = 5):
    """GET with SSRF-safe manual redirect handling: every hop re-validated."""
    from security_utils import assert_public_url
    current = assert_public_url(url)
    response = None
    for _ in range(max_hops):
        response = client.get(current, headers=DEFAULT_HEADERS)
        if response.has_redirect_location:
            current = assert_public_url(str(response.next_request.url))
            continue
        break
    return response


def _extract_page(html: str, url: str, max_text: int = 10000) -> Dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(STRIP_TAGS):
        tag.decompose()

    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag:
        meta_desc = meta_tag.get("content", "") or ""
    og_tag = soup.find("meta", attrs={"property": "og:description"})
    og_desc = (og_tag.get("content", "") or "") if og_tag else ""

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    headings: List[str] = []
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = h.get_text(strip=True)
        if text and len(text) < 200:
            headings.append(text)

    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)[:max_text]

    return {"url": url, "title": title,
            "meta_description": meta_desc or og_desc,
            "headings": headings[:20], "text": text, "soup": soup}


def scrape_website(url: str, max_subpages: int = 3, max_chars: int = 10000,
                   subpage_chars: int = 5000) -> Dict:
    """Scrape a SaaS-style website: homepage + marketing subpages."""
    import httpx

    log.info("Scraping %s ...", url)
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = _get_guarded(client, url)
        response.raise_for_status()

    page = _extract_page(response.text, url, max_text=max_chars)
    soup = page.pop("soup")

    # Find same-host marketing subpages
    base_host = httpx.URL(url).host
    subpages = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(kw in href for kw in SUBPAGE_KEYWORDS):
            try:
                full_url = urljoin(url, a["href"])
                full_host = httpx.URL(full_url).host
                if base_host and full_host and base_host == full_host:
                    subpages.add(full_url)
            except Exception:
                pass

    additional = ""
    scraped_sub = 0
    for sub_url in sorted(subpages)[:max_subpages]:
        try:
            log.info("  → Subpage: %s", sub_url)
            with httpx.Client(timeout=20.0, follow_redirects=False) as client:
                resp = _get_guarded(client, sub_url)
            if resp.status_code == 200:
                sub = _extract_page(resp.text, sub_url, max_text=subpage_chars)
                sub.pop("soup", None)
                additional += f"\n\n--- {sub['url']} ---\n{sub['text']}"
                scraped_sub += 1
        except Exception as e:
            log.warning("  ⚠️ Failed: %s", e)

    result = {
        "url": url,
        "title": page["title"],
        "meta_description": page["meta_description"],
        "headings": page["headings"],
        "main_content": page["text"],
        "additional_pages": additional[:max_chars * 2],
        "pages_scraped": 1 + scraped_sub,
    }
    log.info("Scraped %d pages, %d chars", result["pages_scraped"], len(page["text"]))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Multi-page SaaS website extractor")
    ap.add_argument("url")
    ap.add_argument("--subpages", type=int, default=3, help="max marketing subpages to crawl")
    ap.add_argument("--max-chars", type=int, default=10000)
    ap.add_argument("-o", "--output", help="save JSON to this path (default: stdout)")
    args = ap.parse_args()

    try:
        data = scrape_website(args.url, max_subpages=args.subpages,
                              max_chars=args.max_chars)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    out = json.dumps(data, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
        print(f"saved → {args.output}")
    else:
        print(out)
