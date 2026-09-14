# How to Complete MedEx Scraping (25,354 brands)

## Current Status
- ✅ 720 brands scraped successfully
- ⏸️ Blocked by Cloudflare captcha after ~700 requests
- 💾 Data saved at: `/home/sword/Downloads/scraper/output/brand_details.json`

## Option 1: Use 2Captcha (Recommended - Fast)

1. **Get API Key**:
   - Go to https://2captcha.com/inpage
   - Sign up (free credit for new users)
   - Add $5-10 credit (~2000 captcha solves)

2. **Run the scraper**:
   ```bash
   cd /home/sword/Documents/web-scraper
   export 2CAPTCHA_API_KEY="your_key_here"
   venv/bin/python medex_with_2captcha.py
   ```

3. **Expected time**: ~4-6 hours for full 25k brands

## Option 2: Wait and Resume (Free but Slow)

Cloudflare bans typically expire in 30min-24hrs. The scraper will:
- Save progress every 10 brands
- Resume from where it left off
- Just re-run and wait for bans to expire

```bash
cd /home/sword/Documents/web-scraper
venv/bin/python medex_async.py
```

## Option 3: Residential Proxies (Fastest)

Services like BrightData, Oxylabs, or SmartProxy ($10-30/mo):
- Rotate IPs automatically
- No captcha issues
- Much faster completion

## Data Already Collected

| Field | Coverage |
|-------|----------|
| brandName | 78% (566/720) |
| strength | 95% (687/720) |
| indications | 99% (715/720) |
| dosage | 98% (707/720) |
| pharmacology | 94% (683/720) |
| sideEffects | 97% (700/720) |
| therapeuticClass | 93% (676/720) |
| unitPrice | 67% (487/720) |

## Files Created

- `medex_with_2captcha.py` - Main scraper with captcha solving
- `medex_async.py` - Async version without captcha (for when IP is clean)
- `config/proxies.txt` - Proxy list template

## Need Help?

The script will:
1. Load existing progress (won't redo work)
2. Solve captchas automatically with 2Captcha
3. Save every 10 brands
4. Show progress and ETA
