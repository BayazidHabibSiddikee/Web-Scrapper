#!/usr/bin/env python3
"""
medex_scraper.py — Fast MedEx brand detail scraper

Strategy:
  1. Try httpx (HTTP/2, 100ms/page) — works for ~80% of pages
  2. If challenged, escalate to Scrapling stealth browser (solve CF/Turnstile)
  3. Rotate proxy on sustained blocks

Expected throughput: 500-2000 pages/hour vs 55 before
"""
import asyncio, json, logging, random, re, signal, sys, time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("medex")

BASE_URL = "https://medex.com.bd"
SECTION_MAP = {
    "indications": "indications", "mode_of_action": "pharmacology",
    "dosage": "dosage", "interaction": "interaction",
    "contraindications": "contraindications", "side_effects": "sideEffects",
    "pregnancy_cat": "pregnancyCategory", "precautions": "precautions",
    "storage_conditions": "storageConditions", "overdose_effects": "overdoseEffects",
    "drug_classes": "therapeuticClass",
}
CAPTCHA_MARKERS = ("captcha-challenge", "Security Check")


def parse_price(text: str) -> Dict[str, str]:
    out = {}
    m = re.search(r'Unit Price\s*:\s*[৳$\s]*([\d.]+)', text)
    if m: out["unitPrice"] = m.group(1)
    m = re.search(r'Strip Price\s*:\s*[৳$\s]*([\d.]+)', text)
    if m: out["stripPrice"] = m.group(1)
    m = re.search(r'(\d+)\s*x\s*(\d+)\s*:\s*[৳$\s]*([\d.]+)', text)
    if m: out.update(packSize=f"{m.group(1)} x {m.group(2)}", packPrice=m.group(3))
    return out


def is_blocked(html: str) -> bool:
    h = (html or "").lower()
    return any(m.lower() in h[:3000] for m in CAPTCHA_MARKERS)


def parse_detail(html: str) -> Dict[str, str]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    d: Dict[str, str] = {}
    header = soup.select_one(".brand-header")
    el = header.select_one('[title="Strength"]') if header else None
    if el and el.get_text(strip=True): d["strength"] = el.get_text(strip=True)
    pe = soup.select_one(".packages-wrapper")
    if pe: d.update(parse_price(pe.get_text(separator=" ")))
    cont = soup.select_one(".generic-data-container")
    if cont:
        for sid, fn in SECTION_MAP.items():
            h = cont.find("div", id=sid)
            if not h: continue
            b = h.find_next_sibling("div", class_="ac-body")
            t = (b.get_text(separator="\n").strip()) if b else ""
            if t: d[fn] = t
    return d


async def fetch_fast(url: str) -> Optional[str]:
    """Try httpx first (fast). Returns html or None."""
    import httpx
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=10, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200 and "captcha-challenge" not in r.url:
                return r.text
    except Exception:
        pass
    return None


async def fetch_slow(url: str) -> Optional[str]:
    """Fallback: browser-based fetch for blocked pages."""
    from scrapling.fetchers import AsyncStealthySession
    sess = AsyncStealthySession(headless=True, solve_cloudflare=True, timeout=20_000, disable_resources=True)
    await sess.start()
    try:
        r = await sess.fetch(url)
        body = getattr(r, "body", b"") or getattr(r, "html_content", b"")
        if isinstance(body, bytes): body = body.decode("utf-8", "ignore")
        return body if not is_blocked(body) else None
    except Exception:
        return None
    finally:
        await sess.close()


async def run_brands(brands_path: Path, details_path: Path, failed_path: Path,
                     limit: Optional[int] = None):
    brands = json.loads(brands_path.read_text())
    existing: Dict[int, Dict] = {}
    if details_path.exists():
        for item in json.loads(details_path.read_text()):
            existing[item["medexId"]] = item

    pending = []
    for b in brands:
        mid = b.get("medexId")
        if not mid or mid in existing:
            continue
        url = b.get("url", "")
        if not url.startswith("http"): url = BASE_URL + url.lstrip("/")
        pending.append((mid, url))
    if limit: pending = pending[:limit]

    # Load previous failures
    failed = []
    if failed_path.exists():
        try:
            failed = [f for f in json.loads(failed_path.read_text())
                      if f.get("medexId") not in existing]
        except Exception:
            pass

    def save():
        details_path.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=1))
        still = [f for f in failed if f.get("medexId") not in existing]
        failed_path.write_text(json.dumps(still, ensure_ascii=False))

    t0 = time.time()
    stats = {"http_ok": 0, "browser_esc": 0, "scraped": 0, "empty": 0, "blocked": 0}
    consecutive_blocks = 0

    for idx, (mid, url) in enumerate(pending, 1):
        # Strategy: try fast path first, fall back to browser on block
        html = await fetch_fast(url)
        if html:
            stats["http_ok"] += 1
            consecutive_blocks = 0
        else:
            # Escalate to browser
            html = await fetch_slow(url)
            if html:
                stats["browser_esc"] += 1
            else:
                stats["blocked"] += 1
                failed.append({"medexId": mid, "reason": "blocked", "url": url})
                consecutive_blocks += 1
                # If many consecutive blocks, take a break
                if consecutive_blocks > 10:
                    log.warning("%d consecutive blocks — sleeping 30s", consecutive_blocks)
                    await asyncio.sleep(30)
                continue
                continue

        if not html or is_blocked(html):
            stats["blocked"] += 1
            continue

        detail = parse_detail(html)
        if not detail or not any(detail.get(f) for f in SECTION_MAP.values()):
            stats["empty"] += 1
            failed.append({"medexId": mid, "reason": "empty", "url": url})
        else:
            row = {"medexId": mid, "url": url}
            row.update(detail)
            existing[mid] = row
            stats["scraped"] += 1

        # Save every 20 pages
        if idx % 20 == 0:
            save()
            elapsed = time.time() - t0
            rate = stats["scraped"] / max(elapsed, 1)
            eta = (len(pending) - idx) / max(rate, 0.01) / 3600
            log.info("%d/%d | scraped=%d http=%d browser=%d | %.1f/hr ETA %.1fh",
                     idx, len(pending), stats["scraped"],
                     stats["http_ok"], stats["browser_esc"],
                     rate * 3600, eta)

        # Polite delay between requests
        await asyncio.sleep(0.1 + random.uniform(0, 0.2))

    save()
    elapsed = time.time() - t0
    log.info("DONE: %d total | %d scraped | %d empty | %d blocked | %.0fs (%.0f/hr)",
             len(existing), stats["scraped"], stats["empty"], stats["blocked"],
             elapsed, stats["scraped"] / max(elapsed, 1) * 3600)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Fast MedEx scraper (httpx-first)")
    ap.add_argument("--brands-file", default="/home/sword/Downloads/scraper/output/brands.json", type=Path)
    ap.add_argument("--details-file", default="/home/sword/Downloads/scraper/output/brand_details.json", type=Path)
    ap.add_argument("--failed-file", default="/home/sword/Downloads/scraper/output/brand_details_failed.json", type=Path)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if not args.brands_file.exists():
        sys.exit(f"missing: {args.brands_file}")
    asyncio.run(run_brands(args.brands_file, args.details_file, args.failed_file, limit=args.limit))
