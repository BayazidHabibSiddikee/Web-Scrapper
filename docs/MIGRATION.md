# Migration and compatibility

The new package is the preferred public surface:

```python
from web_scraper import scrape_web, browser_task
```

Existing tools remain available during migration:

| Existing entry point | New role |
| --- | --- |
| `agent_tools.py` | Legacy JSON/MCP-compatible tool registry |
| `master_pipeline.py` | Advanced WAF-aware pipeline and exports |
| `scrapling_backend.py` | Scrapling-specific backend |
| `maps_scraper.py` | Google Maps specialization |
| `captcha_flow.py` | CAPTCHA specialization |
| `cookies.py` | Authenticated browser-cookie helper |

Do not add new browser-agent behavior to `agent_tools.py`; add it to `web_scraper/browser/` and expose it through the unified API.

Recommended migration order:

1. Move new scraping behavior into `web_scraper/scraping/`.
2. Move browser behavior into `web_scraper/browser/`.
3. Keep old root scripts as adapters until callers migrate.
4. Add deprecation notices only after replacement tests exist.
5. Move MedEx experiments into an optional package rather than the core API.
