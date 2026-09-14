#!/bin/bash
while true; do
  rows=$(python3 -c "import json; print(len(json.load(open('/home/sword/Downloads/scraper/output/brand_details.json'))))" 2>/dev/null || echo "?")
  pid=$(pgrep -f medex_async.py | head -1)
  if [ -z "$pid" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] PROCESS ENDED - Rows: $rows"
    exit 0
  fi
  echo "[$(date '+%H:%M:%S')] Rows: $rows | PID: $pid"
  sleep 300  # Check every 5 minutes
done
