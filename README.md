# 🕷️ Web Scraper Toolkit

> A production-grade Python scraping arsenal — stealth browsing, WAF detection, image grabbing, distributed crawling, and more. Built to handle the modern anti-bot web.

**One command, full page:** WAF detection picks the right stealth backend automatically, downloads the page, takes a full-page screenshot, extracts clean article text, and exports to 5 formats.

```bash
python master_pipeline.py https://example.com
```

## 📸 Real output

All screenshots below were captured by this toolkit — full-page stealth renders through Camoufox, no manual touch-ups.

### GitHub — full-page scrape

The complete github.com landing page (originally 1920×11809 px) captured in a single run, with 22 images (2.6 MB) downloaded alongside it.

![GitHub full-page scrape](docs/screenshots/github_fullpage.png)

### Python.org

![Python.org scrape](docs/screenshots/python_org.png)

### Hacker News

A Cloudflare-aware scrape of a minimal, JS-light site:

![Hacker News scrape](docs/screenshots/hackernews.png)

---

## ✨ Highlights

- 🛡️ **WAF-aware backend selection** — fingerprints Cloudflare/Akamai/Imperva/Sucuri/AWS WAF/F5 and picks the right tool: Camoufox for heavy protection, Playwright for medium, raw `httpx` for none
- 🦊 **Camoufox stealth Firefox** — TLS fingerprint, canvas/WebGL/audio noise patched at browser level; not detectable like stock headless Chrome
- 🖼️ **Image grabber** — screenshots any page and bulk-downloads its images (`<img>`, `srcset`, `og:image`, CSS backgrounds) with proper Referer headers
- 🧬 **Deterministic fingerprint noise** — canvas/WebGL/audio spoofing with consistent per-session seeds, so your fingerprint is realistic but stable
- 🔁 **Resilience built in** — circuit breakers, exponential backoff with jitter, token-bucket rate limiting, request deduplication
- 🌐 **Proxy rotation** — health-checked pool with round-robin/random/best/sticky strategies; integrates with requests, httpx, Selenium, and Playwright
- 🧩 **CAPTCHA solving** — 2captcha/anti-captcha integration for image CAPTCHAs, reCAPTCHA v2/v3, hCaptcha, and Cloudflare Turnstile
- 🔍 **Traffic sniffing** — record pages to HAR via Playwright CDP or mitmproxy; extract hidden API endpoints, export to CSV/SQLite
- ⚡ **Distributed crawling** — Redis-backed master/worker architecture with deduplication, priority queues, and stats
- 📊 **5 export formats** — JSON, CSV, Markdown, dark-mode HTML report, SQLite

## What's in the box

| Category | Tools | Path |
|----------|-------|------|
| **Core scraper** | Camoufox + Playwright (download + screenshot + extract) | `scraper.py` |
| **Image grabber** | Page screenshot + bulk image download | `grab_images.py` |
| **Master pipeline** | WAF-detect → auto-backend → scrape → extract → export | `master_pipeline.py` |
| **Screenshots** | Playwright, shot-scraper | `examples/screenshot/` |
| **Anti-detection** | Camoufox, undetected-chromedriver, selenium-stealth, Playwright stealth | `examples/anti_detection/` |
| **Content extraction** | Trafilatura, Readability, Newspaper3k, BeautifulSoup | `examples/content_extract/` |
| **Crawlers** | BFS spider, sitemap crawler, API hunter, dir brute-force | `examples/crawlers/` |
| **Stealth profiles** | 6 pre-built profiles (Cloudflare, paranoid, crawler, screenshot…) | `examples/stealth/` |
| **Output formats** | JSON, CSV, Markdown, HTML report, SQLite | `examples/output/` |
| **Frameworks** | httpx+parsel, Scrapy | `examples/frameworks/` |
| **Proxy rotation** | Health-checked pool, 4 strategies, all browser integrations | `examples/proxy_rotation/` |
| **CAPTCHA solving** | 2captcha + anti-captcha; image, reCAPTCHA v2/v3, hCaptcha, Turnstile | `examples/captcha_solver/` |
| **Traffic sniffer** | Playwright CDP HAR + mitmproxy; HAR→CSV/SQLite/endpoints | `examples/traffic_sniffer/` |
| **WAF fingerprinting** | 14+ WAFs via headers/cookies/TLS/HTML | `examples/waf_fingerprint/` |
| **Resilience** | Circuit breaker, backoff+jitter, rate limiter, dedup | `examples/resilience/` |
| **Fingerprint generator** | Canvas/WebGL/audio/screen/WebRTC spoofing | `examples/fingerprint_generator/` |
| **Distributed crawler** | Redis master/worker task queue | `examples/distributed_crawler/` |

## 🚀 Quick start

