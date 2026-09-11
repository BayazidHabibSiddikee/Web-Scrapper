"""
proxy_rotation.py
===============
Production-grade proxy rotation with:
  - Health checking (auto-remove dead proxies)
  - Protocol support: HTTP, HTTPS, SOCKS4, SOCKS5
  - Sticky sessions (same IP per target)
  - Round-robin and random strategies
  - Integration with httpx, requests, Selenium, Playwright
  - Proxy scoring / priority tiers
  - Auto-reload from proxies.txt

Usage:
    from proxy_rotation import ProxyManager

    pm = ProxyManager("config/proxies.txt")
    proxy = pm.get("https://example.com", sticky=True)

    # httpx
    async with pm.httpx_client() as client:
        resp = await client.get("https://httpbin.org/ip")

    # requests
    resp = pm.requests_get("https://httpbin.org/ip")

    # Selenium
    driver = pm.selenium_driver()

    # Playwright
    context = await pm.playwright_context(browser)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Optional, Dict, List, Tuple

import requests
import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Proxy:
    url: str
    protocol: str = "http"          # http | https | socks4 | socks5
    host: str = ""
    port: str = ""
    username: str = ""
    password: str = ""
    country: str = ""
    tier: int = 0                   # 0 = standard, 1 = premium, 2 = elite
    latency_ms: float = 0.0
    last_checked: datetime = field(default_factory=datetime.utcnow)
    failure_count: int = 0
    is_healthy: bool = True
    total_requests: int = 0
    success_count: int = 0

    @property
    def score(self) -> float:
        """Higher = better. Considers latency, reliability, tier."""
        if not self.is_healthy:
            return -1.0
        success_rate = self.success_count / max(self.total_requests, 1)
        latency_score = max(0, 1000 - self.latency_ms) / 1000
        tier_bonus = self.tier * 0.2
        return (success_rate * 0.5 + latency_score * 0.3 + tier_bonus * 0.2)


# ---------------------------------------------------------------------------
# Proxy parser
# ---------------------------------------------------------------------------

def parse_proxy_line(line: str) -> Optional[Proxy]:
    """Parse a proxy line from proxies.txt."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Format: protocol://[user:pass@]host:port  or  host:port  or  user:pass@host:port
    protocol = "http"
    auth = ""
    host_port = line

    if "://" in line:
        protocol, host_port = line.split("://", 1)

    if "@" in host_port:
        auth, host_port = host_port.rsplit("@", 1)
        if ":" in auth:
            username, password = auth.split(":", 1)
        else:
            username, password = auth, ""
    else:
        username = password = ""

    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
    else:
        return None

    return Proxy(
        url=line,
        protocol=protocol.lower().replace("socks5", "socks5").replace("socks4", "socks4"),
        host=host,
        port=port,
        username=username,
        password=password,
    )


def load_proxies(path: str) -> List[Proxy]:
    """Load proxies from a file."""
    proxies = []
    try:
        text = Path(path).read_text(encoding="utf-8")
        for line in text.splitlines():
            p = parse_proxy_line(line)
            if p:
                proxies.append(p)
    except FileNotFoundError:
        logger.warning("Proxy file not found: %s", path)
    return proxies


# ---------------------------------------------------------------------------
# Proxy manager
# ---------------------------------------------------------------------------

