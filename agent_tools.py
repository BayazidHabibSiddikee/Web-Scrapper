#!/usr/bin/env python3
"""
agent_tools.py
=============
One-call façade over the entire toolkit, shaped for AI agents. Every function
takes simple JSON-able args and returns a dict (never raises — errors come back
under an "error" key). This is the layer the MCP server (mcp_server.py) and the
agent skill (SKILL.md) build on.

    from agent_tools import TOOL_REGISTRY, call_tool
    call_tool("scrape", {"url": "https://example.com"})
    call_tool("extract_forms", {"url": "https://httpbin.org/forms/post"})

Capabilities:
    scrape            WAF-aware page capture (Camoufox/Playwright/httpx/Scrapling)
    fetch             Scrapling fast fetch (TLS impersonation, CSS selectors)
    grab_images       Screenshot + bulk image download
    extract_forms     Form schema discovery (for fill planning)
    fill_form         Auto-fill + submit forms
    solve_captcha     Detect → solve → inject (needs CAPTCHA_API_KEY)
    maps_leads        Google Maps business lead-gen
    file_to_markdown  PDF/DOCX/XLSX/images → Markdown
    saas_extract      Whole-site marketing extraction
    rss_read          RSS/Atom → structured items
    auth_scrape       Scrape with your local browser's cookies
    convert_html      HTML string → Markdown
    sitemap_crawl     Discover URLs from sitemap.xml / BFS
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict

from security_utils import assert_public_url, assert_safe_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("agent_tools")


def _ok(**kw) -> Dict[str, Any]:
    kw.setdefault("ok", True)
    return kw


def _err(msg: str) -> Dict[str, Any]:
    return {"ok": False, "error": str(msg)}


# ---------------------------------------------------------------------------
# Tool implementations (thin, uniform wrappers)
# ---------------------------------------------------------------------------

def t_scrape(args: Dict[str, Any]) -> Dict[str, Any]:
    url = assert_public_url(args["url"])
    from master_pipeline import run_pipeline
    import asyncio
    try:
        res = asyncio.run(run_pipeline(
            url=url,
            stealth_profile=args.get("profile", "auto"),
            screenshot=args.get("screenshot", True),
            solve_captcha=args.get("solve_captcha", False),
            scrapling=args.get("scrapling", False),
            export=args.get("export", True),
        ))
    except Exception as exc:
        return _err(exc)
    step = res.get("steps", {}).get("scrape", {})
    return _ok(url=url, title=step.get("title"), backend=res.get("backend"),
               text_preview=step.get("text_chars"), output="output/pipeline.*")


def t_fetch(args: Dict[str, Any]) -> Dict[str, Any]:
    from scrapling_backend import scrape_scrapling
    url = assert_public_url(args["url"])
    try:
        r = scrape_scrapling(url, mode=args.get("mode", "http"),
                             selectors=args.get("selectors"),
                             impersonate=args.get("impersonate", "chrome"))
    except Exception as exc:
        return _err(exc)
    if r.error:
        return _err(r.error)
    return _ok(url=r.url, status=r.status, title=r.title, text=r.text[:args.get("max_chars", 8000)],
               links=len(r.links), metadata=r.metadata)


def t_grab_images(args: Dict[str, Any]) -> Dict[str, Any]:
    import subprocess, sys
    urls = args["urls"] if isinstance(args.get("urls"), list) else [args["url"]]
    urls = [assert_public_url(url) for url in urls]
    out_dir = assert_safe_path(args.get("out_dir", "output/images"), Path(__file__).resolve().parent / "output")
    try:
        subprocess.run([sys.executable, "grab_images.py", *urls, "--out", out_dir],
                       check=True, timeout=args.get("timeout", 180))
    except Exception as exc:
        return _err(exc)
    return _ok(urls=urls, output_dir=out_dir)


def t_extract_forms(args: Dict[str, Any]) -> Dict[str, Any]:
    from form_fill import extract_forms
    url = assert_public_url(args["url"])
    try:
        forms = extract_forms(url, headless=args.get("headless", True))
    except Exception as exc:
        return _err(exc)
    return _ok(forms=[f.to_dict() for f in forms])


def t_fill_form(args: Dict[str, Any]) -> Dict[str, Any]:
    from form_fill import fill_form
    url = assert_public_url(args["url"])
    try:
        r = fill_form(url, args["values"], submit=args.get("submit", True),
                      form_index=args.get("form_index", 0),
                      screenshot=args.get("screenshot"))
    except Exception as exc:
        return _err(exc)
    return _ok(filled=r.filled, failed=r.failed, missing_required=r.missing_required,
               final_url=r.final_url, response_head=r.response_text[:1000],
               screenshot=r.screenshot_path)


def t_solve_captcha(args: Dict[str, Any]) -> Dict[str, Any]:
    from captcha_flow import solve_captcha_on_page
    url = assert_public_url(args["url"])
    try:
        r = solve_captcha_on_page(
            url=url, captcha_type=args.get("type", "auto"),
            site_key=args.get("site_key"), pre_fill=args.get("pre_fill"),
            submit_selector=args.get("submit_selector"),
            free_first=args.get("free_first", True),
            screenshot=args.get("screenshot"))
    except Exception as exc:
        return _err(exc)
    if r.error:
        return _err(r.error)
    return _ok(detected=r.detected, solved_type=r.solved_type, paid=r.paid,
               injected=r.injected, submitted=r.submitted, final_url=r.final_url,
               session=r.session, screenshot=r.screenshot_path)


def t_maps_leads(args: Dict[str, Any]) -> Dict[str, Any]:
    import maps_scraper as m
    try:
        if not m.health_check():
            return _err("maps API not reachable — run: docker compose -f maps.compose.yml up -d")
        lat, lon = args.get("lat"), args.get("lon")
        if not (lat and lon):
            coords = m.geocode(args.get("city") or args["keywords"][0])
            if not coords:
                return _err("could not geocode; pass lat/lon")
            lat, lon = coords
        rows = m.run_job(args["keywords"], lat, lon, depth=args.get("depth", 5),
                         email=args.get("email", True))
        results = [{k: r.get(k, "") for k in m.LEAD} for r in rows]
        if args.get("socials"):
            m.enrich_sites(results, workers=args.get("workers", 8),
                           emails=args.get("email", True), socials=True)
    except SystemExit as exc:   # maps_scraper uses sys.exit for hard stops
        return _err(exc)
    except Exception as exc:
        return _err(exc)
    out = assert_safe_path(args.get("out", f"output/maps-{int(__import__('time').time())}.json"), Path(__file__).resolve().parent / "output")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    return _ok(count=len(results), saved=out, sample=results[:3])


def t_file_to_markdown(args: Dict[str, Any]) -> Dict[str, Any]:
    sys_path = str(Path(__file__).resolve().parent / "examples" / "content_extract")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from markitdown_convert import convert
    source = args["source"]
    if not str(source).startswith(("http://", "https://")):
        source = assert_safe_path(str(source), Path(__file__).resolve().parent)
    else:
        source = assert_public_url(str(source))
    try:
        md = convert(source)
    except Exception as exc:
        return _err(exc)
    if args.get("output"):
        Path(assert_safe_path(args["output"], Path(__file__).resolve().parent / "output")).write_text(md, encoding="utf-8")
        return _ok(chars=len(md), saved=args["output"])
    return _ok(chars=len(md), markdown=md[: args.get("max_chars", 20000)])


def t_saas_extract(args: Dict[str, Any]) -> Dict[str, Any]:
    import sys
    p = str(Path(__file__).resolve().parent / "examples" / "content_extract")
    if p not in sys.path:
        sys.path.insert(0, p)
    from saas_extract import scrape_website
    url = assert_public_url(args["url"])
    try:
        d = scrape_website(url, max_subpages=args.get("subpages", 3))
    except Exception as exc:
        return _err(exc)
    return _ok(**d)


def t_rss_read(args: Dict[str, Any]) -> Dict[str, Any]:
    url = assert_public_url(args["url"])
    try:
        import feedparser
    except ImportError:
        return _err("feedparser not installed")
    d = feedparser.parse(url)
    items = [{"title": e.get("title"), "link": e.get("link"),
              "published": e.get("published"),
              "summary": (e.get("summary") or "")[:300]}
             for e in d.entries[: args.get("limit", 20)]]
    return _ok(feed_title=d.feed.get("title"), count=len(items), items=items)


def t_auth_scrape(args: Dict[str, Any]) -> Dict[str, Any]:
    from cookies import auth_scrape
    url = assert_public_url(args["url"])
    try:
        r = auth_scrape(url, browser=args.get("browser", "chrome"),
                        screenshot=args.get("screenshot"))
    except Exception as exc:
        return _err(exc)
    return _ok(url=r.url, title=r.title, cookies_used=getattr(r, "metadata", {}).get("cookies_used"),
               text_preview=r.text[: args.get("max_chars", 4000)],
               screenshot=r.screenshot_path, error=r.error)


def t_convert_html(args: Dict[str, Any]) -> Dict[str, Any]:
    import io
    try:
        from markitdown import MarkItDown
    except ImportError:
        return _err("markitdown not installed")
    md = MarkItDown(enable_plugins=False)
    try:
        out = md.convert_stream(
            io.BytesIO(args["html"].encode("utf-8")), file_extension=".html").text_content
    except Exception as exc:
        return _err(exc)
    return _ok(markdown=out[: args.get("max_chars", 20000)])


def _parse_sitemap_loc(xml_text: str) -> list:
    """Extract all <loc> URLs from a sitemap/sitemapindex XML document."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # tolerant fallback: strip entities and retry once
        cleaned = xml_text.replace("&amp;", "&")
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError:
            return []
    return [el.text.strip() for el in root.iter()
            if el.tag.endswith("loc") and el.text and el.text.strip()]


