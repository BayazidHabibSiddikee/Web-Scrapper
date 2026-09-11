# Web Scraper Toolkit

> Pro-level web scraping arsenal — from screenshots to full crawlers.

## What's in the box

| Category | Tools | Path |
|----------|-------|------|
| **Screenshots** | Playwright, shot-scraper | `examples/screenshot/` |
| **Anti-detection** | Camoufox, undetected-chromedriver, selenium-stealth, Playwright stealth | `examples/anti_detection/` |
| **Content extraction** | Trafilatura, Readability, Newspaper3k, BeautifulSoup | `examples/content_extract/` |
| **Crawlers** | BFS spider, sitemap crawler, API hunter, dir brute-force | `examples/crawlers/` |
| **Stealth profiles** | 6 pre-built profiles (Cloudflare, paranoid, crawler, screenshot…) | `examples/stealth/` |
| **Output formats** | JSON, CSV, Markdown, HTML report, SQLite | `examples/output/` |
| **Frameworks** | httpx+parsel, Scrapy | `examples/frameworks/` |
| **Proxy rotation** | Health-checked pool, round-robin/random/best/sticky, httpx/requests/Selenium/Playwright | `examples/proxy_rotation/` |
| **CAPTCHA solving** | 2captcha + anti-captcha; image, reCAPTCHA v2/v3, hCaptcha, Turnstile | `examples/captcha_solver/` |
| **Traffic sniffer** | Playwright CDP HAR + mitmproxy addon; HAR→CSV/SQLite/endpoint extraction | `examples/traffic_sniffer/` |
| **Master pipeline** | End-to-end pipeline combining all modules | `master_pipeline.py` |
| **Core module** | Camoufox + Selenium scraper | `scraper.py` |

## Quick start

```bash
cd /home/sword/Documents/web-scraper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Tool selection matrix

```
┌──────────────────┬───────────────────────────────────────────────────┐
│ Need             │ Use                                              │
├──────────────────┼───────────────────────────────────────────────────┤
│ Screenshot only  │ shot-scraper (CLI) or Playwright screenshot      │
│ Cloudflare bypass │ Camoufox + Selenium / Playwright                 │
│ Full DOM scrape  │ Camoufox + Selenium (selectors, clicks)          │
│ Speed / no JS    │ httpx + parsel (HTTP/2, async)                   │
│ Production crawl │ Scrapy (pipelines, throttling, feeds)            │
│ Article content  │ Trafilatura (best), Readability, Newspaper3k     │
│ Quick extraction │ BeautifulSoup (manual selectors)                 │
│ Batch screenshots│ Playwright async or shot-scraper batch            │
│ API discovery     │ crawlers.py --mode api                           │
│ Site mapping      │ crawlers.py --mode bfs / --mode sitemap          │
│ Paranoid stealth  │ stealth_profiles.py — "stealth_max" profile      │
└──────────────────┴───────────────────────────────────────────────────┘
```

## Anti-detection stack (ranked)

| Rank | Tool | Best for | Cloudflare? |
|------|------|----------|-------------|
| 1 | **Camoufox** | Heavy protection, full DOM | ✅ Best |
| 2 | **Playwright + stealth patches** | General purpose | ✅ Good |
| 3 | **undetected-chromedriver** | Chrome-based sites | ⚠️ Moderate |
| 4 | **selenium-stealth** | Selenium addon | ⚠️ Light |

### Camoufox vs Playwright vs Selenium

```
Camoufox  → spoofs TLS fingerprint, canvas, WebGL, audio at browser level
Playwright → faster, better async API, strong stealth defaults
Selenium  → slowest, most detectable headless, but full ecosystem
```

## Stealth profiles

Six pre-built profiles in `examples/stealth/stealth_profiles.py`:

| Profile | Use case | Extra wait |
|---------|----------|-----------|
| `cloudflare` | Cloudflare-heavy sites | 5s |
| `bot_detected` | Basic bot checks | 3s |
| `stealth_max` | Paranoid mode | 8s |
| `crawler` | Polite bulk crawling | 1s |
| `screenshot` | Screenshot capture | 3s |

## Example: full pipeline

```python
from scraper import scrape, ScrapeConfig
from examples.stealth.stealth_profiles import get_profile, apply_to_playwright_context
from examples.content_extract.content_extract import extract_trafilatura
from examples.output.output_formats import export_all, ScrapeRecord