class ProxyManager:
    """
    Thread-safe proxy rotation manager.

    Strategies:
      - round_robin: cycle through healthy proxies
      - random: pick a random healthy proxy
      - best: pick the highest-scoring proxy
      - sticky: same proxy for the same target domain
    """

    def __init__(self, proxy_file: str = "config/proxies.txt",
                 strategy: str = "round_robin",
                 health_check_url: str = "https://httpbin.org/ip",
                 health_check_interval: float = 300.0,  # seconds
                 max_failures: int = 3,
                 auto_reload: bool = True,
                 reload_interval: float = 60.0):

        self.proxy_file = proxy_file
        self.strategy = strategy
        self.health_check_url = health_check_url
        self.health_check_interval = health_check_interval
        self.max_failures = max_failures
        self.auto_reload = auto_reload
        self.reload_interval = reload_interval

        self._proxies: List[Proxy] = []
        self._lock = Lock()
        self._rr_index = 0
        self._sticky_map: Dict[str, Proxy] = {}
        self._last_health_check = datetime.utcnow() - timedelta(seconds=9999)
        self._last_reload = datetime.utcnow() - timedelta(seconds=9999)

        # Initial load
        self._reload()
        self._run_health_check()

    # ------------------------------------------------------------------
    # Loading / reloading
    # ------------------------------------------------------------------

    def _reload(self):
        """Reload proxies from file."""
        new_proxies = load_proxies(self.proxy_file)
        with self._lock:
            # Merge: keep existing state for proxies that still exist
            existing = {p.url: p for p in self._proxies}
            merged = []
            for p in new_proxies:
                if p.url in existing:
                    merged.append(existing[p.url])
                else:
                    merged.append(p)
            self._proxies = merged
        logger.info("Loaded %d proxies", len(self._proxies))

    def _maybe_reload(self):
        """Auto-reload if interval elapsed."""
        if self.auto_reload and datetime.utcnow() - self._last_reload > timedelta(seconds=self.reload_interval):
            self._last_reload = datetime.utcnow()
            self._reload()

    # ------------------------------------------------------------------
    # Health checking
    # ------------------------------------------------------------------

    def _run_health_check(self):
        """Check all proxies in background thread."""
        import threading
        def _check():
            with self._lock:
                proxies = list(self._proxies)
            for p in proxies:
                self._check_single(p)
            self._last_health_check = datetime.utcnow()

        threading.Thread(target=_check, daemon=True).start()

    def _check_single(self, proxy: Proxy) -> bool:
        """Health check one proxy. Returns True if healthy."""
        try:
            proxies_dict = self._to_requests_format(proxy)
            resp = requests.get(
                self.health_check_url,
                proxies=proxies_dict,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                proxy.latency_ms = resp.elapsed.total_seconds() * 1000
                proxy.is_healthy = True
                proxy.last_checked = datetime.utcnow()
                return True
        except Exception:
            pass

        proxy.failure_count += 1
        if proxy.failure_count >= self.max_failures:
            proxy.is_healthy = False
        proxy.last_checked = datetime.utcnow()
        return False

    def _maybe_health_check(self):
        """Run health check if interval elapsed."""
        if datetime.utcnow() - self._last_health_check > timedelta(seconds=self.health_check_interval):
            self._run_health_check()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _get_healthy(self) -> List[Proxy]:
        with self._lock:
            return [p for p in self._proxies if p.is_healthy]

    def _select(self, target_domain: str = "") -> Optional[Proxy]:
        """Pick a proxy based on strategy."""
        self._maybe_reload()
        self._maybe_health_check()
        healthy = self._get_healthy()

        if not healthy:
            logger.warning("No healthy proxies available")
            return None

        # Sticky: same proxy per domain
        if self.strategy == "sticky" and target_domain:
            if target_domain not in self._sticky_map:
                self._sticky_map[target_domain] = self._pick(healthy)
            p = self._sticky_map[target_domain]
            if p.is_healthy:
                return p
            # Stale sticky proxy — pick new one
            del self._sticky_map[target_domain]
            return self._pick(healthy)

        return self._pick(healthy)

    def _pick(self, proxies: List[Proxy]) -> Proxy:
        if self.strategy == "random":
            return random.choice(proxies)
        elif self.strategy == "best":
            return max(proxies, key=lambda p: p.score)
        else:  # round_robin
            with self._lock:
                p = proxies[self._rr_index % len(proxies)]
                self._rr_index += 1
            return p

    def get(self, target_domain: str = "") -> Optional[Proxy]:
        """Get a proxy for a target domain. Returns None if none available."""
        p = self._select(target_domain)
        if p:
            p.total_requests += 1
        return p

    def report_success(self, proxy: Proxy):
        """Report a successful request through this proxy."""
        with self._lock:
            proxy.success_count += 1

    def report_failure(self, proxy: Proxy):
        """Report a failed request. Increments failure count."""
        with self._lock:
            proxy.failure_count += 1
            if proxy.failure_count >= self.max_failures:
                proxy.is_healthy = False
                logger.warning("Proxy marked unhealthy: %s", proxy.url)

    # ------------------------------------------------------------------
    # Format conversion
    # ------------------------------------------------------------------

    def _to_requests_format(self, proxy: Proxy) -> dict:
        """Convert Proxy to requests-compatible dict."""
        if proxy.username:
            auth = f"{proxy.username}:{proxy.password}@"
            url = f"{proxy.protocol}://{auth}{proxy.host}:{proxy.port}"
        else:
            url = f"{proxy.protocol}://{proxy.host}:{proxy.port}"
        return {"http": url, "https": url}

    def _to_httpx_format(self, proxy: Proxy) -> Optional[str]:
        """Convert Proxy to httpx proxy string."""
        if proxy.protocol in ("socks4", "socks5"):
            if proxy.username:
                return f"{proxy.protocol}://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
            return f"{proxy.protocol}://{proxy.host}:{proxy.port}"
        else:
            if proxy.username:
                return f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
            return f"http://{proxy.host}:{proxy.port}"

    # ------------------------------------------------------------------
    # Integration: requests
    # ------------------------------------------------------------------

    def requests_get(self, url: str, target_domain: str = "", **kwargs) -> Optional[requests.Response]:
        """requests.get() with automatic proxy rotation."""
        proxy = self.get(target_domain or httpx.URL(url).host)
        if not proxy:
            return None

        try:
            resp = requests.get(url, proxies=self._to_requests_format(proxy),
                                timeout=15, **kwargs)
            self.report_success(proxy)
            return resp
        except Exception as exc:
            self.report_failure(proxy)
            logger.warning("Request failed via %s: %s", proxy.url, exc)
            return None

    # ------------------------------------------------------------------
    # Integration: httpx (async)
    # ------------------------------------------------------------------

    def httpx_client(self, target_domain: str = "") -> Optional[httpx.AsyncClient]:
        """Create an httpx.AsyncClient bound to a proxy."""
        proxy = self.get(target_domain)
        if not proxy:
            return None

        proxy_url = self._to_httpx_format(proxy)
        return httpx.AsyncClient(
            proxies=proxy_url,
            follow_redirects=True,
            timeout=15.0,
            http2=True,
        )

    async def httpx_get(self, url: str, target_domain: str = "", **kwargs) -> Optional[httpx.Response]:
        """httpx.get() with automatic proxy rotation."""
        proxy = self.get(target_domain or httpx.URL(url).host)
        if not proxy:
            return None

        try:
            async with httpx.AsyncClient(
                proxies=self._to_httpx_format(proxy),
                follow_redirects=True,
                timeout=15.0,
                http2=True,
            ) as client:
                resp = await client.get(url, **kwargs)
                self.report_success(proxy)
                return resp
        except Exception as exc:
            self.report_failure(proxy)
            logger.warning("httpx request failed via %s: %s", proxy.url, exc)
            return None

    # ------------------------------------------------------------------
    # Integration: Selenium
    # ------------------------------------------------------------------

    def selenium_options(self, browser: str = "chrome"):
        """
        Return Selenium options configured with a proxy.
        Call get() first to pick a proxy, then apply to options.
        """
        proxy = self.get()
        if not proxy:
            return None

        if browser == "firefox":
            from selenium.webdriver.firefox.options import Options
            opts = Options()
            if proxy.username:
                opts.set_preference("network.proxy.type", 1)
                opts.set_preference("network.proxy.http", proxy.host)
                opts.set_preference("network.proxy.http_port", int(proxy.port))
                opts.set_preference("network.proxy.ssl", proxy.host)
                opts.set_preference("network.proxy.ssl_port", int(proxy.port))
                opts.set_preference("network.proxy.username", proxy.username)
                opts.set_preference("network.proxy.password", proxy.password)
            else:
                opts.set_preference("network.proxy.type", 1)
                opts.set_preference("network.proxy.http", proxy.host)
                opts.set_preference("network.proxy.http_port", int(proxy.port))
            return opts
        else:
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            if proxy.username:
                proxy_url = f"{proxy.host}:{proxy.port}:{proxy.username}:{proxy.password}"
            else:
                proxy_url = f"{proxy.host}:{proxy.port}"

            if proxy.protocol in ("socks4", "socks5"):
                opts.add_argument(f"--proxy-server={proxy.protocol}://{proxy.host}:{proxy.port}")
            else:
                opts.add_argument(f"--proxy-server={proxy.host}:{proxy.port}")
            return opts

    # ------------------------------------------------------------------
    # Integration: Playwright
    # ------------------------------------------------------------------

    async def playwright_context(self, browser, target_domain: str = ""):
        """
        Create a Playwright browser context with a proxy.
        Returns (context, proxy) or (None, None).
        """
        proxy = self.get(target_domain)
        if not proxy:
            return None, None

        proxy_config = {
            "server": f"{proxy.protocol}://{proxy.host}:{proxy.port}",
        }
        if proxy.username:
            proxy_config["username"] = proxy.username
            proxy_config["password"] = proxy.password

        context = await browser.new_context(proxy=proxy_config)
        return context, proxy

    # ------------------------------------------------------------------
    # Stats / monitoring
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return proxy pool statistics."""
        with self._lock:
            healthy = [p for p in self._proxies if p.is_healthy]
            unhealthy = [p for p in self._proxies if not p.is_healthy]
            total_req = sum(p.total_requests for p in self._proxies)
            total_ok = sum(p.success_count for p in self._proxies)

        return {
            "total": len(self._proxies),
            "healthy": len(healthy),
            "unhealthy": len(unhealthy),
            "total_requests": total_req,
            "success_rate": f"{(total_ok / max(total_req, 1) * 100):.1f}%",
            "strategy": self.strategy,
        }

    def health_report(self) -> List[dict]:
        """Return per-proxy health report."""
        with self._lock:
            return [
                {
                    "url": p.url,
                    "healthy": p.is_healthy,
                    "latency_ms": round(p.latency_ms, 1),
                    "failures": p.failure_count,
                    "success_rate": f"{(p.success_count / max(p.total_requests, 1) * 100):.1f}%",
                    "tier": p.tier,
                    "country": p.country,
                }
                for p in sorted(self._proxies, key=lambda x: -x.score)
            ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Proxy rotation manager")
    parser.add_argument("proxy_file", nargs="?", default="config/proxies.txt")
    parser.add_argument("--test-url", default="https://httpbin.org/ip")
    parser.add_argument("--strategy", choices=["round_robin", "random", "best", "sticky"],
                        default="round_robin")
    args = parser.parse_args()

    pm = ProxyManager(args.proxy_file, strategy=args.strategy)
    print(f"\nProxy pool stats: {pm.stats()}")

    print("\nHealth report:")
    for p in pm.health_report():
        status = "OK" if p["healthy"] else "DEAD"
        print(f"  [{status}] {p['url']:50s}  {p['latency_ms']:6.1f}ms  {p['success_rate']}")

    # Test one proxy
    print(f"\nTesting via proxy: {args.test_url}")
    proxy = pm.get()
    if proxy:
        print(f"  Selected: {proxy.url}")
        resp = pm.requests_get(args.test_url)
        if resp:
            print(f"  Response: {resp.status_code} — {resp.text[:200]}")
