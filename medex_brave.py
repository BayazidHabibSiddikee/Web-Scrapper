#!/usr/bin/env python3
"""MedEx scraper using Brave browser (fresh IP)"""
import json, sys, time, random, logging, asyncio, os
from pathlib import Path
sys.path.insert(0, '.')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("medex_brave")

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
    return {"done": [], "failed": []}

def save_state(state):
    state["last_save"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_FILE, "w") as f:
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

async def fetch_with_brave(url):
    """Fetch using Playwright with Brave browser profile"""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            # Use Brave browser if available, otherwise Chromium
            browser_type = p.brave if hasattr(p, 'brave') else p.chromium
            browser = await browser_type.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Brave/126"
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            html = await page.content()
            await browser.close()
            return html
    except Exception as e:
        log.debug(f"Brave fetch error: {e}")
        return None

def main():
    log.info("Loading data...")
    brands = json.load(open(BRANDS_FILE))
    state = load_state()
    
    done_ids = set(state.get("done", []))
    failed_ids = set(state.get("failed", []))
    brand_map = {b["medexId"]: b for b in brands}
    
    existing = {}
    if Path(DETAILS_FILE).exists():
        for r in json.load(open(DETAILS_FILE)):
            if r.get("medexId"):
                existing[r["medexId"]] = r
    
    t0 = time.time()
    attempts = 0
    
    log.info(f"Starting: {len(done_ids)} done, {len(failed_ids)} failed, using Brave browser")
    
    while len(done_ids) < len(brands):
        attempts += 1
        
        # Get next brand (prioritize failed)
        mid, url = None, None
        for fid in list(failed_ids):
            if fid in brand_map:
                mid, url = fid, brand_map[fid].get("url", "")
                break
        if not mid:
            for b in brands:
                mid = b.get("medexId")
                if mid and mid not in done_ids and mid not in failed_ids:
                    url = b.get("url", "")
                    break
        
        if not mid:
            log.info("All done!")
            break
        
        if not url.startswith("http"):
            url = f"https://medex.com.bd{url}"
        
        log.info(f"[Attempt {attempts}] {mid}")
        
        html = asyncio.run(fetch_with_brave(url))
        
        if not html or "captcha-challenge" in html.lower()[:2000]:
            wait = min(30 * (attempts % 10 + 1), 300)
            log.warning(f"  Blocked! Waiting {wait}s...")
            time.sleep(wait)
            continue
        
        d = parse_html(html, url)
        if d:
            d["medexId"] = mid
            d["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            existing[mid] = d
            done_ids.add(mid)
            failed_ids.discard(mid)
            log.info(f"  ✓ Saved: {d.get('brandName','?')}")
        else:
            failed_ids.add(mid)
        
        # Save every 25
        if len(done_ids) % 25 == 0:
            with open(DETAILS_FILE, "w") as f:
                json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
            save_state({"done": list(done_ids), "failed": list(failed_ids)})
            elapsed = time.time() - t0
            rate = len(done_ids) / max(elapsed, 1)
            remaining = len(brands) - len(done_ids)
            eta = remaining / max(rate, 1) / 3600
            log.info(f"Progress: {len(done_ids)}/{len(brands)} | {rate*3600:.0f}/hr | ETA: {eta:.1f}h")
        
        time.sleep(random.uniform(2, 4))
    
    with open(DETAILS_FILE, "w") as f:
        json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
    save_state({"done": list(done_ids), "failed": list(failed_ids)})
    log.info(f"DONE: {len(done_ids)} brands in {(time.time()-t0)/3600:.1f}h")

if __name__ == "__main__":
    main()
