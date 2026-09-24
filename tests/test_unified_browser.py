"""Offline contracts for the unified browser policy."""

import pytest

from web_scraper.browser import BrowserError
from web_scraper.policy import BrowserDecision, _validate


def test_rejects_unknown_operation():
    with pytest.raises(BrowserError, match="Unsupported"):
        _validate({"operation": "EXECUTE_SCRIPT"}, {})


def test_rejects_unknown_target():
    with pytest.raises(BrowserError, match="unknown target"):
        _validate({"operation": "CLICK", "target": "e99", "confidence": 1}, {})


def test_rejects_text_operation_without_value():
    with pytest.raises(BrowserError, match="TYPE_TEXT"):
        _validate({"operation": "TYPE_TEXT", "target": "e1", "confidence": 1}, {"e1": {}})


def test_select_requires_option_text():
    with pytest.raises(BrowserError, match="SELECT"):
        _validate({"operation": "SELECT", "target": "e1", "confidence": 1}, {"e1": {"kind": "select"}})


def test_accepts_valid_decision():
    result = _validate({"operation": "CLICK", "target": "e1", "confidence": 0.8}, {"e1": {}})
    assert isinstance(result, BrowserDecision)
    assert result.target == "e1"


def test_should_render_detects_spa_shell():
    from web_scraper.scraper import _should_render

    assert _should_render("<html><body>Loading</body></html>")
    assert not _should_render("<html><body>" + ("real content " * 200) + "</body></html>")


def test_should_render_is_opt_in_for_http_only_mode():
    from web_scraper.scraper import scrape_web

    result = scrape_web("https://example.com", render_js=False)
    assert result.ok
    assert result.backend == "httpx"
