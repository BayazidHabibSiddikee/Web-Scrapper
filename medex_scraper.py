#!/usr/bin/env python3
"""
medex_scraper.py v2 — simple persistent-session with per-request proxy rotation.
Replaces the old version. Uses scrapling.AsyncStealthySession with a rotating
proxy from config/proxies.txt — one clean browser process, new IP every N pages.
"""
import asyncio, json, logging, os, random, re, signal, sys, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("medex")

BASE_URL = "https://medex.com.bd"
OUT_DIR = Path(__file__).parent / "output" / "medex"
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
    if m: out["packSize"], out["packPrice"] = f"{m.group(1)} x {m.group(2)}", m.group(3)
    return out

def is_blocked(html: str) -> bool:
    h = (html or "").lower()
    return any(m.lower() in h[:5000] for m in CAPTCHA_MARKERS)

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

# ---------------------------------------------------------------------------
# Proxy loader
# ---------------------------------------------------------------------------
def load_proxies(path: str = "config/proxies.txt") -> List[str]:
    proxs: List[str] = []
    if not path or not os.path.isfile(path):
        return proxs
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Normalize to http:// style even if user wrote socks5:// etc
        if not line.lower().startswith(("http://", "socks4://", "socks5://")):
            line = "http://" + line
        proxs.append(line)
    return proxs

# ---------------------------------------------------------------------------
# Core run function
# ---------------------------------------------------------------------------
async def run_brands(brands_path: Path, details_path: Path, failed_path: Path,
                     limit: Optional[int] = None, delay: float = 0.3,
                     headless: bool = True, use_proxy: bool = True,
                     rotate_every: int = 30):
    brands = json.loads(brands_path.read_text())
    existing: Dict[int, Dict] = {}
    if details_path.exists() and details_path.stat().st_size > 2:
        for item in json.loads(details_path.read_text()):
            existing[item["medexId"]] = item

    pending: List[Tuple[int, str]] = []
    for b in brands:
        mid = b.get("medexId")
        if not mid or mid in existing:
            continue
        url = b.get("url", "") or (BASE_URL + urlparse(b.get("url","")).path.lstrip("/"))
        if not url.startswith("http"):
            url = BASE_URL + "/" + url.lstrip("/")
        pending.append((mid, url))
    if limit:
        pending = pending[:limit]

    proxies = load_proxies("config/proxies.txt") if use_proxy else []
    if proxies:
        log.info("loaded %d proxies from config/proxies.txt", len(proxies))
    else:
        log.warning("no proxies loaded — will run without rotation (may hit throttle)")

    # Load previous failures
    failed: List[Dict] = []
    if failed_path.exists():
        try:
            for f in json.loads(failed_path.read_text()):
                if f.get("medexId") not in existing:
                    failed.append(f)
        except Exception:
            pass

    def save():
        details_path.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=1))
        still = [f for f in failed if f.get("medexId") not in existing]
        failed_path.write_text(json.dumps(still, ensure_ascii=False))

    from scrapling.fetchers import AsyncStealthySession
    t0 = time.time()
    stats = {"scraped": 0, "empty": 0, "blocked": 0, "errors": 0, "proxies_used": 0}
    stop = False

    def handle_sig(): nonlocal stop; stop = True
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_sig)
        except NotImplementedError:
            pass

    async def one_page(mid: int, url: str, proxy_url: Optional[str]):
        """Fetch one page via fresh session w/ given proxy. Returns (html|None, meta)."""
        kw = dict(headless=headless, solve_cloudflare=True, humanize=True,
                  os_randomize=True, disable_resources=True, timeout=60_000)
        if proxy_url:
            kw["proxy"] = proxy_url
        sess = AsyncStealthySession(**kw)
        await sess.start()
        html: Optional[str] = None
        try:
            r = await sess.fetch(url)
            body = getattr(r, "body", None) or getattr(r, "html_content", None) or b""
            if isinstance(body, bytes):
                body = body.decode("utf-8", "ignore")
            html = body
        except Exception as exc:
            log.warning("fetch error id=%d: %s", mid, exc)
            stats["errors"] += 1
        finally:
            try: await sess.close()
            except Exception: pass
        return html

    n = len(pending)
    log.info("started: %d pending | %d existing | proxies=%d rotate=%d", n, len(existing), len(proxies), rotate_every)
    idx = 0
    last_save = 0

    while pending:
        if stop:
            break
        mid, url = pending.pop(0)
        # Pick a proxy round-robin
        purl = proxies[idx % len(proxies)] if proxies else None
        if proxies:
            stats["proxies_used"] += 1
        idx += 1

        html = await one_page(mid, url, purl)
        if not html or is_blocked(html):
            stats["blocked"] += 1
            failed.append({"medexId": mid, "reason": "blocked", "url": url})
            await asyncio.sleep(delay * 2)
            continue
        detail = parse_detail(html)
        if not detail or not any(detail.get(f) for f in SECTION_MAP.values()):
            stats["empty"] += 1
            failed.append({"medexId": mid, "reason": "empty", "url": url})
        else:
            row = {"medexId": mid, "medexSlug": urlparse(url).path.rsplit("/",1)[-1] or "",
                   "url": url}
            row.update(detail)
            existing[mid] = row
            stats["scraped"] += 1
        # Save every rotate_every or when pool is empty
        if (idx - last_save) >= max(rotate_every, 10) or not pending:
            save()
            last_save = idx
            rate = stats["scraped"] / max(1, time.time() - t0)
            eta = (n - idx) / max(rate, 0.01) / 3600 if rate else 999
            log.info("progress: %d/%d done | scraped=%d empty=%d blocked=%d err=%d | proxy=%s | %.2f/s ETA %.1fh",
                     idx, n, stats["scraped"], stats["empty"], stats["blocked"],
                     stats["errors"], purl.split("@")[-1][-20:] if purl else "DIRECT",
                     rate, eta)
        await asyncio.sleep(delay + random.uniform(0, delay * 0.4))

    save()
    elapsed = time.time() - t0
    log.info("DONE total: %d rows | %d scraped | %d empty | %d blocked | %d errors | %.0fs (%.2f/s)",
             len(existing), stats["scraped"], stats["empty"], stats["blocked"],
             stats["errors"], elapsed, stats["scraped"] / max(elapsed, 1))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="MedEx brand detail scraper v2 (proxy-rotating)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dp = sub.add_parser("details")
    dp.add_argument("--brands-file", default="/home/sword/Downloads/scraper/output/brands.json", type=Path)
    dp.add_argument("--details-file", default="/home/sword/Downloads/scraper/output/brand_details.json", type=Path)
    dp.add_argument("--failed-file", default="/home/sword/Downloads/scraper/output/brand_details_failed.json", type=Path)
    dp.add_argument("--limit", type=int, default=None)
    dp.add_argument("--delay", type=float, default=0.35)
    dp.add_argument("--headful", action="store_true")
    dp.add_argument("--no-proxy", action="store_true", help="skip proxy rotation")
    dp.add_argument("--rotate-every", type=int, default=30,
                    help="switch proxy every N requests (default 30, lower=faster rotation)")
    args = ap.parse_args()

    if not args.brands_file.exists():
        sys.exit(f"brands file missing: {args.brands_file}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(run_brands(
        args.brands_file, args.details_file, args.failed_file,
        limit=args.limit, delay=args.delay, headless=not args.headful,
        use_proxy=not args.no_proxy, rotate_every=args.rotate_every,
    ))
