# Social posts — web-scraper toolkit launch
# Copy/paste-ready for LinkedIn / Facebook / Reddit r/webscraping
# Edit the [LINK] placeholder before posting.

## ─── LinkedIn ────────────────────────────────────────────────────────────────

🕷️ Built an "unbeatable" web scraper.

I merged every scraping library I had across ~/Documents into one production toolkit —
then made it fully agent-operable.

What ended up inside web-scraper/:
• 4 stealth backends: Camoufox (Firefox), Playwright, httpx (fast HTTP/2),
  plus Scrapling's curl_cffi TLS-spoofed fetcher — chosen automatically by WAF detection
• Form engine that auto-detects fields, fills by label or value, handles radios
  + multi-checkbox groups, and submits — verified against httpbin.org
• CAPTCHA solving that is free-first: Cloudflare interstitials + Turnstile boxes are
  solved in-session by Scrapling (no API key). reCAPTCHA/hCaptcha escalate to
  2captcha/anti-captcha only when a paid key is set (caveats fully documented).
• Google Maps lead-gen (gosom Docker + stealth website enrichment for phones/emails/socials)
• File → Markdown (PDF/DOCX/XLSX/PPTX/images/audio via markitdown)
• Cookie auth: reuse your local browser's cookies for logged-in pages
• SSRF guards on every untrusted URL
• An MCP server (stdio) exposing all 13 tools as native agent capabilities
• 13-tool façade (agent_tools.py) — JSON-in, JSON-out, never raises
• Skill docs + llms.txt so any AI agent can self-service it

Live tested end-to-end:
✅ Scrapling cleared nowsecure.nl's CF wall keylessly
✅ Form fill submitted to httpbin with correct values
✅ MCP handshake lists all tools; rss_read + extract_forms work
✅ sitemap_crawl finds 889 URLs on cloudflare.com

Repos: https://github.com/BayazidHabibSiddikee/Web-Scrapper

Built because:
1. I kept writing ad-hoc scrapers instead of maintaining one good one.
2. If an AI agent can fill a form behind Cloudflare and scrape a logged-in dashboard
   in one command, every other tool around it should be reachable the same way.
3. The CAPTCHA world needs better than "hardcode a paid key" — the easy cases
   should work free, and the hard ones should fail loudly with cost info.

Happy to show a walkthrough or answer questions.

## ─── Facebook ─────────────────────────────────────────────────────────────────

I just finished merging my entire ~/Documents into one weapon-grade web-scraper
and making it callable by an AI agent.

TL;DR — one toolkit, every scenario:

• Stealth browsers: Camoufox / Playwright / httpx / Scrapling (TLS-spoof), chosen by WAF
• Form filling: auto-detect fields → fill text/radio/select/checkbox → submit
• CAPTCHA: Free for Cloudflare/ Turnstile (same session solves it). reCAPTCHA/hCaptcha need
  a paid key — and yes, I explain exactly why in the README.
• Google Maps leads: businesses + phones + emails + Instagram/FB/LI handles
• Files → Markdown (PDF / DOCX / XLSX / images / audio / HTML)
• Auth scraping via your browser's cookies (least-privilege per domain)
• RSS feeds, sitemap discovery, HAR traffic capture
• 13-tools MCP server + Python façade — plug into any agent
• Full README / SKILL.md / llms.txt so agents self-navigate

Proof points:
✅ Scrapling solved Cloudflare on nowsecure.nl (HTTP 200, zero cost)
✅ Form submission echoed back by httpbin.org (values correct)
✅ MCP client listed all 13 tools and called fetch/rss_read/convert_html over stdio
✅ Found 889 URLs crawling cloudflare.com's sitemaps

GitHub: https://github.com/BayazidHabibSiddikee/Web-Scrapper
Dockerfile + Makefile included.

Questions? Happy to walk through the architecture or share how the captcha-free path works.

## ─── Reddit (r/webscraping & r/AIVillagers) ───────────────────────────────────

**An unbeatable web scraper toolkit, fully agent-readable**

I've been accumulating scraper projects across ~/Documents — Scrapling, google-maps-scraper-kit,
openshorts, Agent-Reach, markitdown, browser-use captcha/form examples — and I got tired of
picking from a bag of partial solutions for each job. So I merged them into one arsenal
at github.com/BayazidHabibSiddikee/Web-Scrapper and made it MCP-friendly so an AI agent
can drive every capability through a single stdio server.

What landed:
- 4 backend selection paths (Camoufox/Playwright/httpx/Scrapling) with automatic WAF fingerprinting
- `form_fill.py` — detects forms on a page, returns a JSON schema, then fills/submits
  with radio-select-by-label and multi-checkbox support. Verified end-to-end on httpbin.org.
- `captcha_flow.py` — **free-first**: clears Cloudflare interstitials AND clicks interactive
  Turnstile widgets in-session (Scrapling's solve_cloudflare=True). Only escalates to the
  paid 2captcha/anti-captcha path for reCAPTCHA/hCaptcha, when you've set CAPTCHA_API_KEY.
  Caveats section is honest about costs/behavior.
- `cookies.py` — least-privilege cookie extraction from Chrome/Edge/Firefox/Brave for
  authenticated scraping; no dump-everything approach.
- `maps_scraper.py` — Google Maps business leads via the gosom API, with HTTP→httpx→Camoufox
  escalation for website enrichment (email/socials). SSRF-guards every URL.
- `markitdown_convert.py` + `saas_extract.py` — universal file→Markdown and whole-site
  marketing extraction
- `medex_scraper.py` — ported from a friend's script: 25k Bangladesh pharma-brand detail pages
  (indications, dosage, prices in ৳, overdose effects etc.)
- Full MCP server (`mcp_server.py`) — 13 stdio tools. Verified live: MCP client talks
  to the server, calls extract_forms/rss_read/convert_html.
- SKILL.md + llms.txt for agent self-navigation.

Live verified (no keys, free paths):
• Scrapling free pass solved Cloudflare on nowsecure.nl (status=200, token absent → honest report)
• httpbin.org form submission echoed back correct values including "Medium"→radio mapped case-insensitively
• agent_tools.py fetch+selectors returns clean title + metadata
• sitemap_crawl discovers 889 URLs on cloudflare.com
• cookies.py read Brave's github.com cookies (domain-scoped)

Not a wrapper project — I ported working logic (section maps from the original medex scraper,
price regexes, SSRF guard from openshorts, least-privilege cookie extraction pattern from
Agent-Reach) and rewrote the plumbing on this toolkit's faster stack (async httpx,
Camoufox 0.5, Scrapling's own bypasses). All commits are on main; make check compiles everything.

One thing worth flagging: medex.com.bd throws a custom captcha-challenge after ~30 fast hits
(Laravel XSRF-form click-to-continue). The code detects it, but the session-mode needs a
persistent browser to click the box once and ride clearance cookies — still tuning the retry
backoff there.

PRs welcome, especially if you know a cleaner handle for the persistent-Turnstile-per-brand
loop.

Link in comments.
