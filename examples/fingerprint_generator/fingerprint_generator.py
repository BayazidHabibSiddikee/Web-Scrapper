"""
fingerprint_generator.py
======================
Generate realistic browser fingerprints for stealth sessions.

Fingerprints generated:
  - Canvas / WebGL / Audio  — noise-injected, deterministic per session
  - Screen / Viewport        — realistic resolutions + devicePixelRatio
  - Navigator plugins        — plugin list, mimeTypes, languages
  - WebRTC leak prevention   — mDNS IDs, consistent IP
  - Font enumeration         — common font list
  - Hardware concurrency     — realistic CPU core count
  - Device memory            — realistic RAM hint
  - Touch support            — desktop vs mobile
  - Timezone / locale        — consistent profile

Output formats:
  - Playwright context kwargs
  - Selenium options prefs
  - CDP session commands (Playwright)
  - Standalone init_script (Playwright add_init_script)

Usage:
    from fingerprint_generator import FingerprintProfile, apply_to_playwright, apply_to_selenium

    fp = FingerprintProfile(platform="windows", browser="chrome", locale="en-US")
    ctx_kwargs = apply_to_playwright(fp)
    init_script = fp.playwright_init_script()
"""

from __future__ import annotations

import json
import logging
import random
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Platform(str, Enum):
    WINDOWS = "windows"
    MAC = "mac"
    LINUX = "linux"


class BrowserType(str, Enum):
    CHROME = "chrome"
    FIREFOX = "firefox"
    SAFARI = "safari"
    EDGE = "edge"


