#!/bin/bash
# MedEx Scraper Daemon - Runs until all brands done or manual stop
LOGDIR="/home/sword/Documents/web-scraper/output"
LOGFILE="$LOGDIR/medex_daemon.log"
STATE_FILE="/home/sword/Downloads/scraper/output/checkpoint.json"

mkdir -p "$LOGDIR"

echo "[$(date)] Starting MedEx Scraper Daemon..." >> "$LOGFILE"

while true; do
  # Check if process already running
  if pgrep -f medex_async.py > /dev/null; then
    echo "[$(date)] Scraper running (PID: $(pgrep -f medex_async.py))" >> "$LOGFILE"
  else
    echo "[$(date)] Restarting scraper..." >> "$LOGFILE"
    cd /home/sword/Documents/web-scraper
    venv/bin/python medex_async.py >> "$LOGFILE" 2>&1 &
    sleep 5
  fi
  
  # Check completion
  if [ -f "$STATE_FILE" ]; then
    done=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(len(d.get('done',[])))" 2>/dev/null || echo "0")
    total=$(wc -l < /home/sword/Downloads/scraper/output/brands.json 2>/dev/null || echo "0")
    if [ "$done" -ge "$((total-5))" ]; then
      echo "[$(date)] COMPLETED! $done/$total brands scraped." >> "$LOGFILE"
      break
    fi
  fi
  
  # Sleep 10 minutes before next check
  sleep 600
done
