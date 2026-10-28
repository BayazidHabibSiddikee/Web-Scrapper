#!/usr/bin/env python3
"""
prescription_scraper.py — collect how prescriptions are written, and under what rules.

Built for the MedEx doctor app: it gathers (a) real prescription artefacts with their
licence metadata, and (b) the authoritative text that defines what a prescription must
contain, so the app's prescription form can be validated against published rules.

Sources (all public, all licence-declared):
  1. Wikimedia Commons  — free-licensed prescription images (search + categories)
  2. Openverse          — CC-licensed images (Flickr/Wikimedia/… aggregation)
  3. Europe PMC         — open-access full text: prescription-writing audits, WHO core
                          prescribing indicators, legibility / medico-legal papers
  4. Reference pages    — Wikipedia + ISMP error-prone abbreviation list

Only openly licensed material is downloaded, and every image is recorded in
`licence_ledger.json` with its licence, creator and source page so the app team can
attribute or filter (e.g. drop non-commercial licences before shipping).

Usage:
    python prescription_scraper.py                 # full run
    python prescription_scraper.py --dry-run       # resolve sources, download nothing
    python prescription_scraper.py --limit 5       # cap per source
    python prescription_scraper.py --only images   # commons+openverse only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from security_utils import assert_public_url

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("prescriptions")

BASE = Path("output/prescriptions")
TEXT_DIR = BASE / "text"
IMG_DIR = BASE / "images"
CAPTION_DIR = BASE / "captions"
LEDGER = BASE / "licence_ledger.json"
CORPUS = BASE / "corpus.jsonl"
MANIFEST = BASE / "manifest.json"

UA = ("MedExPrescriptionResearch/1.0 "
      "(prescription-form design research; contact: dev@medex-app.local)")
DELAY = 1.0            # seconds between requests to the same host (politeness)
MAX_IMG_BYTES = 8_000_000
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
LICENCE_FORBIDDEN_FOR_COMMERCIAL = ("nc", "nd", "noncommercial", "no derivative")

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

# ---------------------------------------------------------------- source config

# search terms are used after the curated categories
COMMONS_SEARCHES = [
    "handwritten prescription",
    "medical prescription",
    "prescription form",
    "prescription drug slip",
    "prescription handwriting",
    "rezept arzt",
]
COMMONS_CATEGORIES = [
    "Category:Medical prescriptions",
    "Category:Medical prescriptions in Germany",
    "Category:Medical recipes",
    "Category:ERecept",
]

# (query, openverse licence_type) — "commercial" excludes NC/ND licences
OPENVERSE_QUERIES = [
    ("handwritten prescription", "commercial"),
    ("doctor prescription note", "commercial"),
    ("medical prescription form", "commercial"),
    ("prescription pad", "commercial"),
    ("prescription handwriting", "all-cc"),
]

COMMONS_IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".tif", ".tiff")
RELEVANCE_RE = re.compile(r"prescri|recept|rezept|\brecipe\b|℞|\brx\b|pharmac", re.IGNORECASE)

# Europe PMC: each entry is (label, query)
EPMC_QUERIES = [
    ("prescription_writing_audit",
     'TITLE:"prescription writing" AND OPEN_ACCESS:y'),
    ("prescription_audit_criteria",
     '("prescription audit" OR "prescribing audit") AND completeness AND OPEN_ACCESS:y'),
    ("who_core_prescribing_indicators",
     '"core prescribing indicators" AND OPEN_ACCESS:y'),
    ("legibility_handwriting",
     '("illegible handwriting" OR "legibility") AND prescription AND OPEN_ACCESS:y'),
    ("prescription_abbreviations",
     '"error-prone abbreviations" AND OPEN_ACCESS:y'),
    ("handwriting_recognition_rx",
     '"handwriting recognition" AND prescription AND OPEN_ACCESS:y'),
    ("medication_error_prescribing",
     '"prescribing errors" AND (preventable OR reduction) AND OPEN_ACCESS:y'),
]

REFERENCE_PAGES = [
    ("wikipedia_medical_prescription",
     "https://en.wikipedia.org/wiki/Medical_prescription"),
    ("wikipedia_rx_abbreviations",
     "https://en.wikipedia.org/wiki/List_of_abbreviations_used_in_medical_prescriptions"),
    ("ismp_error_prone_abbreviations",
     "https://www.ismp.org/recommendations/error-prone-abbreviations-list"),
]

# phrases that mark a normative statement about prescription content
CRITERIA_PATTERNS = [
    r"must (?:be )?(?:include|contain|state|legibly|clearly)",
    r"should (?:be )?(?:include|contain|state|contain the following)",
    r"shall (?:include|contain|be)",
    r"required (?:elements|information|details|fields)",
    r"prescription (?:must|should) (?:contain|include)",
    r"name and (?:address|signature)",
    r"date of (?:issue|prescribing)",
    r"dose,? (?:frequency|route|duration)",
    r"(?:generic|brand) name",
    r"legib",
    r"signature of (?:the )?prescrib",
    r"registration (?:number|no)",
]


# ---------------------------------------------------------------- plumbing

class Fetcher:
    """Polite httpx wrapper: robots-aware, throttled, retrying."""

    def __init__(self, client: httpx.Client, delay: float = DELAY):
        self.client = client
        self.delay = delay
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.stats = {"requests": 0, "bytes": 0, "blocked_by_robots": 0, "errors": 0}

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last.get(host, 0.0)
        wait = self.delay - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def robots_ok(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root in self._robots:
            rp = self._robots[root]
            return True if rp is None else rp.can_fetch(UA, url)
        try:
            resp = self.client.get(root + "/robots.txt", timeout=15.0)
            if resp.status_code == 200 and "text" in resp.headers.get("content-type", ""):
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines())
                self._robots[root] = rp
                return rp.can_fetch(UA, url)
            self._robots[root] = None
        except Exception:
            self._robots[root] = None
        return True

    def get(self, url: str, *, respect_robots: bool = True,
            timeout: float = 30.0) -> httpx.Response | None:
        try:
            url = assert_public_url(url)
        except Exception as exc:
            log.warning("skipping unsafe url %s (%s)", url, exc)
            self.stats["errors"] += 1
            return None
        if respect_robots and not self.robots_ok(url):
            log.info("robots.txt disallows %s", url)
            self.stats["blocked_by_robots"] += 1
            return None
        self._throttle(url)
        for attempt in range(3):
            try:
                resp = self.client.get(url, timeout=timeout)
                self.stats["requests"] += 1
                self.stats["bytes"] += len(resp.content)
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(2 * (attempt + 1))
                    continue
                return resp
            except Exception as exc:
                self.stats["errors"] += 1
                log.debug("request failed %s: %s", url, exc)
                time.sleep(1.5 * (attempt + 1))
        return None


def slugify(text: str, maxlen: int = 60) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(text)).strip("_").lower()
    return text[:maxlen].strip("_") or "item"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t\u00a0]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, plain text). Uses trafilatura when available, bs4 otherwise."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = ""
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    except Exception:
        text = ""
    if not text:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
    return title, clean_text(text)


def save_text(name: str, title: str, url: str, body: str) -> Path:
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    path = TEXT_DIR / f"{slugify(name)}.txt"
    path.write_text(f"# {title}\n# source: {url}\n\n{body}", encoding="utf-8")
    return path


def find_criteria(text: str, max_hits: int = 12) -> list[str]:
    """Pull the normative sentences that define what a prescription must contain."""
    hits: list[str] = []
    for sentence in re.split(r"(?<=[.;:])\s+", text):
        s = clean_text(sentence)
        if not (25 <= len(s) <= 400):
            continue
        if any(re.search(pat, s, re.IGNORECASE) for pat in CRITERIA_PATTERNS):
            if s not in hits:
                hits.append(s)
        if len(hits) >= max_hits:
            break
    return hits


class Ledger:
    """Accumulates corpus records and the image licence ledger."""

    def __init__(self, write: bool = True):
        self.write = write
        self.records: list[dict] = []
        self.images: list[dict] = []

    def add_record(self, **record) -> None:
        record["collected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.records.append(record)

    def add_image(self, **entry) -> None:
        entry.setdefault("collected_at",
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.images.append(entry)

    def flush(self) -> None:
        if not self.write:
            return
        BASE.mkdir(parents=True, exist_ok=True)
        with CORPUS.open("w", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        LEDGER.write_text(json.dumps(self.images, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")


def download_image(fetcher: Fetcher, url: str, dest_dir: Path, stem: str,
                   min_bytes: int = 4000) -> dict | None:
    """Download one image. Returns {file, bytes, sha256, mime} or None.

    A short hash of the source URL is appended to the filename so that two
    different files whose titles slugify to the same string never collide
    (titles are truncated to 70 chars, e.g. 4 similarly-named Commons files).
    """
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in IMG_EXTS:
        ext = ".jpg"
    resp = fetcher.get(url, respect_robots=False, timeout=60.0)
    if resp is None or resp.status_code != 200:
        return None
    data = resp.content
    if len(data) < min_bytes or len(data) > MAX_IMG_BYTES:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    path = dest_dir / f"{slugify(stem, 60)}_{digest}{ext}"
    path.write_bytes(data)
    return {"file": path.name, "bytes": len(data), "sha256": sha256_bytes(data),
            "mime": resp.headers.get("content-type", "")}


def strip_html(value: str) -> str:
    return clean_text(BeautifulSoup(value or "", "lxml").get_text(" "))


def commercial_ok(licence: str) -> bool:
    low = (licence or "").lower()
    return not any(bad in low for bad in LICENCE_FORBIDDEN_FOR_COMMERCIAL)


# ---------------------------------------------------------------- collectors

def commons_api(fetcher: Fetcher, params: str) -> dict:
    resp = fetcher.get(f"{COMMONS_API}?{params}&format=json", respect_robots=False)
    if resp is None or resp.status_code != 200:
        return {}
    try:
        return resp.json()
    except Exception:
        return {}


def commons_file_titles(fetcher: Fetcher) -> list[tuple[str, str]]:
    """Return (File: title, origin) pairs. Categories first, images only."""
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(title: str, origin: str) -> None:
        if not title or title in seen:
            return
        if not title.lower().endswith(COMMONS_IMG_EXT):
            return                      # skip PDFs/DjVu/OGG - search results are full of them
        seen.add(title)
        candidates.append((title, origin))

    for category in COMMONS_CATEGORIES:
        cm = category.replace(" ", "%20").replace(":", "%3A")
        data = commons_api(
            fetcher,
            f"action=query&list=categorymembers&cmtitle={cm}&cmtype=file&cmlimit=100")
        for item in data.get("query", {}).get("categorymembers", []):
            add(item.get("title", ""), category.replace("Category:", ""))

    for query in COMMONS_SEARCHES:
        data = commons_api(
            fetcher,
            f"action=query&list=search&srsearch={query.replace(' ', '%20')}"
            f"&srnamespace=6&srlimit=40")
        for item in data.get("query", {}).get("search", []):
            add(item.get("title", ""), f"search:{query}")

    log.info("commons: %d candidate image files", len(candidates))
    return candidates


def classify_image(title: str, description: str) -> str:
    """Bucket an image by how the prescription was produced - the app's 'types'."""
    blob = f"{title} {description}".lower()
    if re.search(r"hand-?writ|handschrift|scribbl|manuscript|written by hand", blob):
        return "handwritten"
    if re.search(r"e-?recept|elektronisch|electronic|digital|screen|laptop|mobile|"
                 r"software|app|printer|print", blob):
        return "electronic"
    if re.search(r"blank|template|form|pad|stationery|heading", blob):
        return "template"
    if re.search(r"veterinar|dental|eyeglass|optic", blob):
        return "specialty"
    return "photograph"


