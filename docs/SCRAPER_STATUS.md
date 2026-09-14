# MedEx Brand Detail Fill — Status

## What's running
- **PID**: 617743 (launched 2026-09-14 ~04:49 UTC)
- **Command**: `venv/bin/python medex_scraper2.py details --delay 0.4`
- **Log**: `output/medex_run.log`

## Target
- **25,354** brands total
- **403** already filled before this run (preserved)
- **~24,951** pending

## Expected duration
At 0.4s/page with proxy rotation (30 new IPs), ~1-2 pages/sec average
= **~3-7 hours** for full fill. If site re-throttles, it may take longer
but will continue (each page uses a fresh browser + rotated proxy).

## To check progress
```bash
cd /home/sword/Documents/web-scraper
tail -f output/medex_run.log       # live progress
make status                        # JSON summary
# or:
python3 -c "import json; d=json.load(open('/home/sword/Downloads/scraper/output/brand_details.json')); print(f'{len(d)} rows')"
```

## How to stop safely
```bash
kill 617743   # saves current state, resumes next run
```

## To resume later (after stopping/reboot)
```bash
cd web-scraper && make detail-detail
# (it reads existing brand_details.json and skips already-done IDs)
```
