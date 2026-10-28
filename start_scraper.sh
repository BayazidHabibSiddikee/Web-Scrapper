#!/bin/bash
cd /home/sword/Documents/web-scraper
while true; do
    echo "[$(date)] Starting scraper..."
    venv/bin/python go.py
    EXIT_CODE=$?
    echo "[$(date)] Scraper exited with code $EXIT_CODE"
    echo "[$(date)] Restarting in 30 seconds..."
    sleep 30
done
