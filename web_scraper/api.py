"""High-level unified operations for the toolkit."""

from __future__ import annotations

from typing import Any

from .browser import BrowserController, BrowserError, BrowserTaskResult
from .policy import choose
from .scraper import ScrapeResult, scrape_web


def browser_task(url: str, goal: str, *, headless: bool = True, max_steps: int = 30) -> BrowserTaskResult:
    goal = goal.strip()
    if not url or not goal:
        return BrowserTaskResult(False, "error", goal, [], error="url and goal are required")
    controller = BrowserController(headless=headless, max_steps=max_steps)
    history: list[dict[str, Any]] = []
    try:
        controller.start(url)
        for _ in range(max_steps):
            page = controller.observe()
            decision = choose(goal, page, history)
            history.append({"operation": decision.operation, "target": decision.target, "confidence": decision.confidence})
            if decision.operation in {"DONE", "BLOCKED"}:
                return BrowserTaskResult(True, decision.operation.lower(), goal, history, page)
            before_fingerprint = page.get("fingerprint")
            controller.act(decision, before_fingerprint)
            after_page = controller.observe()
            changed = after_page.get("fingerprint") != before_fingerprint
            history[-1]["page_changed"] = changed
            if len(history) >= 3 and not any(h.get("page_changed", True) for h in history[-3:]) and all(h["operation"] != "WAIT" for h in history[-3:]):
                return BrowserTaskResult(False, "blocked", goal, history, after_page, "Browser made no observable progress")
        return BrowserTaskResult(False, "blocked", goal, history, controller.observe(), "Browser step budget exhausted")
    except (BrowserError, Exception) as exc:
        return BrowserTaskResult(False, "error", goal, history, error=str(exc))
    finally:
        controller.close()


def unified_scrape(url: str, **kwargs: Any) -> ScrapeResult:
    return scrape_web(url, **kwargs)


__all__ = ["browser_task", "unified_scrape", "scrape_web"]
