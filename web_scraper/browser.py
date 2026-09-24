"""Constrained Playwright browser executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .snapshot import READ_STATE, snapshot_fingerprint


class BrowserError(RuntimeError):
    """A browser task could not be completed safely."""


@dataclass(frozen=True)
class BrowserTaskResult:
    ok: bool
    status: str
    goal: str
    steps: list[dict[str, Any]]
    page: dict[str, Any] | None = None
    error: str | None = None


class BrowserController:
    def __init__(self, headless: bool = True, max_steps: int = 30):
        self.headless = headless
        self.max_steps = max_steps
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self, url: str) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError("Browser control needs Playwright: pip install playwright") from exc
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(250)

    def observe(self) -> dict[str, Any]:
        if not self.page:
            raise BrowserError("Browser is not started")
        try:
            state = self.page.evaluate(READ_STATE)
            if state is None:
                raise BrowserError("Document is not ready")
            state["fingerprint"] = snapshot_fingerprint(state)
            return state
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError("Could not observe browser page") from exc

    def act(self, decision, expected_fingerprint: str | None = None, expected_action: dict[str, Any] | None = None) -> None:
        if not self.page:
            raise BrowserError("Browser is not started")
        if expected_fingerprint and self.observe().get("fingerprint") != expected_fingerprint:
            raise BrowserError("Page changed before browser action; observe again")
        op = decision.operation
        if op == "WAIT":
            self.page.wait_for_timeout(500)
        elif op == "SCROLL_DOWN":
            self.page.mouse.wheel(0, 560)
        elif op == "SCROLL_UP":
            self.page.mouse.wheel(0, -560)
        elif op == "DONE":
            return
        elif op == "BLOCKED":
            return
        else:
            action = next((a for a in self.observe().get("actions", []) if a["id"] == decision.target), None)
            if not action:
                raise BrowserError("Selected browser target is no longer available")
            if expected_action:
                expected_guard = expected_action.get("guard", {})
                actual_guard = action.get("guard", {})
                for key in ("label", "role", "value", "checked", "readOnly", "disabled", "expanded"):
                    if expected_guard.get(key) != actual_guard.get(key):
                        raise BrowserError("Target semantics changed before interaction")
            guard = self.page.evaluate(
                """nodeId => {
                  const e=window.__webScraper?.nodes.get(Number(nodeId));
                  if (!e || !e.isConnected || e.matches(':disabled,[aria-disabled="true"]') ||
                      e.closest('[inert],[aria-hidden="true"]') || !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
                  const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
                  if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
                  if (!e.contains(document.elementFromPoint(x,y))) return null;
                  if (e.readOnly || e.getAttribute('aria-readonly')==='true') return null;
                  return {x,y};
                }""",
                action.get("node"),
            )
            if not guard:
                raise BrowserError("Target is no longer safe to interact with")
            handle = self.page.evaluate_handle("(nodeId) => window.__webScraper?.nodes.get(Number(nodeId)) || null", action.get("node"))
            element = handle.as_element()
            if element is None:
                raise BrowserError("Target disappeared before interaction")
            if op == "TYPE_TEXT":
                element.fill(decision.text)
            elif op == "SELECT":
                element.select_option(label=decision.text)
            else:
                element.click(timeout=10000)
        self.page.wait_for_timeout(350)

    def close(self) -> None:
        for obj in (self.context, self.browser, self.playwright):
            try:
                if obj:
                    obj.stop() if obj is self.playwright else obj.close()
            except Exception:
                pass
        self.page = self.context = self.browser = self.playwright = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _noop() -> None:
    """Keep module importable when Playwright is not installed."""


__all__ = ["BrowserError", "BrowserController", "BrowserTaskResult", "_noop"]
