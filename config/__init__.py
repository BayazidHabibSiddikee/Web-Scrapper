"""
config/
=======
Runtime configuration templates.

Files:
  - stealth.yaml       → stealth profile overrides
  - proxies.txt        → HTTP/SOCKS proxy list (one per line)
  - secrets.yaml       → API keys (2captcha, etc.) — gitignored
  - crawler_defaults.yaml → default crawler settings

Usage:
    import yaml
    with open("config/stealth.yaml") as f:
        cfg = yaml.safe_load(f)
"""
