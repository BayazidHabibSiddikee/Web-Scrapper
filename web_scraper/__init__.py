"""Package marker for the unified toolkit."""

"""Unified scraping and browser-agent API for the web-scraper toolkit."""

from .api import browser_task, scrape_web
from .browser import BrowserTaskResult
from .exporters import export_result
from .scraper import ScrapeResult

__all__ = ["ScrapeResult", "BrowserTaskResult", "browser_task", "scrape_web", "export_result"]
