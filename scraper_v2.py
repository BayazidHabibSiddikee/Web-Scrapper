import json, asyncio, time, random, logging, os
from pathlib import Path
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("medex")

B="/home/sword/Downloads/scraper/output/brands.json"
D="/home/sword/Downloads/scraper/output/brand_details.json"
S="/home/sword/Downloads/scraper/output/scrape_state.json"
CK="/tmp/medex_cookies.txt"

def load():
    if Path(S).exists(): return json.load(open(S))
    return {"done":[],"failed":[]}
def save(s):
    with open(S,"w") as f: json.dump(s,f)
def parse(h,u):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(h,"html.parser")
    d={"url":u}
    t=soup.find("title")
    if t and "Security" not in t.text and "Captcha" not in t.text: d["brandName"]=t.text.strip()
    e=soup.select_one('[title="Strength"]')
    if e: d["strength"]=e.text.strip()
    c=soup.select_one(".generic-data-container")
    if c:
        for sid,fn in [("indications","indications"),("dosage","dosage"),("mode_of_action","pharmacology"),("interaction","interaction"),("contraindications","contraindications"),("side_effects","sideEffects"),("pregnancy_cat","pregnancyCategory"),("precautions","precautions"),("storage_conditions","storageConditions"),("overdose_effects","overdoseEffects"),("drug_classes","therapeuticClass")]:
            hh=c.find("div",id=sid)
            if hh:
                b=hh.find_next_sibling("div",class_="ac-body")
                if b and b.text.strip(): d[fn]=b.text.strip()
    return d if any(d.get(k) for k in ["brandName","strength","indications"]) else None

async def fetch(u,session=None):
    """Fetch with session cookies"""
    try:
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=True) as browser:
            ctx = browser.new_context()
            # Load cookies if available
            if os.path.exists(CK):
                try:
                    ctx.add_cookies(json.load(open(CK)))
                except: pass
            p = ctx.new_page()
            p.goto(u,timeout=30000)
            p.wait_for_timeout(3000)
            html = p.content()
            # Save cookies for next request
            cookies = ctx.cookies()
            if cookies:
                with open(CK,"w") as f: json.dump(cookies,f)
            return html
    except Exception as e:
        log.debug(f"Error: {e}")
        return None

def main():
    log.info("Loading...")
    brands = json.load(open(B))
    st = load()
    done=set(st.get("done",[]))
    failed=set(st.get("failed",[]))
    existing={}
    if Path(D).exists():
        for r in json.load(open(D)):
            if r.get("medexId"): existing[r["medexId"]]=r
    bmap={b["medexId"]:b for b in brands}
    t0=time.time()
    att=0
    while len(done)<len(brands):
        att+=1
        mid,url=None,None
        for fid in list(failed):
            if fid in bmap: mid,url=fid,bmap[fid].get("url",""); break
        if not mid:
            for b in brands:
                mid=b.get("medexId")
                if mid and mid not in done and mid not in failed: url=b.get("url",""); break
        if not mid: break
        if not url.startswith("http"): url=f"https://medex.com.bd{url}"
        log.info(f"[{att}] {mid}")
        h=asyncio.run(fetch(url))
        if not h or "captcha-challenge" in h.lower()[:2000]:
            w=random.choice([15,30,60])
            log.warning(f"Blocked wait {w}s")
            time.sleep(w)
            continue
        d=parse(h,url)
        if d:
            d["medexId"]=mid
            existing[mid]=d
            done.add(mid)
            failed.discard(mid)
            log.info(f"OK {mid}")
        else: failed.add(mid)
        if len(done)%10==0:
            with open(D,"w") as f: json.dump(list(existing.values()),f,ensure_ascii=False,indent=2)
            save({"done":list(done),"failed":list(failed)})
            el=time.time()-t0
            rate=len(done)/max(el,1)
            eta=(len(brands)-len(done))/max(rate,1)/3600
            log.info(f"{len(done)}/{len(brands)} {rate*3600:.0f}/hr ETA:{eta:.1f}h")
        time.sleep(random.uniform(1,3))
    with open(D,"w") as f: json.dump(list(existing.values()),f,ensure_ascii=False,indent=2)
    save({"done":list(done),"failed":list(failed)})
    log.info(f"DONE {len(done)}/{len(brands)}")

if __name__=="__main__":
    main()
