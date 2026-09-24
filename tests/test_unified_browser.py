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


def test_accepts_valid_decision():
    result = _validate({"operation": "CLICK", "target": "e1", "confidence": 0.8}, {"e1": {}})
    assert isinstance(result, BrowserDecision)
    assert result.target == "e1"