# 1. Scrape with Camoufox stealth
profile = get_profile("cloudflare")
config = ScrapeConfig(
    url="https://target.example.com/article/123",
    screenshot_path="output/screenshots/article.png",
    wait_seconds=profile.extra_wait_seconds,
    scroll_to_bottom=profile.scroll_to_bottom,
    headless=profile.headless,
)
result = scrape(config)

# 2. Extract clean article text
article = extract_trafilatura(result.url, html=result.html)

# 3. Save to all formats
record = ScrapeRecord(
    url=article.url,
    title=article.title,
    text=article.text,
    links=article.links,
    images=article.images,
    author=article.author,
    publish_date=article.publish_date,
    metadata=article.metadata,
    timestamp=datetime.utcnow().isoformat(),
)
outputs = export_all([record], base_name="output/article")
```

## CLI quick commands

```bash
# Screenshot with Playwright
python examples/screenshot/playwright_screenshot.py https://example.com

# Screenshot with shot-scraper
pip install shot-scraper
shot-scraper https://example.com -o shot.png --full-page

# BFS crawl
python examples/crawlers/crawlers.py https://example.com --mode bfs --depth 2

# Sitemap extraction
python examples/crawlers/crawlers.py https://example.com --mode sitemap

# API endpoint hunter
python examples/crawlers/crawlers.py https://example.com --mode api

# Directory brute-force
python examples/crawlers/crawlers.py https://example.com --mode dir

# Compare extractors
python examples/content_extract/content_extract.py https://news.ycombinator.com

# Compare stealth methods
python examples/anti_detection/anti_detection.py https://example.com
```

## Proxy rotation

`examples/proxy_rotation/proxy_rotation.py` — `ProxyManager`

| Feature | Detail |
|---------|--------|
| Protocols | HTTP, HTTPS, SOCKS4, SOCKS5 |
| Strategies | `round_robin`, `random`, `best` (score-based), `sticky` (same proxy per domain) |
| Health checks | Auto background checks, auto-remove dead proxies after N failures |
| Integrations | `requests`, `httpx` async, Selenium options, Playwright context |
| Scoring | Latency + success rate + tier bonus |

```python
from examples.proxy_rotation.proxy_rotation import ProxyManager

pm = ProxyManager("config/proxies.txt", strategy="best")

# requests
resp = pm.requests_get("https://httpbin.org/ip")

# httpx async
client = pm.httpx_client("example.com")
resp = await client.get("https://httpbin.org/ip")

# Selenium
opts = pm.selenium_options("chrome")
driver = webdriver.Chrome(options=opts)

# Playwright
context, proxy = await pm.playwright_context(browser, "example.com")
page = await context.new_page()
```

Proxy file format (`config/proxies.txt`):
```
http://user:pass@proxy.example.com:8080
socks5://127.0.0.1:9050
192.168.1.100:3128
```

## CAPTCHA solving

`examples/captcha_solver/captcha_solver.py` — `CaptchaSolver`

| Type | Service | Method |
|------|---------|--------|
| Image CAPTCHA | 2captcha, anti-captcha | `solve_image()` |
| reCAPTCHA v2 | 2captcha, anti-captcha | `solve_recaptcha_v2()` |
| reCAPTCHA v3 | 2captcha, anti-captcha | `solve_recaptcha_v3()` |
| hCaptcha | 2captcha, anti-captcha | `solve_hcaptcha()` |
| Turnstile | 2captcha | `solve_turnstile()` |

```python
from examples.captcha_solver.captcha_solver import CaptchaSolver

solver = CaptchaSolver(
    service="2captcha",
    api_key=os.getenv("CAPTCHA_API_KEY"),
)

# Solve reCAPTCHA v2
result = solver.solve_recaptcha_v2(
    site_key="SITE_KEY_FROM_PAGE",
    page_url="https://example.com/login",
)
print(result.token, result.elapsed_seconds)

