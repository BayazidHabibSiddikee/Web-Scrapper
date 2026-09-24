# One command with two workflows: extraction or browser control.
from __future__ import annotations

import argparse
import json
import sys

from web_scraper import browser_task, scrape_web


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified scraper and AI browser")
    sub = parser.add_subparsers(dest="command", required=True)
    scrape = sub.add_parser("scrape", help="retrieve and extract a page")
    scrape.add_argument("url")
    scrape.add_argument("--max-chars", type=int, default=20000)
    browser = sub.add_parser("browser", help="run a natural-language browser task")
    browser.add_argument("url")
    browser.add_argument("goal")
    browser.add_argument("--headed", action="store_true")
    browser.add_argument("--max-steps", type=int, default=30)
    args = parser.parse_args()
    if args.command == "scrape":
        result = scrape_web(args.url, max_chars=args.max_chars)
    else:
        result = browser_task(args.url, args.goal, headless=not args.headed, max_steps=args.max_steps)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
