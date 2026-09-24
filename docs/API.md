# API reference

## Scrape information

```python
from web_scraper import scrape_web

result = scrape_web(
    "https://example.com",
    selectors={"links": "a::attr(href)"},
    max_chars=12000,
)
```

`ScrapeResult` fields:

- `ok`: whether retrieval succeeded
- `url`: final URL after redirects
- `title`: HTML title, if present
- `text`: normalized visible text
- `links`: extracted links
- `metadata`: status, content type, and optional selector output
- `backend`: retrieval backend name
- `error`: failure detail

## Control a browser

```python
from web_scraper import browser_task

result = browser_task(
    "https://example.com",
    "Find the contact page and open it",
    headless=True,
    max_steps=30,
)
```

`BrowserTaskResult` fields:

- `ok`: whether the task ended normally
- `status`: `done`, `blocked`, or `error`
- `goal`: original goal
- `steps`: executed decisions
- `page`: final observed page
- `error`: failure detail

Scrape results can be exported through `export_result`:

```python
from web_scraper import export_result, scrape_web

result = scrape_web("https://example.com")
if result.ok:
    export_result(result, "output/page.json", "json")
```

Supported formats: `json`, `md`, `csv`, and `sqlite`. The CLI also accepts:

```bash
python toolkit.py scrape https://example.com --output output/page.md --format md
```


```bash
python toolkit.py scrape URL
python toolkit.py browser URL "natural language goal"
```

Use `--headed` to show the browser and `--max-steps` to change the action budget.
