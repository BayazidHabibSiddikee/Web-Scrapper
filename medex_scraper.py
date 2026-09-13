#!/usr/bin/env python3
"""
medex_scraper.py
===============
MedEx.com.bd pharmaceutical brand scraper — merged from ~/Downloads/scraper
(their config.py SECTION_MAP, parse_price, captcha markers and resume design),
rebuilt on this toolkit's stack:

  async httpx (HTTP/2, TLS-spoofed via curl_cffi)  →  ~10-20 pages/s
  retry with exponential backoff                    →  transient blocks recover
  captcha/blocked page detected                     →  Scrapling stealth fallback
  incremental save + resume                         →  Ctrl+C safe, restart-safe

Domain knowledge ported from the original (medex.com.bd):
  detail sections keyed by accordion ids: indications, mode_of_action→
  pharmacology, dosage, interaction, contraindications, side_effects,
  pregnancy_cat, precautions, storage_conditions, overdose_effects,
  drug_classes → therapeuticClass. Prices in ৳ (BDT). robots.txt is fully
  open ("Disallow:" empty) — crawling is sanctioned.

Usage:
    # one-time list scrape (from their original, kept as subcommand):
    python medex_scraper.py lists                      # brands/generics/companies

    # fill detail pages (the resumable long job):
    python medex_scraper.py details                    # everything pending
    python medex_scraper.py details --limit 50         # pilot batch
    python medex_scraper.py details --workers 12

    # status:
    python medex_scraper.py status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("medex")

BASE_URL = "https://medex.com.bd"
OUT_DIR = Path(_ROOT / "output" / "medex")

# ── Ported from the original config.py (domain knowledge) ──────────────────
SECTION_MAP = {
    "indications": "indications",
    "mode_of_action": "pharmacology",
    "dosage": "dosage",
    "interaction": "interaction",
    "contraindications": "contraindications",
    "side_effects": "sideEffects",
    "pregnancy_cat": "pregnancyCategory",
    "precautions": "precautions",
    "storage_conditions": "storageConditions",
    "overdose_effects": "overdoseEffects",
    "drug_classes": "therapeuticClass",
}
DETAIL_FIELDS = ["strength"] + list(SECTION_MAP.values()) + [
    "unitPrice", "stripPrice", "packSize", "packPrice"]
BRANDS_URL = f"{BASE_URL}/brands"
GENERICS_URL = f"{BASE_URL}/generics"
COMPANIES_URL = f"{BASE_URL}/companies"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# Ported from their scraper: captcha interstitial markers
CAPTCHA_MARKERS = ("captcha-challenge", "Security Check")


# ---------------------------------------------------------------------------
# Parsers (ported from ~/Downloads/scraper, bs4 kept — it's the right tool)
# ---------------------------------------------------------------------------

def parse_price(text: str) -> Dict[str, str]:
    """৳ prices: 'Unit Price : ৳12.50 | Strip Price: ৳100 | 10 x 4 : ৳400'."""
    prices = {}
    cleaned = text.replace("|", " ")
    m = re.search(r"Unit Price\s*:\s*[৳$\s]*([\d.]+)", cleaned)
    if m:
        prices["unitPrice"] = m.group(1)
    m = re.search(r"Strip Price\s*:\s*[৳$\s]*([\d.]+)", cleaned)
    if m:
        prices["stripPrice"] = m.group(1)
    m = re.search(r"(\d+)\s*x\s*(\d+)\s*:\s*[৳$\s]*([\d.]+)", cleaned)
    if m:
        prices["packSize"] = f"{m.group(1)} x {m.group(2)}"
        prices["packPrice"] = m.group(3)
    return prices


def is_blocked(html: str) -> bool:
    head = html[:3000]
    return any(m in html[:8000] or m in head for m in CAPTCHA_MARKERS)


def parse_brand_detail(html: str) -> Dict[str, str]:
    from bs4 import BeautifulSoup
    detail: Dict[str, str] = {}
    soup = BeautifulSoup(html, "html.parser")

    header = soup.select_one(".brand-header")
    if header:
        el = header.select_one('[title="Strength"]')
        if el and el.get_text(strip=True):
            detail["strength"] = el.get_text(strip=True)

    price_el = soup.select_one(".packages-wrapper")
    if price_el:
        detail.update(parse_price(price_el.get_text(separator=" ")))

    container = soup.select_one(".generic-data-container")
    if container:
        for section_id, field_name in SECTION_MAP.items():
            h = container.find("div", id=section_id)
            if h:
                b = h.find_next_sibling("div", class_="ac-body")
                if b:
                    text = b.get_text(separator="\n").strip()
                    if text:
                        detail[field_name] = text
    return detail


def parse_brand_card(href: str, card) -> Dict[str, object]:
    """List-page brand card → seed row (ported selector logic)."""
    m_id = re.search(r"/(\d+)/", href)
    m_slug = re.search(r"/\d+/([^/?#]+)", href)
    row = {
        "medexId": int(m_id.group(1)) if m_id else None,
        "medexSlug": m_slug.group(1) if m_slug else None,
        "url": href,
    }
    for sel, key in ((".brand-card__name", "brandName"),
                     (".brand-card__strength", "strength"),
                     (".brand-card__generic", "genericName"),
                     (".brand-card__company", "companyName")):
        el = card.select_one(sel)
        row[key] = el.get_text(strip=True) if el else ""
    el = card.select_one(".dosage-icon")
    row["dosageForm"] = (el.get("alt") or "").strip() if el else ""
    return row


# ---------------------------------------------------------------------------
# Async detail runner with retry + stealth escalation + resume
# ---------------------------------------------------------------------------

class Stopper:
    def __init__(self):
        self.stop = False


async def _fetch_httpx(client, url: str, retries: int = 3) -> Tuple[Optional[str], int]:
    """GET with exponential backoff + jitter. Returns (html|None, status)."""
    last_status = 0
    for attempt in range(retries):
        try:
            r = await client.get(url, timeout=25)
            last_status = r.status_code
            if r.status_code == 200:
                return r.text, 200
            if r.status_code in (403, 429, 503):     # throttled — back off harder
                await asyncio.sleep((2 ** attempt) * 3 + random.uniform(0, 2))
                continue
            if 400 <= r.status_code < 500:
                return None, r.status_code            # 404 etc — no retry
            await asyncio.sleep((2 ** attempt) * 2 + random.uniform(0, 1))
        except Exception:
            await asyncio.sleep((2 ** attempt) * 2 + random.uniform(0, 1))
    return None, last_status


def _fetch_stealth(url: str) -> Optional[str]:
    """Escalation for a captcha-blocked URL: Scrapling TLS-spoof, then Camoufox."""
    try:
        from scrapling_backend import scrape_scrapling
        r = scrape_scrapling(url, mode="stealth", solve_cloudflare=True,
                             disable_resources=True)
        if r.status == 200 and r.html and not is_blocked(r.html):
            return r.html
    except Exception as exc:
        log.debug("scrapling escalation failed for %s: %s", url, exc)
    try:
        from scraper import scrape, ScrapeConfig
        r = scrape(ScrapeConfig(url=url, wait_seconds=4.0))
        if r.html and not is_blocked(r.html):
            return r.html
    except Exception as exc:
        log.debug("camoufox escalation failed for %s: %s", url, exc)
    return None


async def run_details_persistent(brands_file: Path, details_file: Path,
                                 failed_file: Path, limit: Optional[int] = None,
                                 delay: float = 0.35, save_every: int = 50,
                                 headless: bool = True):
    """
    One persistent Scrapling StealthySession (solve_cloudflare=True) does ALL
    the work: cookies accumulate naturally, and when medex's rate limiter
    raises the Cloudflare Turnstile wall, Scrapling clicks it in-session —
    no paid solver, and ONE browser for the whole job (not one per page).

    This is the mode that works against medex.com.bd's soft rate limit;
    run_details() (httpx-only) is for sites without a JS-gated wall.
    """
    from scrapling.fetchers import AsyncStealthySession

    brands = json.loads(brands_file.read_text())
    existing: Dict[int, Dict] = {}
    if details_file.exists() and details_file.stat().st_size > 2:
        for item in json.loads(details_file.read_text()):
            existing[item["medexId"]] = item

    pending: List[Tuple[int, str, Dict]] = []
    for b in brands:
        mid = b.get("medexId")
        if not mid or mid in existing:
            continue
        url = b.get("url", "")
        if not url.startswith("http"):
            url = BASE_URL + url
        pending.append((mid, url, b))
    if limit:
        pending = pending[:limit]

    log.info("persistent-session mode: %d pending, ~%.0f min estimated (at 1/s)",
             len(pending), len(pending) / 60)
    if not pending:
        log.info("Nothing to do.")
        return

    stopper = Stopper()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: setattr(stopper, "stop", True))
        except NotImplementedError:
            pass

    failed: List[Dict] = []
    if failed_file.exists() and failed_file.stat().st_size > 2:
        try:
            failed = [f for f in json.loads(failed_file.read_text())
                      if f.get("medexId") not in existing]
        except Exception:
            failed = []

    def save():
        details_file.write_text(json.dumps(list(existing.values()),
                                           ensure_ascii=False, indent=1))
        failed_file.write_text(json.dumps(failed, ensure_ascii=False))

    session = AsyncStealthySession(headless=headless, solve_cloudflare=True,
                                   humanize=True, os_randomize=True,
                                   disable_resources=True, timeout=60_000)
    await session.start()
    t0 = time.time()
    stats = {"scraped": 0, "empty": 0, "errors": 0}
    try:
        for idx, (mid, url, seed) in enumerate(pending, 1):
            if stopper.stop:
                log.info("stop requested — saving and exiting")
                break
            try:
                resp = await session.fetch(url)
                html = resp.html_content if hasattr(resp, "html_content") else resp.body
                if isinstance(html, bytes):
                    html = html.decode("utf-8", "ignore")
            except Exception as exc:
                stats["errors"] += 1
                log.warning("fetch error id=%s: %s", mid, exc)
                await asyncio.sleep(2.0)
                continue
            if not html or is_blocked(html):
                # solver gave up on this one; record and keep going
                stats["errors"] += 1
                failed.append({"medexId": mid, "reason": "blocked", "url": url})
                await asyncio.sleep(delay * 4)
                continue
            detail = parse_brand_detail(html)
            if not detail or not any(detail.get(f) for f in SECTION_MAP.values()):
                stats["empty"] += 1
                failed.append({"medexId": mid, "reason": "empty", "url": url})
            else:
                row = dict(seed)
                row.update(detail)
                row["url"] = url
                existing[mid] = row
                stats["scraped"] += 1
            if idx % save_every == 0:
                save()
                rate = idx / (time.time() - t0)
                eta = (len(pending) - idx) / max(rate, 0.01) / 60
                log.info("  %d/%d (%.2f/s, ETA %.0fh) scraped=%d empty=%d err=%d",
                         idx, len(pending), rate, eta,
                         stats["scraped"], stats["empty"], stats["errors"])
            await asyncio.sleep(delay + random.uniform(0, delay * 0.5))
    finally:
        save()
        try:
            await session.close()
        except Exception:
            pass
    log.info("DONE: %d scraped / %d empty / %d err in %.0fs — total now %d",
             stats["scraped"], stats["empty"], stats["errors"],
             time.time() - t0, len(existing))


async def run_details(brands_file: Path, details_file: Path, failed_file: Path,
                      workers: int = 8, limit: Optional[int] = None,
                      polite_delay: float = 0.12, save_every: int = 100):
    """Resumable detail scrape: fills details_file, tracks failures, saves as it goes."""
    brands = json.loads(brands_file.read_text())
    existing: Dict[int, Dict] = {}
    if details_file.exists() and details_file.stat().st_size > 2:
        for item in json.loads(details_file.read_text()):
            existing[item["medexId"]] = item

    failed: List[Dict] = []
    if failed_file.exists() and failed_file.stat().st_size > 2:
        failed = json.loads(failed_file.read_text())
    prev_failed_ids = {f["medexId"] for f in failed if f.get("medexId")}

    pending: List[Tuple[int, str, Dict]] = []
    for b in brands:
        mid = b.get("medexId")
        if not mid or mid in existing:
            continue
        url = b.get("url", "")
        if not url.startswith("http"):
            url = BASE_URL + url
        pending.append((mid, url, b))
    if limit:
        pending = pending[:limit]

    total, done0 = len(brands), len(existing)
    log.info("brands=%d done=%d pending=%d workers=%d", total, done0, len(pending), workers)
    if not pending:
        log.info("Nothing to do — all brands have detail rows.")
        return

    sem = asyncio.Semaphore(workers)
    stopper = Stopper()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: setattr(stopper, "stop", True))
        except NotImplementedError:
            pass

    stats = {"scraped": 0, "empty": 0, "blocked": 0, "http_err": 0,
             "stealth_saved": 0, "t0": time.time()}
    write_lock = asyncio.Lock()

    def save_progress():
        merged = list(existing.values())
        details_file.write_text(json.dumps(merged, ensure_ascii=False, indent=1))
        # a retried id that now has a detail row drops off the failed list
        still_failed = [f for f in failed if f.get("medexId") not in existing]
        failed_file.write_text(json.dumps(still_failed, ensure_ascii=False))

    async with __import__("httpx").AsyncClient(http2=True, follow_redirects=True,
                                               headers={"User-Agent": UA,
                                                        "Accept": "text/html,application/xhtml+xml",
                                                        "Accept-Language": "en-US,en;q=0.5",
                                                        "Referer": BRANDS_URL}) as client:
        async def one(mid: int, url: str, seed: Dict):
            if stopper.stop:
                return
            async with sem:
                if stopper.stop:
                    return
                html, status = await _fetch_httpx(client, url)
                await asyncio.sleep(polite_delay + random.uniform(0, polite_delay))
                if html is None:
                    if status in (403, 429, 503, 0):
                        # captcha/throttle territory — escalate to stealth (sync
                        # browser work in a thread so the loop stays responsive)
                        html = await asyncio.to_thread(_fetch_stealth, url)
                        if html and not is_blocked(html):
                            stats["stealth_saved"] += 1
                    if html is None:
                        stats["http_err"] += 1
                        async with write_lock:
                            failed.append({"medexId": mid, "reason": f"http-{status or 'net'}",
                                           "url": url})
                        return
                if is_blocked(html):
                    html = await asyncio.to_thread(_fetch_stealth, url)
                    if html is None or is_blocked(html):
                        stats["blocked"] += 1
                        async with write_lock:
                            failed.append({"medexId": mid, "reason": "blocked", "url": url})
                        return
                    stats["stealth_saved"] += 1

                detail = parse_brand_detail(html)
                if not detail or not any(detail.get(f) for f in SECTION_MAP.values()):
                    stats["empty"] += 1
                    async with write_lock:
                        failed.append({"medexId": mid, "reason": "empty", "url": url})
                    return
                row = dict(seed)
                row.update(detail)
                row["url"] = url
                async with write_lock:
                    existing[mid] = row
                    stats["scraped"] += 1
                    n = stats["scraped"]
                    if n % save_every == 0:
                        save_progress()
                        rate = n / (time.time() - stats["t0"])
                        eta = (len(pending) - n) / rate if rate else 0
                        log.info("  %d/%d scraped (%.1f/s, ETA %.0fm) | empty=%d blocked=%d stealth=%d",
                                 n, len(pending), rate, eta / 60,
                                 stats["empty"], stats["blocked"], stats["stealth_saved"])

        await asyncio.gather(*(one(*p) for p in pending))

    save_progress()
    elapsed = time.time() - stats["t0"]
    log.info("DONE: +%d scraped in %.0fs (total %d/%d). empty=%d blocked=%d http_err=%d stealth-saved=%d",
             stats["scraped"], elapsed, len(existing), total,
             stats["empty"], stats["blocked"], stats["http_err"], stats["stealth_saved"])


# ---------------------------------------------------------------------------
# List pages (port of their run(), simplified to EN; --lang bn for Bangla)
# ---------------------------------------------------------------------------

async def run_lists(lang: str = "en"):
    import httpx
    from bs4 import BeautifulSoup
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_bn" if lang == "bn" else ""
    async with httpx.AsyncClient(http2=True, follow_redirects=True,
                                 headers={"User-Agent": UA}, timeout=30) as client:
        async def paginated(list_url: str, out_name: str, kind: str):
            items, page = [], 1
            while True:
                u = f"{list_url}?page={page}" + ("/bn" if lang == "bn" else "")
                r = await client.get(u)
                if r.status_code != 200:
                    break
                soup = BeautifulSoup(r.text, "html.parser")
                if kind == "brands":
                    cards = soup.select("div.brand-list-grid a.brand-card")
                    for c in cards:
                        href = c.get("href", "")
                        row = parse_brand_card(href, c)
                        if row["medexId"]:
                            items.append(row)
                else:
                    cards = soup.select("a.hoverable-block.darker")
                    for c in cards:
                        href = c.get("href", "")
                        m = re.search(r"/(\d+)/", href)
                        el = c.select_one(".data-row-top.dcind-title") or c.select_one("a")
                        if m:
                            items.append({"medexId": int(m.group(1)), "url": href,
                                          "name": (el.get_text(strip=True) if el else "")})
                if not cards:
                    break
                if not soup.select_one("a.page-link[rel='next']"):
                    break
                page += 1
                await asyncio.sleep(0.25)
            path = OUT_DIR / f"{out_name}{suffix}.json"
            path.write_text(json.dumps(items, ensure_ascii=False, indent=1))
            log.info("%s%s: %d items → %s", out_name, suffix, len(items), path)

        await paginated(BRANDS_URL, "brands", "brands")
        await paginated(GENERICS_URL, "generics", "generics")
        await paginated(COMPANIES_URL, "pharmaceuticals", "companies")


def run_status(brands_file: Path, details_file: Path, failed_file: Path):
    brands = json.loads(brands_file.read_text()) if brands_file.exists() else []
    det = json.loads(details_file.read_text()) if details_file.exists() else []
    fl = json.loads(failed_file.read_text()) if failed_file.exists() else []
    ids = {b["medexId"] for b in brands}
    done = {d["medexId"] for d in det}
    print(json.dumps({
        "brands_total": len(ids), "details_done": len(done),
        "pending": len(ids - done), "failed_tracked": len(fl),
        "field_fill": {f: sum(1 for d in det if d.get(f)) for f in
                       ["indications", "pharmacology", "dosage", "overdoseEffects"]},
    }, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="MedEx.com.bd scraper (merged, async, resumable)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    lp = sub.add_parser("lists", help="scrape brand/generic/company index pages")
    lp.add_argument("--lang", default="en", choices=["en", "bn"])
    dp = sub.add_parser("details", help="fill brand detail pages (resumable)")
    dp.add_argument("--mode", choices=["http", "session"], default="session",
                    help="session = 1 persistent Camoufox+Turnstile solver (works on medex); "
                         "http = async httpx/Scrapling-escalation (fast, for open sites)")
    dp.add_argument("--workers", type=int, default=8)
    dp.add_argument("--delay", type=float, default=0.4, help="session mode: polite gap (s)")
    dp.add_argument("--headful", action="store_true", help="watch the browser solve Turnstile")
    dp.add_argument("--limit", type=int, default=None, help="pilot: first N pending")
    dp.add_argument("--brands-file", type=Path, default=OUT_DIR / "brands.json")
    dp.add_argument("--details-file", type=Path, default=OUT_DIR / "brand_details.json")
    dp.add_argument("--failed-file", type=Path, default=OUT_DIR / "brand_details_failed.json")
    args = ap.parse_args()

    if args.cmd == "lists":
        asyncio.run(run_lists(args.lang))
    elif args.cmd == "details":
        if not args.brands_file.exists():
            sys.exit(f"no brands file at {args.brands_file} — run 'lists' first "
                     f"or pass --brands-file (e.g. the one in ~/Downloads/scraper/output)")
        if args.mode == "session":
            asyncio.run(run_details_persistent(
                args.brands_file, args.details_file, args.failed_file,
                limit=args.limit, delay=args.delay, headless=not args.headful))
        else:
            asyncio.run(run_details(args.brands_file, args.details_file, args.failed_file,
                                    workers=args.workers, limit=args.limit))
    elif args.cmd == "status":
        run_status(OUT_DIR / "brands.json", OUT_DIR / "brand_details.json",
                   OUT_DIR / "brand_details_failed.json")
    else:
        ap.print_help()
