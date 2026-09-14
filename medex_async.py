#!/usr/bin/env python3
"""MedEx Async Scraper - Resumable with captcha handling and auto-backoff"""
import json, sys, time, random, logging, asyncio
from pathlib import Path
sys.path.insert(0, '.')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("medex")

BRANDS_FILE = "/home/sword/Downloads/scraper/output/brands.json"
DETAILS_FILE = "/home/sword/Downloads/scraper/output/brand_details.json"
FAILED_FILE = "/home/sword/Downloads/scraper/output/brand_details_failed.json"
CHECKPOINT = "/home/sword/Downloads/scraper/output/checkpoint.json"
SAVE_EVERY = 20
DELAY_MIN, DELAY_MAX = 2, 4

SECTION_MAP = {
    "indications": "indications", "mode_of_action": "pharmacology",
    "dosage": "dosage", "interaction": "interaction",
    "contraindications": "contraindications", "side_effects": "sideEffects",
    "pregnancy_cat": "pregnancyCategory", "precautions": "precautions",
    "storage_conditions": "storageConditions", "overdose_effects": "overdoseEffects",
    "drug_classes": "therapeuticClass",
}

def load_checkpoint():
    if Path(CHECKPOINT).exists():
        return json.load(open(CHECKPOINT))
    return {"done": [], "failed": []}

def save_checkpoint(state):
    state["last_save"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(CHECKPOINT, "w") as f:
        json.dump(state, f)

def parse_html(html, url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    d = {"url": url}
    t = soup.find("title")
    if t and "Security" not in t.get_text() and "Captcha" not in t.get_text():
        d["brandName"] = t.get_text(strip=True)
    el = soup.select_one('[title="Strength"]')
    if el: d["strength"] = el.get_text(strip=True)
    p = soup.select_one(".packages-wrapper")
    if p:
        tx = p.get_text(separator=" ")
        import re
        m = re.search(r'Unit Price\s*:\s*[৳$\s]*([\d.]+)', tx)
        if m: d["unitPrice"] = m.group(1)
    c = soup.select_one(".generic-data-container")
    if c:
        for sid, fn in SECTION_MAP.items():
            h = c.find("div", id=sid)
            if h:
                b = h.find_next_sibling("div", class_="ac-body")
                if b:
                    txt = b.get_text(separator="\n", strip=True)
                    if txt: d[fn] = txt
    if not any(d.get(k) for k in ["brandName", "strength", "indications"]):
        return None
    return d

async def fetch_page(url, timeout=30):
    """Fetch page using Camoufox (handles CF challenges)"""
    try:
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(headless=True) as browser:
            ctx = await browser.new_context(locale="en-US")
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout*1000)
            await page.wait_for_timeout(2000)
            html = await page.content()
            return html
    except Exception as e:
        log.debug(f"Fetch error: {e}")
        return None

def main_sync():
    brands = json.load(open(BRANDS_FILE))
    cp = load_checkpoint()
    done_ids = set(cp.get("done", []))
    failed_ids = set(cp.get("failed", []))

    existing = {}
    if Path(DETAILS_FILE).exists():
        for r in json.load(open(DETAILS_FILE)):
            if r.get("medexId"):
                existing[r["medexId"]] = r

    pending = [(b["medexId"], b.get("url","")) for b in brands
               if b.get("medexId") not in done_ids and b.get("medexId") not in failed_ids]

    log.info(f"Starting: {len(pending)} pending, {len(done_ids)} done, {len(failed_ids)} failed")

    if not pending:
        log.info("All done!")
        return

    asyncio.run(run_brands(brands, existing, pending, done_ids, failed_ids))

async def run_brands(brands, existing, pending, done_ids, failed_ids):
    t0 = time.time()
    consecutive_captcha = 0

    for i, (mid, url) in enumerate(pending):
        if not url.startswith("http"):
            url = f"https://medex.com.bd{url}"

        html = await fetch_page(url)

        if not html or "captcha-challenge" in html.lower()[:2000]:
            failed_ids.add(mid)
            consecutive_captcha += 1
            log.warning(f"[{i+1}] Captcha/Error on {mid} (streak: {consecutive_captcha})")

            # Backoff on repeated captchas
            if consecutive_captcha >= 5:
                wait = min(consecutive_captcha * 15, 300)
                log.info(f"  → {wait}s backoff due to captcha streak...")
                await asyncio.sleep(wait)
            else:
                await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            continue

        consecutive_captcha = 0
        d = parse_html(html, url)

        if d:
            d["medexId"] = mid
            d["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            existing[mid] = d
            done_ids.add(mid)
            log.info(f"[{i+1}/{len(pending)}] ✓ {mid}: {d.get('brandName','?')}")
        else:
            failed_ids.add(mid)
            log.warning(f"[{i+1}] Parse failed for {mid}")

        # Save periodically
        if (i+1) % SAVE_EVERY == 0 or i == len(pending)-1:
            with open(DETAILS_FILE, "w") as f:
                json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
            save_checkpoint({"done": list(done_ids), "failed": list(failed_ids)})

            elapsed = time.time() - t0
            rate = len(done_ids) / max(elapsed, 1)
            remaining = len(pending) - i - 1
            eta = remaining / max(rate, 1) / 3600

            log.info(f"Progress: {len(done_ids)}/{len(brands)} | {rate*3600:.0f}/hr | ETA: {eta:.1f}h")

        # Polite delay
        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # Final save
    with open(DETAILS_FILE, "w") as f:
        json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
    save_checkpoint({"done": list(done_ids), "failed": list(failed_ids)})

    elapsed = time.time() - t0
    log.info(f"\n{'='*60}")
    log.info(f"COMPLETED: {len(done_ids)} rows in {elapsed/60:.1f} min ({elapsed/3600:.1f} hr)")
    log.info(f"Failed: {len(failed_ids)}")
    log.info(f"Rate: {len(done_ids)/max(elapsed,1)*3600:.0f} brands/hr")
    log.info(f"{'='*60}")

if __name__ == "__main__":
    main_sync()