def collect_commons(fetcher: Fetcher, ledger: Ledger, limit: int, write: bool) -> dict:
    """Download free-licensed prescription images from Wikimedia Commons."""
    candidates = commons_file_titles(fetcher)
    dest = IMG_DIR / "commons"
    saved = skipped = irrelevant = 0
    licences: dict[str, int] = {}
    kinds: dict[str, int] = {}

    for i in range(0, len(candidates), 20):
        if saved >= limit:
            break
        chunk = candidates[i:i + 20]
        encoded = "%7C".join(t.replace(" ", "%20").replace(":", "%3A") for t, _ in chunk)
        origins = {t: o for t, o in chunk}
        data = commons_api(
            fetcher,
            "action=query&prop=imageinfo&iiprop=url|size|mime|extmetadata"
            f"&iiurlwidth=1280&titles={encoded}")
        for page in data.get("query", {}).get("pages", {}).values():
            if saved >= limit:
                break
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            if not info.get("mime", "").startswith("image/"):
                skipped += 1
                continue
            title = page.get("title", f"File:{i}")
            description = strip_html(meta.get("ImageDescription", {}).get("value", ""))
            blob = f"{title} {description}"
            if not RELEVANCE_RE.search(blob):
                irrelevant += 1
                continue          # keep the corpus on-topic
            licence = strip_html(meta.get("LicenseShortName", {}).get("value", ""))
            src = info.get("thumburl") or info.get("url")
            entry = {
                "source": "wikimedia_commons",
                "title": title.replace("File:", ""),
                "description": description[:500],
                "kind": classify_image(title, description),
                "found_via": origins.get(title, ""),
                "licence": licence,
                "creator": strip_html(meta.get("Artist", {}).get("value", ""))[:300],
                "credit": strip_html(meta.get("Credit", {}).get("value", ""))[:300],
                "commercial_use_ok": commercial_ok(licence),
                "source_page": info.get("descriptionurl", ""),
                "original_url": info.get("url"),
                "downloaded_from": src,
                "width": info.get("width"),
                "height": info.get("height"),
                "bytes_remote": info.get("size"),
            }
            stem = f"commons_{slugify(title.replace('File:', ''), 55)}"
            if write:
                got = download_image(fetcher, src, dest, stem)
                if got is None:
                    skipped += 1
                    continue
                entry.update(got)
                entry["path"] = str(dest / got["file"])
            saved += 1
            ledger.add_image(**entry)
            key = licence or "unknown"
            licences[key] = licences.get(key, 0) + 1
            kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1

    log.info("commons: %d images saved (%d skipped, %d off-topic)", saved, skipped, irrelevant)
    return {"images_saved": saved, "skipped": skipped, "filtered_irrelevant": irrelevant,
            "licences": licences, "kinds": kinds}