```bash
git clone <this-repo>
cd web-scraper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# One-time: fetch the Camoufox stealth Firefox binary (~150 MB)
camoufox fetch

# One-time: install Playwright Chromium (for the Playwright backend)
playwright install chromium
```

## 📖 Usage

### One-command scrape (recommended)

```bash
# Auto-detects WAF, picks backend, screenshots, extracts, exports
python master_pipeline.py https://example.com

# Full firepower: proxy rotation + HAR capture + CAPTCHA solving
python master_pipeline.py https://example.com \
  --use-proxy \
  --capture-har \
  --solve-captcha \
  --profile auto
```

### Download pages + grab images

```bash
# Screenshot the page AND download all its images
python grab_images.py https://github.com https://www.python.org
```

Results land in `output/images/<site>/` — full-page screenshot plus every image above the size filter.

### Python API

```python
from scraper import scrape, ScrapeConfig

result = scrape(ScrapeConfig(
    url="https://github.com/trending",
    screenshot_path="trending.png",
    screenshot_full_page=True,
    wait_seconds=5.0,
    scroll_to_bottom=True,       # trigger lazy-loaded content
    selectors={                   # custom CSS selectors
        "repo_names": "h2 a",
        "stars": "a.Link--muted",
    },
))

print(result.title)          # page title
print(result.text[:500])     # visible text
print(result.links)          # all links
print(result.metadata)       # your selector results
print(result.screenshot_path)
```

### Individual tools

```bash
# Screenshot with Playwright
python examples/screenshot/playwright_screenshot.py https://example.com

# BFS crawl a site
python examples/crawlers/crawlers.py https://example.com --mode bfs --depth 2

# Hunt API endpoints in page source
python examples/crawlers/crawlers.py https://example.com --mode api

# Fingerprint a target's WAF protection
python examples/waf_fingerprint/waf_fingerprint.py https://example.com

# Health-check your proxy pool
python examples/proxy_rotation/proxy_rotation.py config/proxies.txt
```

### Distributed crawling

```bash
# Terminal 1 — master: seed URLs
python examples/distributed_crawler/distributed_crawler.py master --seeds urls.txt

# Terminal 2+ — workers (each uses WAF-aware backend selection)
python examples/distributed_crawler/distributed_crawler.py worker --concurrency 5

# Monitor
python examples/distributed_crawler/distributed_crawler.py status
```

Falls back to an in-memory queue if Redis isn't running.

## 🧠 How auto backend selection works

```
                    ┌─────────────────┐
                    │  Target URL     │
                    └────────┬────────┘
                             ▼
                  ┌──────────────────────┐
                  │  WAF fingerprinting  │
                  │  headers · cookies   │
                  │  TLS cert · HTML     │
                  └────────┬─────────────┘
                           ▼
        ┌──────────────────┼──────────────────────┐
        ▼                  ▼                      ▼
  Cloudflare/Akamai/   AWS WAF/Fastly/        No WAF found
  Imperva/Sucuri       F5/FortiWeb
        ▼                  ▼                      ▼
   ┌─────────┐      ┌────────────┐         ┌──────────┐
   │Camoufox │      │ Playwright │         │  httpx   │
   │ Firefox │      │+ max stealth│        │(HTTP/2)  │
   └─────────┘      └────────────┘         └──────────┘
```

## ⚙️ Configuration

Stealth behavior is tuned via `config/stealth.yaml` — per-profile overrides for wait times, user agents, scrolling, and window sizes:

```yaml
cloudflare:
  extra_wait_seconds: 6.0
  scroll_to_bottom: false
```

Proxies go in `config/proxies.txt` (one per line, `socks5://` supported). CAPTCHA keys via environment:

```bash
export CAPTCHA_SERVICE=2captcha
export CAPTCHA_API_KEY=your_key
```

## ✅ Verified

The toolkit is smoke-tested end-to-end against live sites:

- `scraper.py` — downloads HTML, extracts text/links, saves screenshots (Camoufox + Playwright)
- `master_pipeline.py` — full auto path: WAF detection → backend selection → extraction → 5-format export
- `grab_images.py` — GitHub (22 images, 2.6 MB), python.org (5 images), Hacker News
- Nested event-loop safety — works inside async callers and the distributed crawler

## 🗺️ Roadmap

- [ ] WAF-aware session persistence (reuse cf_clearance cookies)
- [ ] Browserless/ScrapingBee API integration as optional fallback
- [ ] Sitemap-driven distributed crawling
- [ ] Diff-based change detection for periodic re-scrapes

## ⚠️ Disclaimer

This toolkit is for **educational purposes and authorized scraping** — your own sites, APIs with permission, public data, or security research within legal bounds. Respect `robots.txt`, rate limits, and terms of service. You are responsible for how you use it.

## License

MIT
