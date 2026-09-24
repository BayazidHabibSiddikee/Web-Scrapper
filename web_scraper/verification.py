"""Conservative, evidence-based verification for browser task completion."""

from __future__ import annotations

import re
from typing import Any

STOP_WORDS = {
    "a", "an", "and", "at", "be", "by", "find", "for", "from", "in", "into", "is", "it",
    "of", "on", "open", "or", "page", "the", "then", "this", "to", "with", "click", "go",
}


def verify_goal(goal: str, page: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Return whether visible page evidence contains the goal's distinctive terms.

    This is intentionally conservative. A model saying DONE is not evidence; the final
    URL, title, visible text, and current control labels must contain the goal terms.
    """
    terms = [term for term in re.findall(r"[a-z0-9]+", goal.lower()) if len(term) > 2 and term not in STOP_WORDS]
    haystack = " ".join(str(page.get(key, "")) for key in ("url", "title", "text")).lower()
    labels = " ".join(str(a.get("label", "")) for a in page.get("actions", [])).lower()
    evidence = {term: (term in haystack or term in labels) for term in terms}
    required = list(dict.fromkeys(terms))
    satisfied = bool(required) and all(evidence[term] for term in required)
    return satisfied, {"terms": terms, "evidence": evidence, "source": "visible_page"}


__all__ = ["verify_goal"]