def t_sitemap_crawl(args: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    from urllib.parse import urljoin
    base = assert_public_url(args["url"]).rstrip("/")
    urls = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; web-scraper-toolkit)"}
    # 1) robots.txt → declared sitemaps; 2) common paths as fallback
    candidates = []
    try:
        with httpx.Client(follow_redirects=False, timeout=20, headers=headers) as c:
            robots = c.get(urljoin(base + "/", "robots.txt"))
            if robots.status_code == 200:
                candidates += [l.split(":", 1)[1].strip()
                               for l in robots.text.splitlines()
                               if l.lower().startswith("sitemap:")]
    except Exception:
        pass
    if not candidates:
        candidates = [urljoin(base + "/", p)
                      for p in ("sitemap.xml", "sitemap_index.xml", "wp-sitemap.xml")]
    with httpx.Client(follow_redirects=False, timeout=20, headers=headers) as c:
        for sm in candidates[:4]:
            try:
                r = c.get(sm)
                if r.status_code == 200:
                    found = _parse_sitemap_loc(r.text)
                    # a sitemapindex's <loc>s are sitemaps — one level of recursion
                    for inner in found[:3] if "sitemapindex" in r.text[:500] else []:
                        try:
                            ri = c.get(inner)
                            if ri.status_code == 200:
                                found += _parse_sitemap_loc(ri.text)
                        except Exception:
                            pass
                    urls += found
            except Exception:
                pass
    seen, uniq = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return _ok(count=len(uniq), urls=uniq[: args.get("limit", 500)])


TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "scrape":         {"fn": t_scrape,         "desc": "WAF-aware page capture; auto-picks stealth backend; exports 5 formats"},
    "fetch":          {"fn": t_fetch,          "desc": "Fast TLS-impersonating fetch (Scrapling) with optional CSS selectors"},
    "grab_images":    {"fn": t_grab_images,    "desc": "Screenshot a page and download all its images"},
    "extract_forms":  {"fn": t_extract_forms,  "desc": "List forms + fields (name/type/required/options) as a JSON schema"},
    "fill_form":      {"fn": t_fill_form,      "desc": "Auto-fill and submit a form from a values dict"},
    "solve_captcha":  {"fn": t_solve_captcha,  "desc": "CAPTCHA flow — free-first: Scrapling solves Cloudflare/Turnstile without a key; reCAPTCHA/hCaptcha escalate to 2captcha (CAPTCHA_API_KEY)"},
    "maps_leads":     {"fn": t_maps_leads,     "desc": "Google Maps business leads: name/phone/emails/socials (needs Docker API)"},
    "file_to_markdown": {"fn": t_file_to_markdown, "desc": "PDF/DOCX/XLSX/PPTX/images/audio → Markdown"},
    "saas_extract":   {"fn": t_saas_extract,   "desc": "Scrape a whole marketing site: home + pricing/features/about"},
    "rss_read":       {"fn": t_rss_read,       "desc": "Parse an RSS/Atom feed into items"},
    "auth_scrape":    {"fn": t_auth_scrape,    "desc": "Scrape a logged-in page reusing your local browser's cookies"},
    "convert_html":   {"fn": t_convert_html,   "desc": "Convert an HTML string to Markdown"},
    "sitemap_crawl":  {"fn": t_sitemap_crawl,  "desc": "Discover a site's URLs from sitemap.xml / robots.txt"},
}


def call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a tool by name. Never raises — returns an error dict instead."""
    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return _err(f"unknown tool {name!r}; available: {', '.join(TOOL_REGISTRY)}")
    try:
        return entry["fn"](args or {})
    except KeyError as exc:
        return _err(f"missing required arg: {exc}")
    except Exception as exc:
        log.exception("tool %s failed", name)
        return _err(exc)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Agent tool façade (call any toolkit capability)")
    ap.add_argument("tool", nargs="?", help=f"one of: {', '.join(TOOL_REGISTRY)}")
    ap.add_argument("--args", default="{}", help="JSON arguments dict")
    ap.add_argument("--list", action="store_true", help="show all tools + descriptions")
    a = ap.parse_args()

    if a.list or not a.tool:
        for name, meta in TOOL_REGISTRY.items():
            print(f"{name:20} {meta['desc']}")
        raise SystemExit(0)

    result = call_tool(a.tool, json.loads(a.args))
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
