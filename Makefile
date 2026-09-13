# Quick commands for the web-scraper toolkit
PYTHON := venv/bin/python
.PHONY: help list details detail-detail detail-session detail-pilot \
        captcha-test maps-setup clear-output install check docker build run-detached status

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

list: ## Show available tools
	$(PYTHON) agent_tools.py --list

detail: ## Scrape one brand detail page
	$(PYTHON) medex_scraper.py details --limit 1 --brands-file /home/sword/Downloads/scraper/output/brands.json --details-file /home/sword/Downloads/scraper/output/brand_details.json --failed-file /home/sword/Downloads/scraper/output/brand_details_failed.json --mode session --delay 1.5

detail-session: ## Medium pilot with persistent stealth browser (100 pages)
	$(PYTHON) medex_scraper.py details --mode session --limit 100 --delay 1.5 --brands-file /home/sword/Downloads/scraper/output/brands.json --details-file /home/sword/Downloads/scraper/output/brand_details.json --failed-file /home/sword/Downloads/scraper/output/brand_details_failed.json

detail-pilot: ## One-page pilot
	$(PYTHON) medex_scraper.py details --mode session --limit 1 --delay 1.5 --brands-file /home/sword/Downloads/scraper/output/brands.json --details-file /home/sword/Downloads/scraper/output/brand_details.json --failed-file /home/sword/Downloads/scraper/output/brand_details_failed.json

detail-detail: ## Full fill (all brands; runs ~10-20h at 1 req/sec)
	$(PYTHON) medex_scraper.py details --mode session --delay 1.5 --headful --brands-file /home/sword/Downloads/scraper/output/brands.json --details-file /home/sword/Downloads/scraper/output/brand_details.json --failed-file /home/sword/Downloads/scraper/output/brand_details_failed.json

captcha-test: ## Test captcha detection on a URL
	$(PYTHON) captcha_flow.py https://www.google.com/recaptcha/api2/demo --detect-only

maps-setup: ## Start the Google Maps scraper API
	docker compose -f maps.compose.yml up -d
	@echo "API ready at http://localhost:8080"

clear-output: ## Clear existing output files (keeps backups)
	@echo "Backing up..."
	@test -f output/medex/brand_details.json && mv output/medex/brand_details.json{,.bak-$$(date +%Y%m%d-%H%M)} || true
	@find . -name "*.bak-*" -mtime +7 -delete
	@echo "Done (old backups older than 7 days pruned)"

install: ## Install deps into venv
	@source venv/bin/activate && pip install -r requirements.txt && playwright install chromium

check: ## Compile-check all modules
	$(PYTHON) -m py_compile agent_tools.py mcp_server.py captcha_flow.py cookies.py form_fill.py maps_scraper.py scrapling_backend.py medex_scraper.py scraper.py master_pipeline.py && echo OK

docker-build: ## Build the production image
	docker build -t web-scraper .

run-detached: ## Long-running brand detail fill (recommended for production)
	nohup venv/bin/python medex_scraper.py details \
	  --mode session --delay 1.5 \
	  --brands-file /home/sword/Downloads/scraper/output/brands.json \
	  --details-file /home/sword/Downloads/scraper/output/brand_details.json \
	  --failed-file /home/sword/Downloads/scraper/output/brand_details_failed.json \
	  > output/medex_full_run.log 2>&1 &
	@echo "PID=$$! log=output/medex_full_run.log"

status: ## Check current state of medex brand_details
	@$(PYTHON) -c "import json; d=json.load(open('/home/sword/Downloads/scraper/output/brand_details.json')); b=json.load(open('/home/sword/Downloads/scraper/output/brands.json')); f=json.load(open('/home/sword/Downloads/scraper/output/brand_details_failed.json')); print(json.dumps({'total_brands':len(b),'detail_rows':len(d),'pending':len(b)-len(d),'failed':len(f),'fields':{'indications':sum(1 for x in d if x.get('indications')),'overdoseEffects':sum(1 for x in d if x.get('overdoseEffects')),'unitPrice':sum(1 for x in d if x.get('unitPrice'))}},indent=2))"