def collect_openverse(fetcher: Fetcher, ledger: Ledger, limit: int, write: bool) -> dict:
    """Download CC-licensed images from the Openverse API."""
    dest = IMG_DIR / "openverse"
    saved = skipped = 0
    licences: dict[str, int] = {}
    kinds: dict[str, int] = {}
    seen: set[str] = set()

    for query, licence_type in OPENVERSE_QUERIES:
        if saved >= limit:
            break
        resp = fetcher.get(
            f"{OPENVERSE_API}?q={query.replace(' ', '%20')}"
            f"&license_type={licence_type}&page_size=12&mature=false", respect_robots=False)
        if resp is None or resp.status_code != 200:
            log.warning("openverse query failed: %s", query)
            continue
        try:
            results = resp.json().get("results", [])
        except Exception:
            continue
        for item in results:
            if saved >= limit:
                break
            src = item.get("url")
            if not src or src in seen:
                continue
            seen.add(src)
            licence = f"{item.get('license', '')} {item.get('license_version', '')}".strip()
            title = item.get("title") or "image"
            description = item.get("description") or ""
            stem = f"openverse_{slugify(title, 50)}_{len(seen)}"
            entry = {
                "source": "openverse",
                "title": item.get("title"),
                "description": description[:500],
                "kind": classify_image(title, description),
                "found_via": f"search:{query}",
                "licence": licence,
                "creator": item.get("creator"),
                "attribution": item.get("attribution"),
                "commercial_use_ok": commercial_ok(licence),
                "source_page": item.get("foreign_landing_url") or item.get("detail_url"),
                "provider": item.get("provider") or item.get("source"),
                "original_url": src,
                "downloaded_from": src,
                "width": item.get("width"),
                "height": item.get("height"),
            }
            if write:
                got = download_image(fetcher, src, dest, stem)
                if got is None:
                    skipped += 1
                    continue
                entry.update(got)
                entry["path"] = str(dest / got["file"])
            saved += 1
            ledger.add_image(**entry)
            key = licence or "unknown"
            licences[key] = licences.get(key, 0) + 1
            kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1

    log.info("openverse: %d images saved (%d skipped)", saved, skipped)
    return {"images_saved": saved, "skipped": skipped, "licences": licences, "kinds": kinds}


