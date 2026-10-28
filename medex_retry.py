#!/usr/bin/env python3
"""MedEx scraper that retries every 5 min until ban expires"""
import json, sys, time, random, logging, asyncio
from pathlib import Path
sys.path.insert(0, '.')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("medex")

BRANDS = "/home/sword/Downloads/scraper/output/brands.json"
DETAILS = "/home/sword/Downloads/scraper/output/brand_details.json"
STATE = "/home/sword/Downloads/scraper/output/scrape_state.json"

SMAP = {"indications":"indications","mode_of_action":"pharmacology",
    "dosage":"dosage","interaction":"interaction","contraindications":"contraindications",
    "side_effects":"sideEffects","pregnancy_cat":"pregnancyCategory",
    "precautions":"precautions","storage_conditions":"storageConditions",
    "overdose_effects":"overdoseEffects","drug_classes":"therapeuticClass"}

def load_state():
    if Path(STATE).exists():
        return json.load(open(STATE))
    return {"done":[], "failed":[]}

def save(s):
    s["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE, "w") as f:
        json.dump(s, f)

def parse(html, url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    d = {"url": url}
    t = soup.find("title")
    if t:
        n = t.get_text(strip=True)
        if n and "Security" not in n and "Captcha" not in n:
            d["brandName"] = n
    e = soup.select_one('[title="Strength"]')
    if e:
        d["strength"] = e.get_text(strip=True)
    c = soup.select_one(".generic-data-container")
    if c:
        for sid, fn in SMAP.items():
            h = c.find("div", id=sid)
            if h:
                b = h.find_next_sibling("div", class_="ac-body")
                if b:
                    txt = b.get_text(strip=True)
                    if txt:
                        d[fn] = txt
    return d if any(d.get(k) for k in ["brandName","strength","indications"]) else None

async def fetch(url):
    try:
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(headless=True) as b:
            ctx = await b.new_context()
            p = await ctx.new_page()
            await p.goto(url, wait_until="domcontentloaded", timeout=30000)
            await p.wait_for_timeout(2000)
            html = await p.content()
            return html
    except:
        return None

def main():
    log.info("Starting persistent MedEx scraper...")
    brands = json.load(open(BRANDS))
    st = load_state()
    done = set(st.get("done",[]))
    failed = set(st.get("failed",[]))
    
    bmap = {b["medexId"]:b for b in brands}
    existing = {}
    if Path(DETAILS).exists():
        for r in json.load(open(DETAILS)):
            if r.get("medexId"):
                existing[r["medexId"]] = r
    
    t0 = time.time()
    attempt = 0
    
    while len(done) < len(brands):
        attempt += 1
        
        mid, url = None, None
        for fid in list(failed):
            if fid in bmap:
                mid, url = fid, bmap[fid].get("url","")
                break
        if not mid:
            for b in brands:
                mid = b.get("medexId")
                if mid and mid not in done and mid not in failed:
                    url = b.get("url","")
                    break
        
        if not mid:
            log.info("All done!")
            break
        
        if not url.startswith("http"):
            url = f"https://medex.com.bd{url}"
        
        log.info(f"[{attempt}] Scraping {mid}...")
        html = asyncio.run(fetch(url))
        
        if not html or "captcha-challenge" in html.lower()[:2000]:
            wait = 300
            log.warning(f"  Blocked! Waiting {wait}s...")
            time.sleep(wait)
            continue
        
        d = parse(html, url)
        if d:
            d["medexId"] = mid
            d["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            existing[mid] = d
            done.add(mid)
            failed.discard(mid)
            log.info(f"  OK {d.get('brandName','')}")
        else:
            failed.add(mid)
        
        if len(done) % 25 == 0:
            with open(DETAILS, "w") as f:
                json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
            save({"done":list(done), "failed":list(failed)})
            elapsed = time.time() - t0
            rate = len(done)/max(elapsed,1)
            eta = (len(brands)-len(done))/max(rate,1)/3600
            log.info(f"{len(done)}/{len(brands)} | {rate*3600:.0f}/hr | ETA:{eta:.1f}h")
        
        time.sleep(random.uniform(2,3))
    
    with open(DETAILS, "w") as f:
        json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)
    log.info(f"DONE: {len(done)}/{len(brands)} brands")

if __name__ == "__main__":
    main()
