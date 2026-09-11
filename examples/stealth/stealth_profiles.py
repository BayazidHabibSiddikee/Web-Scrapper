"""
stealth_profiles.py
=================
Pre-built stealth profiles for different scraping contexts.

Profiles:
  - cloudflare  : Maximum stealth for Cloudflare-protected sites
  - bot_detected: Moderate stealth for sites with basic bot checks
  - stealth_max : Paranoid mode — full fingerprint masking
  - crawler     : Politeness-first for bulk crawling
  - screenshot  : Optimized for screenshot capture

Usage:
    from stealth_profiles import get_profile
    profile = get_profile("cloudflare")
    # profile["selenium_options"], profile["playwright_context"], etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class StealthProfile:
    name: str
    description: str
    headless: bool = True
    user_agent: str = ""
    window_size: Tuple[int, int] = (1920, 1080)
    locale: str = "en-US"
    timezone: str = "America/New_York"
    geolocation: Optional[Dict] = None
    selenium_args: List[str] = field(default_factory=list)
    selenium_prefs: Dict[str, any] = field(default_factory=dict)
    playwright_args: List[str] = field(default_factory=list)
    playwright_init_script: str = ""
    extra_wait_seconds: float = 3.0
    scroll_to_bottom: bool = False
    click_cookies: bool = True
    notes: str = ""


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

PROFILES: Dict[str, StealthProfile] = {}


def register_profile(profile: StealthProfile) -> None:
    PROFILES[profile.name] = profile


def get_profile(name: str) -> StealthProfile:
    if name not in PROFILES:
        raise KeyError(f"Unknown stealth profile: {name}. Available: {list(PROFILES.keys())}")
    return PROFILES[name]


def list_profiles() -> List[str]:
    return list(PROFILES.keys())


# ---------------------------------------------------------------------------
# Cloudflare (Camoufox-first)
# ---------------------------------------------------------------------------

register_profile(StealthProfile(
    name="cloudflare",
    description="Best for Cloudflare-protected sites. Uses Camoufox stealth Firefox.",
    headless=True,
    user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    window_size=(1920, 1080),
    locale="en-US",
    timezone="America/New_York",
    extra_wait_seconds=5.0,
    scroll_to_bottom=False,
    click_cookies=True,
    selenium_args=[
        "--headless",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ],
    playwright_init_script="""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
    """,
    notes="Use Camoufox as primary; Playwright/Chromium as fallback. "
          "Increase extra_wait_seconds if Cloudflare challenge persists.",
))

# ---------------------------------------------------------------------------
# Bot-detected (moderate)
# ---------------------------------------------------------------------------

register_profile(StealthProfile(
    name="bot_detected",
    description="Sites with basic bot detection. Use undetected-chromedriver or Playwright.",
    headless=True,
    user_agent=(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    window_size=(1440, 900),
    locale="en-US",
    timezone="America/Los_Angeles",
    extra_wait_seconds=3.0,
    scroll_to_bottom=True,
    click_cookies=True,
    selenium_args=[
        "--headless=new",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ],
    playwright_init_script="""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
    """,
    notes="undetected-chromedriver recommended. "
          "Disable headless if detection persists — stock headless is very detectable.",
))

# ---------------------------------------------------------------------------
# Paranoid / maximum stealth
# ---------------------------------------------------------------------------

register_profile(StealthProfile(
    name="stealth_max",
    description="Maximum fingerprint masking. For high-security targets.",
    headless=True,
    user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    window_size=(1920, 1080),
    locale="en-US",
    timezone="America/New_York",
    geolocation={"latitude": 40.7128, "longitude": -74.0060},
    extra_wait_seconds=8.0,
    scroll_to_bottom=False,
    click_cookies=True,
    selenium_args=[
        "--headless",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-popup-blocking",
        "--disable-translate",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ],
    selenium_prefs={
        "dom.webnotifications.enabled": False,
        "media.autoplay.default": 0,
        "browser.cache.disk.enable": False,
        "browser.cache.memory.enable": False,
        "dom.enable_resource_timing": False,
        "dom.enable_user_timing": False,
        "general.useragent.override": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    },
    playwright_args=[
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
    ],
    playwright_init_script="""
        // Mask webdriver
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        // Spoof plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        // Spoof languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        // Spoof chrome
        window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
        // Mask permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        // Spoof WebGL vendor/renderer
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris Xe Graphics';
            return getParameter.apply(this, [parameter]);
        };
        // Spoof hardware concurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8
        });
        // Spoof device memory
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8
        });
        // Mask canvas fingerprint
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function() {
            if (this.width > 1 && this.height > 1) {
                const ctx = this.getContext('2d');
                const imageData = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < imageData.data.length; i += 4) {
                    imageData.data[i] += Math.random() * 2 - 1;
                }
                ctx.putImageData(imageData, 0, 0);
            }
            return originalToDataURL.apply(this, arguments);
        };
    """,
    notes="Combines Camoufox + Playwright/Chromium stealth patches. "
          "For highest assurance, run Camoufox FIRST and fall back to "
          "Playwright only if Camoufox fails.",
))

# ---------------------------------------------------------------------------
# Crawler (polite)
# ---------------------------------------------------------------------------

register_profile(StealthProfile(
    name="crawler",
    description="Polite bulk crawler. Lower stealth, respects rate limits.",
    headless=True,
    user_agent="ScrapyKit/1.0 (+https://example.com/bot)",
    window_size=(1920, 1080),
    locale="en-US",
    timezone="UTC",
    extra_wait_seconds=1.0,
    scroll_to_bottom=True,
    click_cookies=True,
    selenium_args=["--headless", "--no-sandbox"],
    playwright_init_script="""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
    """,
    notes="Use for large-scale crawls. Add robots.txt respect and "
          "rate limiting. 1s+ delay between requests recommended.",
))

# ---------------------------------------------------------------------------
# Screenshot-optimized
# ---------------------------------------------------------------------------

register_profile(StealthProfile(
    name="screenshot",
    description="Optimized for screenshot capture. Full page, high quality.",
    headless=True,
    user_agent=(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    window_size=(1920, 1080),
    locale="en-US",
    timezone="America/New_York",
    extra_wait_seconds=3.0,
    scroll_to_bottom=True,
    click_cookies=True,
    selenium_args=["--headless=new", "--no-sandbox"],
    playwright_args=["--disable-blink-features=AutomationControlled"],
    playwright_init_script="""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
    """,
    notes="Use full_page=True for complete screenshots. "
          "For element screenshots, target selector before capture.",
))


# ---------------------------------------------------------------------------
# Quick apply helpers
# ---------------------------------------------------------------------------

def apply_to_selenium(profile_name: str, options) -> None:
    """Apply a stealth profile to a Selenium options object."""
    profile = get_profile(profile_name)
    for arg in profile.selenium_args:
        options.add_argument(arg)
    for key, val in profile.selenium_prefs.items():
        options.set_preference(key, val)
    if profile.user_agent:
        options.set_preference("general.useragent.override", profile.user_agent)


def apply_to_playwright_context(profile_name: str, context_kwargs: dict) -> str:
    """Apply a stealth profile to Playwright context kwargs.
    Returns the init_script to pass to add_init_script."""
    profile = get_profile(profile_name)
    context_kwargs.setdefault("viewport", {
        "width": profile.window_size[0],
        "height": profile.window_size[1],
    })
    context_kwargs.setdefault("user_agent", profile.user_agent)
    context_kwargs.setdefault("locale", profile.locale)
    context_kwargs.setdefault("timezone_id", profile.timezone)
    if profile.geolocation:
        context_kwargs["geolocation"] = profile.geolocation
        context_kwargs["permissions"] = ["geolocation"]
    return profile.playwright_init_script


if __name__ == "__main__":
    print("Available stealth profiles:")
    for name in list_profiles():
        p = get_profile(name)
        print(f"\n  {name}: {p.description}")
        print(f"    Headless: {p.headless}")
        print(f"    UA: {p.user_agent[:60]}...")
        print(f"    Extra wait: {p.extra_wait_seconds}s")
