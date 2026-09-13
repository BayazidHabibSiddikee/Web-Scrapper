#!/usr/bin/env python3
"""
maps_scraper.py
==============
Google Maps business-listing scraper merged from google-maps-scraper-kit
(© wraps gosom/google-maps-scraper, MIT). Lead-gen pipeline in one command:

    python maps_scraper.py "gyms in Miami FL" --city "Miami, FL" --depth 5 --socials

What you get per business: name, phone, emails, website, category, address,
review rating/count — plus (with --socials) Instagram/Facebook/LinkedIn handles
found on each business website.

Requires the gosom scraper service (Docker):
    docker compose -f maps.compose.yml up -d      # in this directory
    (verify: curl http://localhost:8080/api/v1/jobs)

Unlike the original kit, website visits for email/social enrichment go through
THIS toolkit's stealth stack (httpx-with-HTTP/2 first, Camoufox fallback), so
business sites that block plain urllib no longer silently return nothing.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("maps_scraper")

BASE = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8080")
KEY = os.environ.get("SCRAPER_API_KEY", "")
UA = "web-scraper-toolkit/2.0 (merged google-maps-scraper-kit)"

# Money-useful LEAD fields only — what you actually use to contact/qualify a lead.
LEAD = ["title", "phone", "emails", "website", "category", "address",
        "review_rating", "review_count"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
SOCIAL_RE = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook":  re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/([A-Za-z0-9_.\-]+)", re.I),
    "linkedin":  re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/([A-Za-z0-9_.\-%]+)", re.I),
}
_SKIP_HANDLE = {"", "home", "pages", "people", "help", "about", "policies", "policy",
                "legal", "tos", "privacy", "settings", "sharer", "tr", "profile.php",
                "plugins", "dialog", "intent", "login", "share.php", "permalink.php",
                "p", "reel", "reels", "explore", "stories", "tv", "watch", "events",
                "groups", "marketplace", "gaming", "photo", "hashtag", "search"}


# ---------------------------------------------------------------------------
# Stealth HTTP for enrichment (merge of the toolkit's own scrapers)
# ---------------------------------------------------------------------------

def _fetch_html_stealth(url: str, timeout: int = 12) -> str:
    """Fetch a business website HTML, escalating urllib -> httpx -> Camoufox.

    Business URLs come from (untrusted) listing data, so every candidate URL
    is SSRF-guarded before any fetcher touches it.
    """
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        from security_utils import assert_public_url
        url = assert_public_url(url)
    except Exception:
        return ""

    # 1. Plain urllib (fastest, matches original kit behaviour)
    try:
        r = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; " + UA + ")", "Accept": "text/html"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read(500_000).decode("utf-8", "replace")
    except Exception:
        pass

    # 2. httpx HTTP/2 with browser-like headers
    try:
        import httpx
        with httpx.Client(http2=True, follow_redirects=True, timeout=timeout) as c:
            resp = c.get(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/126.0.0.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            if resp.status_code < 400:
                return resp.text[:500_000]
    except Exception:
        pass

    # 3. Camoufox stealth browser (slow — only for stubborn sites)
    try:
        from scraper import scrape, ScrapeConfig
        result = scrape(ScrapeConfig(url=url, wait_seconds=2.0,
                                     extra_wait_for_cloudflare=False))
        return result.html or ""
    except Exception:
        return ""


def _find_socials(html: str) -> Dict[str, str]:
    out = {"instagram": "", "facebook": "", "linkedin": ""}
    for plat, rx in SOCIAL_RE.items():
        for m in rx.finditer(html or ""):
            h = m.group(1).lower()
            if h in _SKIP_HANDLE:
                continue
            if plat == "facebook" and (h.isdigit() or len(h) < 3):
                continue
            out[plat] = m.group(0).rstrip('"\'/').replace("\\", "")
            break
    return out


def enrich_sites(results: List[Dict], workers: int = 8, emails: bool = True,
                 socials: bool = True) -> None:
    """Visit each business website once; add emails + social handles in-place.
    Zero LLM tokens — pure code."""
    for r in results:
        r.setdefault("instagram", "")
        r.setdefault("facebook", "")
        r.setdefault("linkedin", "")
        if emails:
            r.setdefault("emails", "")
    todo = [r for r in results if r.get("website")]
    done = 0

    def work(r):
        html = _fetch_html_stealth(r["website"])
        if socials:
            r.update(_find_socials(html))
        if emails and html and not r.get("emails"):
            found = EMAIL_RE.findall(html)
            junk = ("example.com", "domain.com", "sentry", "wixpress", ".png", ".jpg",
                    ".gif", ".webp", ".svg", "@2x", "u003e")
            clean = sorted({e.lower() for e in found
                            if not any(j in e.lower() for j in junk)})[:5]
            if clean:
                r["emails"] = ",".join(clean)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(work, todo):
            done += 1
            print(f"\r  enrich: {done}/{len(todo)} sites", end="", flush=True)
    print()


# ---------------------------------------------------------------------------
# gosom API client
# ---------------------------------------------------------------------------

def _req(method: str, path: str, body=None) -> Tuple[int, bytes]:
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if KEY:
        headers["X-API-Key"] = KEY
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return resp.status, resp.read()


def geocode(place: str) -> Optional[Tuple[str, str]]:
    """City/place -> (lat, lon) via OpenStreetMap Nominatim. Keep to ~1 req/s."""
    q = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            hits = json.loads(resp.read())
        time.sleep(1)  # Nominatim policy
        if hits:
            return str(hits[0]["lat"]), str(hits[0]["lon"])
    except Exception as e:
        log.warning("geocoding failed: %s", e)
    return None


def health_check() -> bool:
    try:
        _req("GET", "/api/v1/jobs")
        return True
    except Exception:
        return False


def run_job(keywords: List[str], lat: str, lon: str, depth: int = 5,
            email: bool = True, max_time: int = 600, radius: int = 10000) -> List[Dict]:
    """Create a gosom job, poll to completion, return parsed rows."""
    body = {"name": "web-scraper-toolkit", "keywords": keywords, "lang": "en",
            "zoom": 15, "lat": str(lat), "lon": str(lon), "fast_mode": False,
            "radius": radius, "depth": depth, "email": email, "max_time": max_time}
    print(f"▶ Creating job: {len(keywords)} keyword(s) @ {lat},{lon} depth={depth}")
    for k in keywords:
        print(f"    • {k}")
    try:
        _, raw = _req("POST", "/api/v1/jobs", body)
    except urllib.error.HTTPError as e:
        sys.exit(f"✗ Create failed: HTTP {e.code} — {e.read().decode()[:200]}")
    job_id = json.loads(raw).get("id")
    if not job_id:
        sys.exit("✗ No job id returned.")
    print(f"  job id: {job_id}")

    print("▶ Polling…")
    for i in range(120):
        _, raw = _req("GET", f"/api/v1/jobs/{job_id}")
        status = json.loads(raw).get("Status")
        print(f"\r  status: {str(status):<10} (attempt {i + 1})", end="", flush=True)
        if status == "ok":
            print()
            break
        if status == "failed":
            sys.exit("\n✗ Job failed. Repeated failures usually mean Google IP "
                     "rate-limiting — wait a while or add proxies.")
        time.sleep(8)
    else:
        sys.exit("\n✗ Timed out polling the job.")

    _, raw = _req("GET", f"/api/v1/jobs/{job_id}/download")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Google Maps business scraper (gosom API + stealth enrichment)")
    ap.add_argument("keyword", nargs="?", help='e.g. "coffee shops in Austin TX"')
    ap.add_argument("lat", nargs="?", help="latitude (optional if --city given)")
    ap.add_argument("lon", nargs="?", help="longitude (optional if --city given)")
    ap.add_argument("--keyword", dest="also", action="append", help="extra keyword (repeatable)")
    ap.add_argument("--keywords-file", help="file with one keyword per line (batch)")
    ap.add_argument("--city", help="city/place to auto-geocode for lat/lon")
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--no-email", dest="email", action="store_false", default=True,
                    help="skip email enrichment for a fast run")
    ap.add_argument("--socials", action="store_true",
                    help="enrich Instagram/Facebook/LinkedIn from each website")
    ap.add_argument("--max-time", type=int, default=600)
    ap.add_argument("--workers", type=int, default=8, help="enrichment thread count")
    ap.add_argument("--out", default=None, help="output file (.csv or .json)")
    ap.add_argument("--json", action="store_true", help="force JSON output")
    ap.add_argument("--full", action="store_true", help="keep all raw gosom columns")
    ap.add_argument("--fields", help="comma-separated columns to keep")
    a = ap.parse_args()

    keywords = []
    if a.keyword:
        keywords.append(a.keyword)
    keywords.extend(a.also or [])
    if a.keywords_file:
        with open(a.keywords_file) as f:
            keywords.extend(line.strip() for line in f
                            if line.strip() and not line.startswith("#"))
    seen, uniq = set(), []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    keywords = uniq
    if not keywords:
        sys.exit("✗ No keywords. Pass a keyword or --keywords-file.")

    if not health_check():
        sys.exit(f"✗ Scraper API not reachable at {BASE}\n"
                 f"  Start it:  docker compose -f maps.compose.yml up -d\n"
                 f"  Or set SCRAPER_BASE_URL to a remote instance.")

    lat, lon = a.lat, a.lon
    if not (lat and lon):
        place = a.city or keywords[0]
        print(f"▶ Geocoding \"{place}\"…")
        coords = geocode(place)
        if not coords:
            sys.exit("✗ Could not resolve coordinates. Pass lat/lon or a clearer --city.")
        lat, lon = coords
        print(f"  → {lat}, {lon}")

    if a.depth >= 15 or len(keywords) >= 10:
        print("⚠️  Large job (high depth / many keywords) — repeated runs without "
              "proxies can get your IP temporarily rate-limited by Google. Proceeding…\n")

    rows = run_job(keywords, lat, lon, depth=a.depth, email=a.email,
                   max_time=a.max_time)

    if a.full:
        fields = list(rows[0].keys()) if rows else LEAD
    elif a.fields:
        fields = [c.strip() for c in a.fields.split(",") if c.strip()]
    else:
        fields = LEAD
    results = [{k: r.get(k, "") for k in fields} for r in rows]
    print(f"✓ {len(results)} businesses scraped.")

    # Stealth enrichment pass (our upgrade over the kit: emails + socials via
    # httpx/Camoufox so blocked sites still yield data).
    if a.socials or (a.email and any(not r.get("emails") for r in results if r.get("website"))):
        print("▶ Stealth-enriching business websites (0 LLM tokens)…")
        enrich_sites(results, workers=a.workers, emails=a.email, socials=a.socials)
        if a.socials:
            fields = fields + ["instagram", "facebook", "linkedin"]
            found = sum(1 for r in results
                        if r.get("instagram") or r.get("facebook") or r.get("linkedin"))
            print(f"  socials found for {found}/{len(results)} businesses")

    as_json = a.json or (a.out and a.out.lower().endswith(".json"))
    out = a.out or f"output/maps-results-{int(time.time())}.{'json' if as_json else 'csv'}"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if as_json:
        with open(out, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    else:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
    print(f"  saved → {out}")
    for r in results[:5]:
        tail = f" | IG:{r.get('instagram') or '—'}" if a.socials else f" | {r.get('website','')}"
        print(f"  • {r.get('title','')} | {r.get('phone','')} | {r.get('emails','')}{tail}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