# ------------------------------------------------------- Europe PMC OA text

def epmc_section_text(soup) -> str:
    """Flatten the JATS body into readable text, keeping section headings."""
    parts: list[str] = []
    body = soup.find("body")
    if body is None:
        return ""
    for sec in body.find_all("sec"):
        title = sec.find("title", recursive=False)
        if title:
            parts.append(f"\n## {clean_text(title.get_text(' '))}")
        for para in sec.find_all("p", recursive=False):
            txt = clean_text(para.get_text(" "))
            if txt:
                parts.append(txt)
        for tw in sec.find_all("table-wrap", recursive=False):
            cap = tw.find("caption")
            if cap:
                parts.append("[TABLE] " + clean_text(cap.get_text(" ")))
    return clean_text("\n\n".join(parts))


def epmc_figure_captions(soup) -> list[str]:
    caps = []
    for fig in soup.find_all("fig"):
        label = fig.find("label")
        cap = fig.find("caption")
        if cap:
            text = clean_text(cap.get_text(" "))
            if label:
                text = f"{clean_text(label.get_text(' '))}: {text}"
            caps.append(text)
    return caps


def collect_europepmc(fetcher: Fetcher, ledger: Ledger, per_query: int,
                      write: bool) -> dict:
    """Search open-access literature and pull the full text of each hit."""
    saved = failed = 0
    criteria_total = 0
    sources: list[dict] = []

    for label, query in EPMC_QUERIES:
        resp = fetcher.get(
            f"{EPMC_SEARCH}?query={query.replace(' ', '+').replace('"', '%22')}"
            f"&format=json&pageSize={per_query}&resultType=core", respect_robots=False)
        if resp is None or resp.status_code != 200:
            log.warning("europepmc search failed: %s", label)
            failed += 1
            continue
        try:
            results = resp.json().get("resultList", {}).get("result", [])
        except Exception:
            failed += 1
            continue
        log.info("europepmc[%s]: %d hits", label, len(results))

        for item in results:
            pmcid = item.get("pmcid")
            title = item.get("title", "").rstrip(".")
            if not pmcid:
                continue
            xml_resp = fetcher.get(EPMC_FULLTEXT.format(pmcid=pmcid), respect_robots=False)
            if xml_resp is None or xml_resp.status_code != 200:
                failed += 1
                continue
            soup = BeautifulSoup(xml_resp.text, "xml")
            body_text = epmc_section_text(soup)
            captions = epmc_figure_captions(soup)
            licence = ""
            lic = soup.find("license")
            if lic:
                licence = clean_text(lic.get_text(" "))
            criteria = find_criteria(body_text)

            if write and body_text:
                save_text(f"{pmcid}_{label}", title,
                          f"https://europepmc.org/article/PMC/{pmcid}", body_text)
                if captions:
                    CAPTION_DIR.mkdir(parents=True, exist_ok=True)
                    (CAPTION_DIR / f"{slugify(pmcid)}_captions.txt").write_text(
                        "\n\n".join(captions), encoding="utf-8")

            ledger.add_record(
                source="europepmc", topic=label, pmcid=pmcid, pmid=item.get("pmid"),
                doi=item.get("doi"), title=title,
                journal=(item.get("journalInfo") or {}).get("journal", {}).get("title"),
                year=item.get("pubYear"), authors=item.get("authorString"),
                licence=licence, open_access=item.get("isOpenAccess"),
                url=f"https://europepmc.org/article/PMC/{pmcid}",
                text_chars=len(body_text), figure_captions=captions,
                criteria=criteria,
            )
            if body_text:
                saved += 1
                criteria_total += len(criteria)
            sources.append({"query": label, "pmcid": pmcid, "title": title,
                            "criteria_hits": len(criteria)})

    log.info("europepmc: %d full texts, %d criteria sentences", saved, criteria_total)
    return {"full_texts": saved, "failed": failed, "criteria_sentences": criteria_total,
            "articles": sources}


