# Architecture

The toolkit has two deliberately separate workflows.

## Information flow

```text
scrape_web(url)
  -> URL validation
  -> retrieval backend
  -> HTML/DOM extraction
  -> structured ScrapeResult

browser_task(url, goal)
  -> Playwright browser session
  -> observed page snapshot
  -> provider-neutral LLM decision
  -> strict decision validation
  -> observed-target execution
  -> fresh page observation
  -> BrowserTaskResult
```

## Boundaries

- `web_scraper/scraping` owns retrieval and extraction.
- `web_scraper/browser` owns browser observation and mutation.
- `web_scraper/llm` owns provider calls and response validation.
- `web_scraper/integrations` will own MCP and compatibility adapters.
- Root scripts are legacy or specialized workflows until migrated.

The LLM never emits CSS selectors, JavaScript, shell commands, or coordinates. It chooses only supported operations and IDs observed in the current page snapshot.

## Safety invariants

- URLs supplied to scraping are validated before network access.
- Browser mutations use a page fingerprint and abort if the page changed.
- Targets must still exist and be enabled at execution time.
- Page text is untrusted data, never an instruction channel.
- Step budgets and provider timeouts are mandatory.
- Credentials are read from environment variables and are not logged.

## Current implementation

The public API is implemented in `web_scraper/api.py`. Browser control currently uses Playwright. The current browser observer is intentionally small; the next hardening step is to port the atomic DOM identity and independent verification mechanisms from `jev-ultrafast`.