@dataclass
class FingerprintProfile:
    """
    Deterministic browser fingerprint profile.
    All randomized values are generated once at init time.
    """
    platform: Platform = Platform.WINDOWS
    browser: BrowserType = BrowserType.CHROME
    locale: str = "en-US"
    timezone: str = "America/New_York"
    screen_width: int = 1920
    screen_height: int = 1080
    device_pixel_ratio: float = 1.0
    color_depth: int = 24
    hardware_concurrency: int = 8
    device_memory_gb: int = 8
    touch_support: bool = False
    plugins: List[str] = field(default_factory=lambda: [
        "Chrome PDF Plugin", "Chrome PDF Viewer", "Native Client",
        "Widevine Content Decryption Module", "Microsoft Edge PDF Viewer",
    ])
    mime_types: List[str] = field(default_factory=lambda: [
        "application/pdf", "application/x-google-chrome-pdf",
        "application/vnd.chromium.remoting-viewer",
    ])
    languages: List[str] = field(default_factory=lambda: ["en-US", "en"])
    fonts: List[str] = field(default_factory=lambda: [
        "Arial", "Arial Black", "Calibri", "Cambria", "Comic Sans MS",
        "Consolas", "Courier New", "Georgia", "Impact", "Liberation Serif",
        "Linux Libertine", "Microsoft Sans Serif", "Monaco", "Segoe UI",
        "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana",
    ])
    webgl_vendor: str = "Intel Inc."
    webgl_renderer: str = "Intel Iris Xe Graphics"
    canvas_noise: bool = True
    audio_noise: bool = True
    session_seed: Optional[int] = None

    def __post_init__(self):
        if self.session_seed is None:
            self.session_seed = secrets.randbits(32)
        # Adjust defaults per platform
        if self.platform == Platform.MAC:
            self.screen_width, self.screen_height = 2560, 1600
            self.device_pixel_ratio = 2.0
            self.hardware_concurrency = 10
            self.device_memory_gb = 16
            self.plugins = ["PDFKit", "WebKit built-in PDF", "QuickTime Plugin"]
            self.webgl_vendor = "Apple Inc."
            self.webgl_renderer = "Apple M2 Pro"
        elif self.platform == Platform.LINUX:
            self.screen_width, self.screen_height = 1920, 1080
            self.device_pixel_ratio = 1.0
            self.hardware_concurrency = 8
            self.device_memory_gb = 8
            self.plugins = [
                "Chrome PDF Plugin", "Widevine Content Decryption Module",
                "Chrome Media Router",
            ]

    # ------------------------------------------------------------------
    # Noise injection (deterministic per session)
    # ------------------------------------------------------------------

    def _noise_value(self, offset: int = 0) -> float:
        """Deterministic pseudo-random float in [-1, 1]."""
        val = self.session_seed + offset
        return ((val * 9301 + 49297) % 233280) / 233280.0 * 2.0 - 1.0

    # ------------------------------------------------------------------
    # Playwright context kwargs
    # ------------------------------------------------------------------

    def playwright_context_kwargs(self) -> Dict[str, Any]:
        """Return kwargs for browser.new_context(**kwargs)."""
        kwargs = {
            "viewport": {
                "width": self.screen_width,
                "height": self.screen_height,
            },
            "device_scale_factor": self.device_pixel_ratio,
            "locale": self.locale,
            "timezone_id": self.timezone,
            "user_agent": self.user_agent(),
            "has_touch": self.touch_support,
            "color_scheme": "light",
            "reduced_motion": "no-preference",
            "forced_colors": "none",
        }
        if self.platform == Platform.MAC:
            kwargs["geolocation"] = {
                "latitude": 37.7749 + self._noise_value(1) * 0.01,
                "longitude": -122.4194 + self._noise_value(2) * 0.01,
            }
            kwargs["permissions"] = ["geolocation"]
        return kwargs

    def user_agent(self) -> str:
        """Generate a realistic user agent string."""
        ua_map = {
            (Platform.WINDOWS, BrowserType.CHROME): (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/126.0.0.0 Safari/537.36"
            ),
            (Platform.WINDOWS, BrowserType.EDGE): (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
            ),
            (Platform.MAC, BrowserType.CHROME): (
                f"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/126.0.0.0 Safari/537.36"
            ),
            (Platform.MAC, BrowserType.SAFARI): (
                f"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
                f"AppleWebKit/605.1.15 (KHTML, like Gecko) "
                f"Version/17.4 Safari/605.1.15"
            ),
            (Platform.LINUX, BrowserType.CHROME): (
                f"Mozilla/5.0 (X11; Linux x86_64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/126.0.0.0 Safari/537.36"
            ),
            (Platform.LINUX, BrowserType.FIREFOX): (
                f"Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                f"Gecko/20100101 Firefox/128.0"
            ),
        }
        return ua_map.get((self.platform, self.browser), ua_map[(Platform.WINDOWS, BrowserType.CHROME)])

    # ------------------------------------------------------------------
    # Playwright init script (fingerprint masking)
    # ------------------------------------------------------------------

    def playwright_init_script(self) -> str:
        """Generate a JavaScript init script to mask automation fingerprints."""
        # Canvas noise
        canvas_script = ""
        if self.canvas_noise:
            canvas_script = """
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(...args) {
                    const ctx = this.getContext('2d');
                    const imgData = ctx ? ctx.getImageData(0, 0, this.width, this.height) : null;
                    if (imgData) {
                        for (let i = 0; i < imgData.data.length; i += 4) {
                            imgData.data[i] += arguments[1] ? 0 : (Math.random() * 2 - 1);
                        }
                        ctx.putImageData(imgData, 0, 0);
                    }
                    return origToDataURL.apply(this, args);
                };
            """

        # Audio noise
        audio_script = ""
        if self.audio_noise:
            audio_script = """
                const origAnalyser = AudioContext.prototype.createAnalyser;
                AudioContext.prototype.createAnalyser = function() {
                    const analyser = origAnalyser.call(this);
                    const origGetFloat = Float32Array.prototype.slice;
                    Object.defineProperty(analyser, 'frequencyBinCount', {
                        get: () => analyser.frequencyBinCount + (Math.random() * 2 - 1)
                    });
                    return analyser;
                };
            """

        return f"""
            // ── Navigator spoofing ──────────────────────────────
            Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
            Object.defineProperty(navigator, 'plugins', {{
                get: () => [{", ".join(f'{{name: "{p}", filename: "{p}.dll"}}' for p in self.plugins[:3])}]
            }});
            Object.defineProperty(navigator, 'languages', {{
                get: () => {json.dumps(self.languages)}
            }});
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {self.hardware_concurrency}
            }});
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {self.device_memory_gb}
            }});
            Object.defineProperty(navigator, 'maxTouchPoints', {{
                get: () => {1 if self.touch_support else 0}
            }});
            window.chrome = {{ runtime: {{}} }};

            // ── Screen spoofing ──────────────────────────────────
            Object.defineProperty(screen, 'width', {{ get: () => {self.screen_width} }});
            Object.defineProperty(screen, 'height', {{ get: () => {self.screen_height} }});
            Object.defineProperty(screen, 'availWidth', {{ get: () => {self.screen_width} }});
            Object.defineProperty(screen, 'availHeight', {{ get: () => {self.screen_height - 40} }});
            Object.defineProperty(screen, 'colorDepth', {{ get: () => {self.color_depth} }});
            Object.defineProperty(screen, 'pixelDepth', {{ get: () => {self.color_depth} }});

            // ── WebGL spoofing ───────────────────────────────────
            const getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) return '{self.webgl_vendor}';
                if (parameter === 37446) return '{self.webgl_renderer}';
                return getParam.apply(this, [parameter]);
            }};

            // ── Permission API masking ───────────────────────────
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({{ state: Notification.permission }})
                    : origQuery(parameters)
            );

            // ── Canvas noise ─────────────────────────────────────
            {canvas_script}

            // ── Audio noise ──────────────────────────────────────
            {audio_script}

            // ── WebRTC mDNS mask ────────────────────────────────
            const origCreateOffer = RTCPeerConnection.prototype.createOffer;
            RTCPeerConnection.prototype.createOffer = function(...args) {{
                return origCreateOffer.apply(this, args).then(offer => {{
                    if (offer.sdp) {{
                        offer.sdp = offer.sdp.replace(
                            /[a-f0-9]{{32}}-md\.local/g,
                            '{self.session_seed:08x}.local'
                        );
                    }}
                    return offer;
                }});
            }};
        """

    # ------------------------------------------------------------------
    # Selenium preferences
    # ------------------------------------------------------------------

    def selenium_prefs(self, browser: str = "chrome") -> Dict[str, Any]:
        """Return Selenium option preferences."""
        prefs = {
            "general.useragent.override": self.user_agent(),
            "dom.webnotifications.enabled": False,
            "media.autoplay.default": 0,
            "browser.cache.disk.enable": False,
            "browser.cache.memory.enable": False,
        }
        if browser == "firefox":
            prefs["webgl.disabled"] = False
            prefs["gfx.canvas.azure.accelerated"] = True
        return prefs


