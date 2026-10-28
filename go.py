#!/usr/bin/env python3
import json,sys,time,random,logging,asyncio
from pathlib import Path
sys.path.insert(0,'.')
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s")
log=logging.getLogger("go")
B="/home/sword/Downloads/scraper/output/brands.json"
D="/home/sword/Downloads/scraper/output/brand_details.json"
S="/home/sword/Downloads/scraper/output/scrape_state.json"
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
    if t:
        n=t.get_text(strip=True)
        if n and "Security" not in n and "Captcha" not in n: d["brandName"]=n
    e=soup.select_one('[title="Strength"]')
    if e: d["strength"]=e.get_text(strip=True)
    c=soup.select_one(".generic-data-container")
    if c:
        for sid,fn in [("indications","indications"),("dosage","dosage"),("mode_of_action","pharmacology"),("interaction","interaction"),("contraindications","contraindications"),("side_effects","sideEffects"),("pregnancy_cat","pregnancyCategory"),("precautions","precautions"),("storage_conditions","storageConditions"),("overdose_effects","overdoseEffects"),("drug_classes","therapeuticClass")]:
            h2=c.find("div",id=sid)
            if h2:
                b=h2.find_next_sibling("div",class_="ac-body")
                if b:
                    txt=b.get_text(strip=True)
                    if txt: d[fn]=txt
    return d if any(d.get(k) for k in ["brandName","strength","indications"]) else None
async def fetch(u):
    try:
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(headless=True) as b:
            ctx=await b.new_context()
            p=await ctx.new_page()
            await p.goto(u,wait_until="domcontentloaded",timeout=30000)
            await p.wait_for_timeout(random.uniform(1000,2000))
            return await p.content()
    except: return None
brands=json.load(open(B))
st=load()
done=set(st.get("done",[]))
failed=set(st.get("failed",[]))
existing={}
if Path(D).exists():
    for r in json.load(open(D)):
        if r.get("medexId"): existing[r["medexId"]]=r
bmap={b["medexId"]:b for b in brands}
t0=time.time()
att=0
consec=0
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
    html=asyncio.run(fetch(url))
    if not html or "captcha-challenge" in html.lower()[:2000]:
        consec+=1
        w=random.choice([5,10,15,30])
        log.warning(f"[{att}] Blocked #{consec} wait {w}s")
        time.sleep(w)
        continue
    consec=0
    d=parse(html,url)
    if d:
        d["medexId"]=mid
        d["scraped_at"]=time.strftime("%Y-%m-%d %H:%M:%S")
        existing[mid]=d
        done.add(mid)
        failed.discard(mid)
        log.info(f"[{att}] OK {mid}: {d.get('brandName','')}")
    else: failed.add(mid)
    if len(done)%10==0:
        with open(D,"w") as f: json.dump(list(existing.values()),f,ensure_ascii=False,indent=2)
        save({"done":list(done),"failed":list(failed)})
        el=time.time()-t0
        rate=len(done)/max(el,1)
        eta=(len(brands)-len(done))/max(rate,1)/3600
        log.info(f"{len(done)}/{len(brands)} | {rate*3600:.0f}/hr | ETA:{eta:.1f}h")
    time.sleep(random.uniform(1,2))
with open(D,"w") as f: json.dump(list(existing.values()),f,ensure_ascii=False,indent=2)
save({"done":list(done),"failed":list(failed)})
log.info(f"DONE: {len(done)}/{len(brands)} in {(time.time()-t0)/60:.1f}min")
