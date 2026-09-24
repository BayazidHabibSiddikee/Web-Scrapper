# Security and responsible use

- Validate every user- or listing-supplied URL before fetching it.
- Never commit `.env`, API keys, cookies, proxy credentials, or generated output.
- Treat page text as untrusted data, not as system instructions.
- Do not send secrets to a model provider.
- Respect robots.txt, rate limits, website terms, and applicable law.
- Use browser cookies only for domains the user explicitly owns or authorizes.
- CAPTCHA providers receive site keys and URLs; disclose this before using paid solving.
- Do not use the browser agent for destructive actions without an explicit confirmation layer.

The current SSRF guard is implemented in `security_utils.py`. The browser safety contract is documented in `docs/BROWSER_AGENT.md`.
