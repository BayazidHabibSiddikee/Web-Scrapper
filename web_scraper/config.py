"""Environment-backed configuration for the unified toolkit."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            api_key=os.getenv("BROWSER_LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            base_url=os.getenv("BROWSER_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            model=os.getenv("BROWSER_LLM_MODEL", "gpt-4.1-mini"),
            timeout=float(os.getenv("BROWSER_LLM_TIMEOUT", "30")),
        )


__all__ = ["LLMConfig"]