# ------------------------------------------------------------ reference pages

def collect_reference_pages(fetcher: Fetcher, ledger: Ledger, write: bool) -> dict:
    """Fetch guideline/reference pages that define prescription content."""
    saved = failed = 0
    pages: list[dict] = []

    for label, url in REFERENCE_PAGES:
        resp = fetcher.get(url)
        if resp is None or resp.status_code != 200:
            log.warning("page failed (%s): %s", label, url)
            failed += 1
            continue
        title, text = html_to_text(resp.text)
        criteria = find_criteria(text)
        if write and text:
            save_text(label, title or label, url, text)
        ledger.add_record(source="reference_page", topic=label, title=title or label,
                          url=url, licence="site terms (see source)", text_chars=len(text),
                          criteria=criteria)
        if text:
            saved += 1
        pages.append({"label": label, "url": url, "title": title,
                      "chars": len(text), "criteria_hits": len(criteria)})
        log.info("page ok: %s (%d chars, %d criteria)", label, len(text), len(criteria))

    return {"pages_saved": saved, "failed": failed, "pages": pages}


# ------------------------------------------------------------------- summary

def write_summary(manifest: dict, ledger: Ledger, write: bool) -> Path:
    licences: dict[str, int] = {}
    commercial = 0
    for img in ledger.images:
        key = img.get("licence") or "unknown"
        licences[key] = licences.get(key, 0) + 1
        if img.get("commercial_use_ok"):
            commercial += 1

    lines = [
        "# Prescription research corpus — collection summary", "",
        f"Collected: {manifest['finished_at']}", "",
        "## What was collected", "",
        "| Source | Result |", "|---|---|",
        f"| Wikimedia Commons images | {manifest['stages'].get('commons', {}).get('images_saved', 0)} |",
        f"| Openverse (CC) images | {manifest['stages'].get('openverse', {}).get('images_saved', 0)} |",
        f"| Europe PMC open-access full texts | {manifest['stages'].get('europepmc', {}).get('full_texts', 0)} |",
        f"| Europe PMC criteria sentences extracted | {manifest['stages'].get('europepmc', {}).get('criteria_sentences', 0)} |",
        f"| Reference / guideline pages | {manifest['stages'].get('reference_pages', {}).get('pages_saved', 0)} |",
        "",
        "## Image licence breakdown", "",
        "| Licence | Images |", "|---|---|",
    ]
    for key, count in sorted(licences.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {key} | {count} |")
    kinds: dict[str, int] = {}
    for img in ledger.images:
        k = img.get("kind") or "unclassified"
        kinds[k] = kinds.get(k, 0) + 1
    lines += [
        "",
        "## Image types (how the prescription was produced)", "",
        "| Type | Images |", "|---|---|",
    ]
    for key, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {key} | {count} |")
    lines += [
        "",
        f"Images cleared for commercial reuse: **{commercial} / {len(ledger.images)}** "
        "(see `licence_ledger.json` → `commercial_use_ok`).",
        "",
        "## Files", "",
        "| File | Contents |", "|---|---|",
        "| `corpus.jsonl` | one JSON record per collected document, incl. criteria sentences |",
        "| `licence_ledger.json` | every image: licence, creator, source page, sha256, commercial flag |",
        "| `text/*.txt` | full text of each article / page |",
        "| `captions/*.txt` | figure captions from the OA articles (prescription examples) |",
        "| `images/commons/*`, `images/openverse/*` | downloaded images |",
        "| `manifest.json` | run metadata + per-stage results |",
        "",
        "## How to use this in the app", "",
        "1. Filter `licence_ledger.json` on `commercial_use_ok == true` before shipping any image.",
        "2. Read the criteria sentences from `corpus.jsonl` (`criteria` field) — they are the "
        "published statements of what a prescription must contain; they back the rules in "
        "`PRESCRIPTION_STANDARDS_REPORT.md`.",
        "3. Use `prescription_rules.json` as the validation schema for the prescription form.",
        "",
    ]
    path = BASE / "CORPUS_SUMMARY.md"
    if write:
        path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ----------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Collect how prescriptions are written + the rules they must satisfy")
    ap.add_argument("--limit", type=int, default=60,
                    help="max images per image source")
    ap.add_argument("--per-query", type=int, default=4,
                    help="open-access articles fetched per literature query")
    ap.add_argument("--delay", type=float, default=DELAY, help="seconds between requests")
    ap.add_argument("--only", choices=["images", "text", "all"], default="all",
                    help="run only the image or only the text stage")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve everything but write/download nothing")
    args = ap.parse_args()
    write = not args.dry_run

    if write:
        for d in (BASE, TEXT_DIR, IMG_DIR):
            d.mkdir(parents=True, exist_ok=True)

    manifest = {
        "tool": "prescription_scraper.py",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": args.dry_run,
        "user_agent": UA,
        "stages": {},
        "notes": [
            "Only openly licensed / public sources are collected.",
            "Every image records licence, creator and source page in licence_ledger.json.",
            "Images are downloaded for handwriting-style and form-layout reference only; "
            "they are not to be used as real prescriptions and may contain personal data.",
        ],
    }

    headers = {"User-Agent": UA, "Accept-Language": "en"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30.0) as client:
        fetcher = Fetcher(client, delay=args.delay)
        ledger = Ledger(write=write)

        if args.only in ("images", "all"):
            manifest["stages"]["commons"] = collect_commons(fetcher, ledger, args.limit, write)
            manifest["stages"]["openverse"] = collect_openverse(fetcher, ledger, args.limit, write)

        if args.only in ("text", "all"):
            manifest["stages"]["europepmc"] = collect_europepmc(fetcher, ledger,
                                                                args.per_query, write)
            manifest["stages"]["reference_pages"] = collect_reference_pages(fetcher, ledger, write)

        manifest["fetcher_stats"] = fetcher.stats

    ledger.flush()
    manifest["images_total"] = len(ledger.images)
    manifest["documents_total"] = len(ledger.records)
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if write:
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    summary = write_summary(manifest, ledger, write)

    print("\n" + "=" * 68)
    print("PRESCRIPTION CORPUS")
    print("=" * 68)
    for stage, result in manifest["stages"].items():
        print(f"  {stage:16s} {json.dumps({k: v for k, v in result.items() if k != 'articles' and k != 'pages'})}")
    print(f"  images total     {manifest['images_total']}")
    print(f"  documents total  {manifest['documents_total']}")
    print(f"  http             {manifest['fetcher_stats']}")
    print(f"  summary          {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
