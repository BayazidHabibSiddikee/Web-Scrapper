#!/usr/bin/env python3
"""Persistent MedEx scraper - runs forever until all brands done"""
import json, sys, time, random, logging, asyncio, os, signal
from pathlib import Path
sys.path.insert(0, '.')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("medex")

BRANDS_FILE = "/home/sword/Downloads/scraper/output/brands.json"
DETAILS_FILE = "/home/sword/Downloads/scraper/output/brand_details.json"
STATE_FILE = "/home/sword/Downloads/scraper/output/scrape_state.json"

SECTION_MAP = {"indications":"indications","mode_of_action":"pharmacology",
    "dosage":"dosage","interaction":"interaction","contraindications":"contraindications",
    "side_effects":"sideEffects","pregnancy_cat":"pregnancyCategory",
    "precautions":"precautions","storage_conditions":"storageConditions",
    "overdose_effects":"overdoseEffects","drug_classes":"therapeuticClass"}

def load_state():
    if Path(STATE_FILE).exists():
        return json.load(open(STATE_FILE))
    return {"done": [], "failed": [], "total_attempts": 0}

def save_state(state):
    state["last_save"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def parse_html(html, url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    d = {"url": url}
    t = soup.find("title")
    if t and "Security" not in t.get_text() and "Captcha" not in t.get_text():
        d["brandName"] = t.get_text(strip=True)
    el = soup.select_one('[title="Strength"]')
    if el: d["strength"] = el.get_text(strip=True)
    c = soup.select_one(".generic-data-container")
    if c:
        for sid, fn in SECTION_MAP.items():
            h = c.find("div", id=sid)
            if h:
                b = h.find_next_sibling("div", class_="ac-body")
                if b:
                    txt = b.get_text(separator="\n", strip=True)
                    if txt: d[fn] = txt
    return d if any(d.get(k) for k in ["brandName","strength","indications"]) else None

async def fetch_page(url):
    try:
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(headless=True) as browser:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            html = await page.content()
            return html
    except Exception as e:
        return None

def main():
    log.info("="*60)
    log.info("MedEx Persistent Scraper - Will run until complete")
    log.info("="*60)
    
    brands = json.load(open(BRANDS_FILE))
    state = load_state()
    
    done_ids = set(state.get("done", []))
    failed_ids = set(state.get("failed", []))
    
    # Build brand lookup
    brand_map = {b["medexId"]: b for b in brands}
    
    # Load existing data
    existing = {}
    if Path(DETAILS_FILE).exists():
        for r in json.load(open(DETAILS_FILE)):
            if r.get("medexId"):
                existing[r["medexId"]] = r
    
    t0 = time.time()
    attempts = 0
    consecutive_captcha = 0
    last_save = 0
    
    log.info(f"Starting: {len(done_ids)} done, {len(failed_ids)} failed")
    log.info("Will retry failed items indefinitely until ban expires")
    
    while len(done_ids) < len(brands):
        attempts += 1
        
        # Get next pending brand (prioritize failed ones)
        mid = None
        url = None
        
        # Try failed first
        for fid in list(failed_ids):
            if fid in brand_map:
                mid = fid
                url = brand_map[fid].get("url", "")
                if not url.startswith("http"):
                    url = f"https://medex.com.bd{url}"
                break
        
        # If no failed, get next pending
        if not mid:
            for b in brands:
                mid = b.get("medexId")
                if mid and mid not in done_ids and mid not in failed_ids:
                    url = b.get("url", "")
                    if not url.startswith("http"):
                        url = f"https://medex.com.bd{url}"
                    break
        
        if not mid:
            log.info("All brands processed!")
            break
        
        log.info(f"[Attempt {attempts}] Scraping {mid}...")
        
        html = asyncio.run(fetch_page(url))
        
        if not html or "captcha-challenge" in html.lower()[:2000]:
            consecutive_captcha += 1
            wait = min(30 * consecutive_captcha, 600)  # Max 10 min wait
            log.warning(f"  Captcha/Blocked! Waiting {wait}s (attempt {consecutive_captcha})...")
            time.sleep(wait)
            continue
        
        consecutive_captcha = 0
        
        d = parse_html(html, url)
        if d:
            d["medexId"] = mid
            d["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            existing[mid] = d
            done_ids.add(mid)
            failed_ids.discard(mid)
            log.info(f"  ✓ Saved {mid}: {d.get('brandName','?')}")
        else:
            log.warning(f"  Parse failed for {mid}")
            failed_ids.add(mid)
        
        # Save every 25 brands
        if len(done_ids) - last_save >= 25:
            with open(DETAILS_FILE, "w") as f:
                json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
            save_state({"done": list(done_ids), "failed": list(failed_ids)})
            last_save = len(done_ids)
            elapsed = time.time() - t0
            rate = len(done_ids) / max(elapsed, 1)
            remaining = len(brands) - len(done_ids)
            eta = remaining / max(rate, 1) / 3600
            log.info(f"Progress: {len(done_ids)}/{len(brands)} | {rate*3600:.0f}/hr | ETA: {eta:.1f}h")
        
        # Polite delay
        time.sleep(random.uniform(2, 4))
    
    # Final save
    with open(DETAILS_FILE, "w") as f:
        json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
    save_state({"done": list(done_ids), "failed": list(failed_ids)})
    
    elapsed = time.time() - t0
    log.info(f"\n{'='*60}")
    log.info(f"COMPLETED! {len(done_ids)}/{len(brands)} brands in {elapsed/3600:.1f} hours")
    log.info(f"{'='*60}")

if __name__ == "__main__":
    main()