# ---------------------------------------------------------------------------
# Helper: apply to Playwright / Selenium
# ---------------------------------------------------------------------------

def apply_to_playwright(profile: FingerprintProfile) -> Dict[str, Any]:
    """Return Playwright browser.new_context() kwargs + init_script."""
    return {
        "kwargs": profile.playwright_context_kwargs(),
        "init_script": profile.playwright_init_script(),
    }


def apply_to_selenium(profile: FingerprintProfile, options) -> None:
    """Apply fingerprint prefs to a Selenium options object."""
    prefs = profile.selenium_prefs()
    for key, val in prefs.items():
        try:
            options.set_preference(key, val)
        except Exception:
            pass
    for arg in [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]:
        options.add_argument(arg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Fingerprint profile generator")
    parser.add_argument("--platform", choices=["windows", "mac", "linux"], default="windows")
    parser.add_argument("--browser", choices=["chrome", "firefox", "safari", "edge"], default="chrome")
    parser.add_argument("--locale", default="en-US")
    args = parser.parse_args()

    fp = FingerprintProfile(
        platform=Platform(args.platform),
        browser=BrowserType(args.browser),
        locale=args.locale,
    )

    print(f"User-Agent: {fp.user_agent()}")
    print(f"Viewport  : {fp.screen_width}x{fp.screen_height} @ {fp.device_pixel_ratio}x")
    print(f"Platform  : {fp.platform.value} / {fp.browser.value}")

    ctx = apply_to_playwright(fp)
    print(f"\nPlaywright context kwargs:")
    print(json.dumps(ctx["kwargs"], indent=2, default=str))
    print(f"\nInit script length: {len(ctx['init_script'])} chars")
