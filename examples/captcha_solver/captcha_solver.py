"""
captcha_solver.py
===============
CAPTCHA solving integration for:
  - Image CAPTCHAs (text recognition)
  - reCAPTCHA v2 / v3
  - hCaptcha
  - Cloudflare Turnstile

Supported services:
  - 2captcha (recommended, wide coverage)
  - anti-captcha.com
  - capsolver.com

Usage:
    from captcha_solver import CaptchaSolver

    solver = CaptchaSolver(service="2captcha", api_key="YOUR_KEY")

    # Image CAPTCHA
    text = solver.solve_image("captcha.png")

    # reCAPTCHA v2
    token = solver.solve_recaptcha_v2(site_key="SITE_KEY", page_url="https://example.com")

    # reCAPTCHA v3
    token = solver.solve_recaptcha_v3(site_key="SITE_KEY", page_url="https://example.com", action="login")

    # hCaptcha
    token = solver.solve_hcaptcha(site_key="SITE_KEY", page_url="https://example.com")

    # Turnstile
    token = solver.solve_turnstile(site_key="SITE_KEY", page_url="https://example.com")

    # Selenium integration — auto-inject token
    solver.selenium_solve_recaptcha(driver, site_key_selector="iframe")
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class CaptchaResult:
    success: bool
    token: str = ""
    text: str = ""
    error: str = ""
    elapsed_seconds: float = 0.0
    cost: float = 0.0


# ---------------------------------------------------------------------------
# Base solver
# ---------------------------------------------------------------------------

class BaseCaptchaSolver(ABC):
    """Abstract base for CAPTCHA solving services."""

    def __init__(self, api_key: str, timeout: int = 120):
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = ""

    @abstractmethod
    def solve_image(self, image_path: str = None, image_base64: str = None) -> CaptchaResult:
        """Solve a text image CAPTCHA."""
        ...

    @abstractmethod
    def solve_recaptcha_v2(self, site_key: str, page_url: str,
                           invisible: bool = False) -> CaptchaResult:
        """Solve reCAPTCHA v2."""
        ...

    @abstractmethod
    def solve_recaptcha_v3(self, site_key: str, page_url: str,
                           action: str = "verify", min_score: float = 0.3) -> CaptchaResult:
        """Solve reCAPTCHA v3."""
        ...

    @abstractmethod
    def solve_hcaptcha(self, site_key: str, page_url: str) -> CaptchaResult:
        """Solve hCaptcha."""
        ...

    @abstractmethod
    def solve_turnstile(self, site_key: str, page_url: str) -> CaptchaResult:
        """Solve Cloudflare Turnstile."""
        ...


# ---------------------------------------------------------------------------
# 2captcha implementation
# ---------------------------------------------------------------------------

class Captcha2Solver(BaseCaptchaSolver):
    """
    2captcha.com implementation.
    Most popular CAPTCHA solving service. Supports all major types.
    Pricing: ~$0.50/1000 image CAPTCHAs, ~$2.99/1000 reCAPTCHA.
    """

    BASE_URL = "http://2captcha.com"

    def __init__(self, api_key: str, timeout: int = 120, polling_interval: int = 5):
        super().__init__(api_key, timeout)
        self.polling_interval = polling_interval

    # ------------------------------------------------------------------
    # Image CAPTCHA
    # ------------------------------------------------------------------

    def solve_image(self, image_path: str = None, image_base64: str = None) -> CaptchaResult:
        """Solve a simple image CAPTCHA."""
        start = time.time()

        # Upload image
        if image_path and not image_base64:
            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode()

        if not image_base64:
            return CaptchaResult(success=False, error="No image provided")

        # Submit
        resp = requests.post(f"{self.BASE_URL}/in.php", data={
            "key": self.api_key,
            "method": "base64",
            "body": image_base64,
            "numeric": 0,
            "min_len": 4,
            "max_len": 10,
        }, timeout=30)

        if resp.text.startswith("ERROR"):
            return CaptchaResult(success=False, error=resp.text)

        captcha_id = resp.text.split("|")[1]

        # Poll for result
        result = self._poll_result(captcha_id)
        result.elapsed_seconds = time.time() - start
        return result

    # ------------------------------------------------------------------
    # reCAPTCHA v2
    # ------------------------------------------------------------------

    def solve_recaptcha_v2(self, site_key: str, page_url: str,
                           invisible: bool = False) -> CaptchaResult:
        start = time.time()
        method = "userrecaptcha" if not invisible else "userrecaptcha"
        data = {
            "key": self.api_key,
            "method": method,
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        if invisible:
            data["invisible"] = 1

        resp = requests.post(f"{self.BASE_URL}/in.php", data=data, timeout=30)
        if resp.text.startswith("ERROR"):
            return CaptchaResult(success=False, error=resp.text)

        captcha_id = resp.json().get("request", "")
        if not captcha_id or captcha_id == "ERROR_WRONG_USER_KEY":
            return CaptchaResult(success=False, error="Invalid API key or site key")

        result = self._poll_result(captcha_id)
        result.elapsed_seconds = time.time() - start
        return result

    # ------------------------------------------------------------------
    # reCAPTCHA v3
    # ------------------------------------------------------------------

    def solve_recaptcha_v3(self, site_key: str, page_url: str,
                           action: str = "verify", min_score: float = 0.3) -> CaptchaResult:
        start = time.time()
        data = {
            "key": self.api_key,
            "method": "userrecaptchav3",
            "googlekey": site_key,
            "pageurl": page_url,
            "version": "v3",
            "action": action,
            "min_score": min_score,
            "json": 1,
        }
        resp = requests.post(f"{self.BASE_URL}/in.php", data=data, timeout=30)
        if resp.text.startswith("ERROR"):
            return CaptchaResult(success=False, error=resp.text)

        try:
            captcha_id = resp.json().get("request", "")
        except Exception:
            return CaptchaResult(success=False, error=f"Bad response: {resp.text[:200]}")

        result = self._poll_result(captcha_id)
        result.elapsed_seconds = time.time() - start
        return result

    # ------------------------------------------------------------------
    # hCaptcha
    # ------------------------------------------------------------------

    def solve_hcaptcha(self, site_key: str, page_url: str) -> CaptchaResult:
        start = time.time()
        data = {
            "key": self.api_key,
            "method": "hcaptcha",
            "sitekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        resp = requests.post(f"{self.BASE_URL}/in.php", data=data, timeout=30)
        if resp.text.startswith("ERROR"):
            return CaptchaResult(success=False, error=resp.text)

        captcha_id = resp.json().get("request", "")
        result = self._poll_result(captcha_id)
        result.elapsed_seconds = time.time() - start
        return result

    # ------------------------------------------------------------------
    # Turnstile
    # ------------------------------------------------------------------

    def solve_turnstile(self, site_key: str, page_url: str) -> CaptchaResult:
        """
        2captcha handles Turnstile via the 'turnstile' method.
        """
        start = time.time()
        data = {
            "key": self.api_key,
            "method": "turnstile",
            "sitekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        resp = requests.post(f"{self.BASE_URL}/in.php", data=data, timeout=30)
        if resp.text.startswith("ERROR"):
            return CaptchaResult(success=False, error=resp.text)

        captcha_id = resp.json().get("request", "")
        result = self._poll_result(captcha_id)
        result.elapsed_seconds = time.time() - start
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _poll_result(self, captcha_id: str) -> CaptchaResult:
        """Poll 2captcha for result."""
        for _ in range(self.timeout // self.polling_interval):
            time.sleep(self.polling_interval)
            resp = requests.get(f"{self.BASE_URL}/res.php", params={
                "key": self.api_key,
                "action": "get",
                "id": captcha_id,
                "json": 1,
            }, timeout=30)

            if resp.text.startswith("ERROR"):
                return CaptchaResult(success=False, error=resp.text)

            try:
                data = resp.json()
            except Exception:
                return CaptchaResult(success=False, error=f"Bad response: {resp.text[:200]}")

            if data.get("request") == "CAPCHA_NOT_READY":
                continue
            if data.get("status") == 1:
                return CaptchaResult(success=True, token=data.get("request", ""))
            return CaptchaResult(success=False, error=data.get("request", "Unknown error"))

        return CaptchaResult(success=False, error="Timeout waiting for CAPTCHA solution")


# ---------------------------------------------------------------------------
# anti-captcha.com implementation
# ---------------------------------------------------------------------------

class AntiCaptchaSolver(BaseCaptchaSolver):
    """
    anti-captcha.com implementation.
    Alternative to 2captcha, similar pricing.
    """

    BASE_URL = "https://api.anti-captcha.com"

    def __init__(self, api_key: str, timeout: int = 120, polling_interval: int = 5):
        super().__init__(api_key, timeout)
        self.polling_interval = polling_interval

    def _create_task(self, task_type: str, task_body: dict) -> Optional[str]:
        """Create a task and return its ID."""
        resp = requests.post(f"{self.BASE_URL}/createTask", json={
            "clientKey": self.api_key,
            "task": {"type": task_type, **task_body},
        }, timeout=30)

        data = resp.json()
        if data.get("errorId") == 0:
            return str(data["taskId"])
        return None

    def _get_result(self, task_id: str) -> CaptchaResult:
        """Poll for task result."""
        for _ in range(self.timeout // self.polling_interval):
            time.sleep(self.polling_interval)
            resp = requests.post(f"{self.BASE_URL}/getTaskResult", json={
                "clientKey": self.api_key,
                "taskId": int(task_id),
            }, timeout=30)
            data = resp.json()

            if data.get("errorId") != 0:
                return CaptchaResult(success=False, error=data.get("errorDescription", "Unknown"))

            status = data.get("status", "")
            if status == "processing":
                continue
            if status == "ready":
                solution = data.get("solution", {})
                return CaptchaResult(
                    success=True,
                    token=solution.get("token", solution.get("text", "")),
                    text=solution.get("text", ""),
                )
            return CaptchaResult(success=False, error=f"Unexpected status: {status}")

        return CaptchaResult(success=False, error="Timeout")

    def solve_image(self, image_path: str = None, image_base64: str = None) -> CaptchaResult:
        start = time.time()
        if image_path and not image_base64:
            image_base64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        if not image_base64:
            return CaptchaResult(success=False, error="No image provided")

        tid = self._create_task("ImageToTextTask", {"body": image_base64})
        if not tid:
            return CaptchaResult(success=False, error="Failed to create task")
        result = self._get_result(tid)
        result.elapsed_seconds = time.time() - start
        return result

    def solve_recaptcha_v2(self, site_key: str, page_url: str, invisible: bool = False) -> CaptchaResult:
        start = time.time()
        tid = self._create_task("RecaptchaV2Task", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            "isInvisible": invisible,
        })
        if not tid:
            return CaptchaResult(success=False, error="Failed to create task")
        result = self._get_result(tid)
        result.elapsed_seconds = time.time() - start
        return result

    def solve_recaptcha_v3(self, site_key: str, page_url: str,
                           action: str = "verify", min_score: float = 0.3) -> CaptchaResult:
        start = time.time()
        tid = self._create_task("RecaptchaV3TaskProxyless", {
            "websiteURL": page_url,
            "websiteKey": site_key,
            "minScore": min_score,
            "pageAction": action,
        })
        if not tid:
            return CaptchaResult(success=False, error="Failed to create task")
        result = self._get_result(tid)
        result.elapsed_seconds = time.time() - start
        return result

    def solve_hcaptcha(self, site_key: str, page_url: str) -> CaptchaResult:
        start = time.time()
        tid = self._create_task("HCaptchaTaskProxyless", {
            "websiteURL": page_url,
            "websiteKey": site_key,
        })
        if not tid:
            return CaptchaResult(success=False, error="Failed to create task")
        result = self._get_result(tid)
        result.elapsed_seconds = time.time() - start
        return result

    def solve_turnstile(self, site_key: str, page_url: str) -> CaptchaResult:
        start = time.time()
        tid = self._create_task("TurnstileTaskProxyless", {
            "websiteURL": page_url,
            "websiteKey": site_key,
        })
        if not tid:
            return CaptchaResult(success=False, error="Failed to create task")
        result = self._get_result(tid)
        result.elapsed_seconds = time.time() - start
        return result


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------

class CaptchaSolver:
    """
    Unified CAPTCHA solver. Supports 2captcha and anti-captcha.

    Configure via environment or direct args:
        solver = CaptchaSolver()  # reads CAPTCHA_SERVICE + CAPTCHA_API_KEY from env
        solver = CaptchaSolver(service="2captcha", api_key="KEY")
    """

    def __init__(self, service: str = None, api_key: str = None, **kwargs):
        service = service or os.getenv("CAPTCHA_SERVICE", "2captcha")
        api_key = api_key or os.getenv("CAPTCHA_API_KEY", "")

        if not api_key:
            raise ValueError("CAPTCHA API key required. Set CAPTCHA_API_KEY env var or pass api_key=")

        if service == "2captcha":
            self._solver = Captcha2Solver(api_key, **kwargs)
        elif service == "anti-captcha":
            self._solver = AntiCaptchaSolver(api_key, **kwargs)
        else:
            raise ValueError(f"Unknown CAPTCHA service: {service}. Use '2captcha' or 'anti-captcha'.")

    def solve_image(self, **kwargs) -> CaptchaResult:
        return self._solver.solve_image(**kwargs)

    def solve_recaptcha_v2(self, **kwargs) -> CaptchaResult:
        return self._solver.solve_recaptcha_v2(**kwargs)

    def solve_recaptcha_v3(self, **kwargs) -> CaptchaResult:
        return self._solver.solve_recaptcha_v3(**kwargs)

    def solve_hcaptcha(self, **kwargs) -> CaptchaResult:
        return self._solver.solve_hcaptcha(**kwargs)

    def solve_turnstile(self, **kwargs) -> CaptchaResult:
        return self._solver.solve_turnstile(**kwargs)

    # ------------------------------------------------------------------
    # Selenium integration
    # ------------------------------------------------------------------

    def selenium_solve_recaptcha(self, driver, site_key: str = None,
                                  site_key_selector: str = None,
                                  page_url: str = None) -> Optional[str]:
        """
        Auto-detect reCAPTCHA site key and inject token into Selenium driver.
        Returns the token on success.
        """
        if not page_url:
            page_url = driver.current_url

        if not site_key:
            if site_key_selector:
                try:
                    iframe = driver.find_element("css selector", site_key_selector)
                    src = iframe.get_attribute("src") or ""
                    if "k=" in src:
                        site_key = src.split("k=")[1].split("&")[0]
                except Exception:
                    pass

            if not site_key:
                try:
                    el = driver.find_element("css selector", '[data-sitekey]')
                    site_key = el.get_attribute("data-sitekey")
                except Exception:
                    pass

        if not site_key:
            logger.error("Could not detect reCAPTCHA site key")
            return None

        logger.info("Solving reCAPTCHA v2 for site_key=%s", site_key)
        result = self.solve_recaptcha_v2(site_key=site_key, page_url=page_url)
        if result.success:
            # Inject token via JavaScript
            token = result.token
            driver.execute_script(f"""
                document.getElementById('g-recaptcha-response').innerHTML = '{token}';
                if (typeof grecaptcha !== 'undefined') {{
                    grecaptcha.getResponse = function() {{ return '{token}'; }};
                }}
            """)
            logger.info("Token injected (%.1fs)", result.elapsed_seconds)
            return token
        else:
            logger.error("reCAPTCHA solve failed: %s", result.error)
            return None

    def selenium_solve_hcaptcha(self, driver, site_key: str = None) -> Optional[str]:
        """Solve hCaptcha in Selenium."""
        if not site_key:
            try:
                el = driver.find_element("css selector", '[data-hcaptcha-sitekey], [data-sitekey]')
                site_key = el.get_attribute("data-hcaptcha-sitekey") or el.get_attribute("data-sitekey")
            except Exception:
                pass

        if not site_key:
            logger.error("Could not detect hCaptcha site key")
            return None

        page_url = driver.current_url
        result = self.solve_hcaptcha(site_key=site_key, page_url=page_url)
        if result.success:
            driver.execute_script(f"""
                document.querySelector('textarea[name="h-captcha-response"]').value = '{result.token}';
            """)
            return result.token
        return None

    # ------------------------------------------------------------------
    # Playwright integration
    # ------------------------------------------------------------------

    async def playwright_solve_recaptcha(self, page, site_key: str = None) -> Optional[str]:
        """Solve reCAPTCHA in Playwright page."""
        if not site_key:
            try:
                site_key = await page.evaluate("""
                    () => {
                        const el = document.querySelector('[data-sitekey]');
                        return el ? el.getAttribute('data-sitekey') : null;
                    }
                """)
            except Exception:
                pass

        if not site_key:
            logger.error("Could not detect reCAPTCHA site key")
            return None

        page_url = page.url
        result = self.solve_recaptcha_v2(site_key=site_key, page_url=page_url)
        if result.success:
            await page.evaluate(f"""
                () => {{
                    const ta = document.getElementById('g-recaptcha-response');
                    if (ta) ta.innerHTML = '{result.token}';
                }}
            """)
            return result.token
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="CAPTCHA solver")
    parser.add_argument("--service", default=os.getenv("CAPTCHA_SERVICE", "2captcha"))
    parser.add_argument("--api-key", default=os.getenv("CAPTCHA_API_KEY", ""))
    parser.add_argument("--image", help="Path to image CAPTCHA")
    parser.add_argument("--site-key", help="reCAPTCHA/hCaptcha site key")
    parser.add_argument("--page-url", help="Page URL for reCAPTCHA")
    parser.add_argument("--mode", choices=["image", "v2", "v3", "hcaptcha", "turnstile"])
    args = parser.parse_args()

    if not args.api_key:
        parser.error("--api-key required or set CAPTCHA_API_KEY env var")

    solver = CaptchaSolver(service=args.service, api_key=args.api_key)

    if args.mode == "image" and args.image:
        r = solver.solve_image(image_path=args.image)
        print(f"Text: {r.text}  ({r.elapsed_seconds:.1f}s)")
    elif args.mode == "v2" and args.site_key and args.page_url:
        r = solver.solve_recaptcha_v2(site_key=args.site_key, page_url=args.page_url)
        print(f"Token: {r.token[:40]}...  ({r.elapsed_seconds:.1f}s)")
    elif args.mode == "hcaptcha" and args.site_key and args.page_url:
        r = solver.solve_hcaptcha(site_key=args.site_key, page_url=args.page_url)
        print(f"Token: {r.token[:40]}...  ({r.elapsed_seconds:.1f}s)")
    else:
        parser.print_help()
