# Roadmap

## Completed foundation

- Unified `scrape_web` and `browser_task` API
- OpenAI-compatible browser LLM configuration
- Structured scrape results
- SSRF URL validation
- Page fingerprints and stale-action rejection
- Redirect-aware HTTP URL validation
- Browser navigation/request SSRF blocking
- Secret-safe browser observations
- Independent goal verification for `DONE`
- Stale decision re-observation without replaying mutations
- JSON, Markdown, CSV, and SQLite result exporters
- Offline policy tests
- Architecture, setup, API, browser, and security docs

## Next hardening

- Port atomic DOM identity and independent outcome verification from `jev-ultrafast`
- Add browser integration fixtures
- Add `pyproject.toml` and lockfile
- Migrate legacy root scripts behind compatibility adapters
- Add confirmation policies for sensitive browser actions
