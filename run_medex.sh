#!/bin/bash
cd "$(dirname "$0")"
echo "Launching medex fill ($(wc -l < /home/sword/Downloads/scraper/output/brands.json | tr -d ' ') brands total)..."
nohup venv/bin/python medex_scraper.py details --delay 1.0 --no-proxy \
  --brands-file /home/sword/Downloads/scraper/output/brands.json \
  --details-file /home/sword/Downloads/scraper/output/brand_details.json \
  --failed-file /home/sword/Downloads/scraper/output/brand_details_failed.json \
  > output/medex_full_run.log 2>&1 &
echo "PID=$!  tail -f output/medex_full_run.log"
