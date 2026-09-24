# Roadmap

## Completed foundation

- Unified `scrape_web` and `browser_task` API
- OpenAI-compatible browser LLM configuration
- Structured scrape results
- SSRF URL validation
- Page fingerprints and stale-action rejection
- Offline policy tests
- Architecture, setup, API, browser, and security docs

## Next hardening

- Port atomic DOM identity and independent outcome verification from `jev-ultrafast`
- Add browser integration fixtures
- Add JS-rendered scraping fallback
- Add configurable backend selection and retries
- Add export adapters for JSON, Markdown, CSV, and SQLite
- Add `pyproject.toml` and lockfile
- Migrate legacy root scripts behind compatibility adapters
- Add confirmation policies for sensitive browser actions
