# Setup

## Install

```bash
cd /home/sword/Documents/web-scraper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Scraping with the unified HTTP backend does not require an LLM key. Browser control does.

## Browser LLM configuration

```bash
export BROWSER_LLM_API_KEY="your-provider-key"
export BROWSER_LLM_BASE_URL="https://api.openai.com/v1"
export BROWSER_LLM_MODEL="gpt-4.1-mini"
export BROWSER_LLM_TIMEOUT="30"
```

The provider must expose an OpenAI-compatible `/chat/completions` endpoint. TypeSafe is not required.

Examples:

```bash
# OpenRouter
export BROWSER_LLM_BASE_URL="https://openrouter.ai/api/v1"
export BROWSER_LLM_MODEL="provider/model"

# DeepSeek
export BROWSER_LLM_BASE_URL="https://api.deepseek.com/v1"
export BROWSER_LLM_MODEL="deepseek-chat"
```

Run the read-only Linux network baseline:

```bash
bash scripts/network_doctor.sh
```

This reports interfaces, routes, DNS configuration, proxy variables, and public DNS resolution. It does not change IP, DNS, MAC, firewall, or VPN state.

The optional mutation-capable tool is explicit and dry-run by default:

```bash
bash scripts/network_control.sh list
bash scripts/network_control.sh dns wlan0 1.1.1.1 1.0.0.1 --dry-run
sudo bash scripts/network_control.sh dns wlan0 1.1.1.1 1.0.0.1 --apply
sudo bash scripts/network_control.sh mac-random wlan0 --apply
sudo bash scripts/network_control.sh mac-restore wlan0 aa:bb:cc:dd:ee:ff --apply
sudo bash scripts/network_control.sh dhcp wlan0 --apply
```

Supported mutation categories are DNS set/reset, MAC randomize/restore, DHCP renewal, and NetworkManager static IPv4 configuration. Every mutation requires `--apply`, root privileges, and an interactive `APPLY` confirmation. Use a local console or an out-of-band recovery path before changing the active interface.

```bash
python toolkit.py scrape https://example.com
python -m py_compile web_scraper/*.py toolkit.py
pytest -q tests/test_unified_browser.py
```

## Optional features

Set `CAPTCHA_API_KEY` only for paid CAPTCHA providers. `SCRAPER_API_KEY` and `SCRAPER_BASE_URL` are used by the Google Maps integration. Keep `.env`, proxy lists, cookies, and generated output out of Git.
