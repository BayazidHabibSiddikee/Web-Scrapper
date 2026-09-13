---
name: web-scraper
description: Stealth web scraping toolkit — WAF-aware page capture, form detection/filling, CAPTCHA solving, Google Maps lead-gen, file→Markdown, RSS, authenticated scraping via local browser cookies. Use when the user wants to scrape/download/extract web data, fill a web form, bypass bot protection, or convert files to Markdown.
---

# Web Scraper Toolkit — Agent Skill

Location: `/home/sword/Documents/web-scraper`
Venv: `venv/bin/python` (always use this interpreter — deps are installed there).

## Preferred entry: the tool façade

From the toolkit directory, every capability is one call:

```bash
cd /home/sword/Documents/web-scraper
venv/bin/python agent_tools.py --list
venv/bin/python agent_tools.py <tool> --args '<JSON>'
```

Or as Python:

```python
from agent_tools import call_tool
call_tool("fetch", {"url": "https://example.com", "selectors": {"t": "title::text"}})
```

All tools return `{"ok": bool, ...}` — never raise. Check `ok` first.

## Tool selection guide

| User wants | Call | Notes |
|---|---|---|
| Read/extract a normal page | `fetch` (mode=http) | fastest (~250ms), TLS-impersonated |
| Page behind Cloudflare/anti-bot | `scrape` (profile=auto) | WAF-fingerprints then picks Camoufox/Playwright/httpx |
| Hard target, want Scrapling stack | `scrape` (scrapling=true) or `fetch` (mode=stealth) | stealthy_fetch solves CF challenges; needs `camoufox fetch` binary |
| "What's on this site" (whole marketing site) | `saas_extract` | home + pricing/features/about pages, structured |
| Fill a form / sign up / submit data | `extract_forms` FIRST, then `fill_form` | use field `name`/`id`/`label` from schema as keys |
| Checkbox groups | `fill_form` values as lists | `{"topping": ["cheese","bacon"]}` |
| Radio/select by visible text | `fill_form` | case-insensitive match on value OR label |
| Page with CAPTCHA | `solve_captcha` | **free-first**: Cloudflare/Turnstile solved keylessly by Scrapling; only reCAPTCHA/hCaptcha need `CAPTCHA_API_KEY` |
| Local businesses / leads / "places in X" | `maps_leads` | **needs Docker: `docker compose -f maps.compose.yml up -d`** |
| PDF/DOCX/XLSX/PPTX/image → text | `file_to_markdown` | |
| HTML string → Markdown | `convert_html` | |
| RSS/Atom feed | `rss_read` | |
| Logged-in page (user's own account) | `auth_scrape` | uses their local browser cookies (ask which browser) |
| Site URL discovery | `sitemap_crawl` | robots.txt + sitemap.xml |
| Bulk images + screenshot | `grab_images` | saves to output/images/ |

## Known-good example targets

- Form testing: `https://httpbin.org/forms/post` (extract_forms → fill_form round-trip verified)
- reCAPTCHA demo (detection only): `https://www.google.com/recaptcha/api2/demo`
- Safe scraping: `https://example.com`, `https://www.python.org`, `https://news.ycombinator.com`

## Rules

1. **Stealth defaults**: `scrape` auto-escalates httpx → Playwright → Camoufox by WAF
   detection. Even without it, failed scrapes auto-recover via Scrapling stealth browser.
2. **CAPTCHAs are free-first**: `solve_captcha` defaults to Scrapling's keyless
   `solve_cloudflare` (clears CF interstitials + clicks interactive Turnstile in the
   same session used for filling/submitting). It reports honestly: `cloudflare-free`
   / `turnstile-free` (token verified) vs `paid: true`. Only reCAPTCHA v2/v3 and
   hCaptcha need a paid `CAPTCHA_API_KEY` (2captcha/anti-captcha, ~$1-3/1k solves);
   if unset, the tool returns a clear error instead of silently failing.
3. **Rate limits**: Google Maps jobs at depth ≥15 or ≥10 keywords can IP-throttle the
   user — warn once, proceed. Batch keywords into ONE job (`keywords` is a list).
4. **Privacy**: `maps_leads` output contains personal data (phones/emails) — save to
   file, summarize counts in chat, don't dump full CSVs into the conversation.
5. **Cookie scraping is sensitive**: `auth_scrape` reads the user's own browser cookies.
   Only for domains they named; never print cookie values.
6. **Respect robots.txt / ToS** for anything beyond the user's own properties.
7. Big scrapes: prefer `fetch` in `bulk` loops or `examples/distributed_crawler/`
   over spinning Camoufox per page (it's ~2s startup each call).

## Full pipeline (all steps + 5 export formats)

```bash
venv/bin/python master_pipeline.py URL            # WAF-detect → scrape → export
venv/bin/python master_pipeline.py URL --scrapling --use-proxy --capture-har
```

Output lands in `output/` (JSON/CSV/MD/HTML/SQLite + screenshot).