# Selenium integration — auto-inject token
token = solver.selenium_solve_recaptcha(driver)

# Playwright integration
token = await solver.playwright_solve_recaptcha(page)
```

Environment variables:
```bash
export CAPTCHA_SERVICE=2captcha
export CAPTCHA_API_KEY=your_key_here
```

## Traffic sniffer / HAR capture

`examples/traffic_sniffer/traffic_sniffer.py`

Two backends:

| Backend | Pros | Cons |
|---------|------|------|
| **Playwright CDP** | No extra binary, async, built-in | Chromium only |
| **mitmproxy** | All browsers, full HTTPS intercept, WebSocket | Requires mitmproxy binary |

```python
# Playwright HAR
from examples.traffic_sniffer.traffic_sniffer import capture_with_playwright
entries = asyncio.run(capture_with_playwright(
    "https://example.com",
    output="output/traffic.har",
    wait=5.0,
))

# mitmproxy (programmatic)
from examples.traffic_sniffer.traffic_sniffer import MitmproxyHarvester
hv = MitmproxyHarvester(output="output/traffic.har")
hv.start()
# route traffic through hv.get_proxy_url()
hv.stop()
```

HAR analysis:
```python
from examples.traffic_sniffer.traffic_sniffer import har_to_endpoints, analyze_har

# Extract API endpoints
eps = har_to_endpoints("output/traffic.har")
print(eps["api"])    # ['/api/users', '/v1/search', ...]
print(eps["graphql"])

# Full analysis
report = analyze_har("output/traffic.har")
print(report["total_bytes"], report["status_codes"])
```

Export HAR to other formats:
```python
har_to_csv("traffic.har", "output/traffic.csv")
har_to_sqlite("traffic.har", "output/traffic.db")
```

## Master pipeline

`master_pipeline.py` — run everything in one shot:

```bash
# Basic pipeline
python master_pipeline.py https://example.com

# With proxy + HAR capture + CAPTCHA solving
python master_pipeline.py https://example.com \
  --use-proxy \
  --capture-har \
  --har-backend playwright \
  --solve-captcha \
  --profile stealth_max \
  --wait 8.0
```

Pipeline steps:
1. Proxy rotation (optional)
2. Screenshot + stealth scrape (Camoufox/Playwright/UC)
3. CAPTCHA detection + solving (optional, requires API key)
4. Network traffic capture → HAR (optional)
5. Content extraction → Trafilatura clean text
6. Export → JSON + CSV + Markdown + HTML + SQLite

## Anti-bot bypass tips

1. **Camoufox is non-negotiable for Cloudflare** — it patches TLS/canvas/WebGL at the binary level
2. **Never use default headless Chrome** — sites detect `navigator.webdriver` instantly
3. **Use realistic user agents** — rotate across Chrome/Firefox/Safari
4. **Add delays** — 1-5s grace period after page load
5. **Accept cookies / dismiss banners** — cookie walls break scrapers
6. **Spoof timezone/locale** — mismatch is a red flag
7. **Rotate IPs / use proxies** — rate limiting + IP diversity (see `ProxyManager`)
8. **Playwright context reuse** — one context, many pages (no browser relaunch)
9. **Solve CAPTCHAs programmatically** — integrate 2captcha for image/reCAPTCHA/hCaptcha
10. **Capture and analyze traffic** — use HAR to find hidden API endpoints

## Output formats

All formats from `examples/output/output_formats.py`:

- **JSON** — structured, metadata preserved
- **CSV** — flat table, metadata flattened as `metadata__key`
- **Markdown** — readable report with links + previews
- **HTML** — dark-mode standalone report
- **SQLite** — queryable database for large result sets

## Roadmap

- [x] Proxy rotation with health checks (SOCKS5/HTTP/HTTPS)
- [x] CAPTCHA solving integration (2captcha / anti-captcha)
- [x] HAR capture for traffic analysis (Playwright CDP + mitmproxy)
- [x] Master pipeline tying all modules together
- [ ] WAF fingerprinting module
- [ ] Distributed crawl with task queue (Redis + Celery)
- [ ] Browserless.com / ScrapingBee API integration
- [ ] Puppeteer extra-stealth plugin wrapper
