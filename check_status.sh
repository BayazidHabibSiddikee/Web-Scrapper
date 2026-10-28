#!/bin/bash
echo "=== MedEx Scraper Status ==="
if pgrep -f medex_persistent > /dev/null; then
    PID=$(pgrep -f medex_persistent)
    echo "✓ Running (PID: $PID)"
else
    echo "✗ Not running"
fi
python3 -c "import json; d=json.load(open('/home/sword/Downloads/scraper/output/brand_details.json')); print(f'Scraped: {len(d)}/25,354 ({len(d)*100//25354}%)')" 2>/dev/null
echo ""
tail -3 /home/sword/Documents/web-scraper/output/medex_persistent.log 2>/dev/null | grep -v "WARNING\|INFO" | tail -1
