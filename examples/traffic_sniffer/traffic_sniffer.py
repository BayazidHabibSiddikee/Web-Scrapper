"""
traffic_sniffer.py
===============
Network traffic capture and HAR generation for scraping.

Two backends:
  1. Playwright CDP  — Chromium-only, native HAR export
  2. mitmproxy       — full HTTP/HTTPS proxy, intercept + modify

Captures:
  - All requests (URL, method, headers, body)
  - All responses (status, headers, body)
  - Timings (DNS, connect, TLS, TTFB, total)
  - WebSocket frames (mitmproxy)
  - API endpoints extracted from traffic

Output formats:
  - HAR (HTTP Archive) — standard format, viewable in browser DevTools
  - JSON — structured request/response log
  - CSV — flat request table
  - Endpoint list — extracted API routes

Usage:
    # Playwright HAR
    python traffic_sniffer.py https://example.com --backend playwright --output capture.har

    # mitmproxy HAR
    python traffic_sniffer.py https://example.com --backend mitmproxy --output capture.har

    # Just extract endpoints from existing HAR
    python traffic_sniffer.py --har existing.har --mode endpoints
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class TrafficRequest:
    url: str
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    post_data: str = ""
    timestamp: str = ""

@dataclass
class TrafficResponse:
    status: int = 0
    status_text: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    content_type: str = ""
    size_bytes: int = 0
    timing_dns: float = 0.0
    timing_connect: float = 0.0
    timing_tls: float = 0.0
    timing_ttfb: float = 0.0
    timing_total: float = 0.0

@dataclass
class TrafficEntry:
    request: TrafficRequest
    response: TrafficResponse
    entry_id: int = 0


# ---------------------------------------------------------------------------
# HAR helpers
# ---------------------------------------------------------------------------

def har_from_playwright_entries(entries: list) -> dict:
    """Convert Playwright CDP entries to HAR format."""
    har = {
        "log": {
            "version": "1.2",
            "creator": {"name": "traffic-sniffer", "version": "1.0"},
            "entries": [],
        }
    }
    for idx, entry in enumerate(entries, 1):
        req = entry.get("request", {})
        resp = entry.get("response", {})
        har["log"]["entries"].append({
            "_trafficSnifferId": idx,
            "startedDateTime": entry.get("timestamp", datetime.utcnow().isoformat()),
            "time": entry.get("timing", {}).get("total", 0),
            "request": {
                "method": req.get("method", "GET"),
                "url": req.get("url", ""),
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in req.get("headers", {}).items()],
                "queryString": [{"name": k, "value": v} for k, v in req.get("queryString", {}).items()],
                "postData": {"mimeType": resp.get("contentType", ""), "text": req.get("postData", "")} if req.get("postData") else {},
                "headersSize": -1,
                "bodySize": -1,
            },
            "response": {
                "status": resp.get("status", 0),
                "statusText": resp.get("statusText", ""),
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in resp.get("headers", {}).items()],
                "content": {
                    "size": resp.get("sizeBytes", 0),
                    "mimeType": resp.get("contentType", ""),
                    "text": resp.get("body", "")[:100000],
                },
                "headersSize": -1,
                "bodySize": resp.get("sizeBytes", 0),
            },
            "cache": {},
            "timings": {
                "dns": resp.get("timingDns", -1),
                "connect": resp.get("timingConnect", -1),
                "ssl": resp.get("timingTls", -1),
                "ttfb": resp.get("timingTtfb", -1),
                "receive": 0,
            },
        })
    return har


# ---------------------------------------------------------------------------
# Backend 1: Playwright CDP HAR capture
# ---------------------------------------------------------------------------

async def capture_with_playwright(url: str, output: str = "output/traffic.har",
                                  wait: float = 5.0, headless: bool = True) -> List[TrafficEntry]:
    """
    Capture traffic via Playwright CDP HAR export.
    Chromium only. No mitmproxy needed.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("playwright not installed: pip install playwright")
        return []

    entries: List[TrafficEntry] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        page = await context.new_page()

        # Hook into CDP requests
        async def _on_request(request):
            post_data = ""
            try:
                if request.method in ("POST", "PUT", "PATCH"):
                    post_data = (await request.post_data_json()) or (await request.post_data()) or ""
            except Exception:
                pass

            entries.append(TrafficEntry(
                request=TrafficRequest(
                    url=request.url,
                    method=request.method,
                    headers=dict(request.headers),
                    post_data=str(post_data)[:50000],
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ),
                response=TrafficResponse(),
                entry_id=len(entries) + 1,
            ))

        async def _on_response(response):
            eid = len(entries)
            if eid == 0:
                return
            entry = entries[eid - 1]
            try:
                body = ""
                ct = response.headers.get("content-type", "")
                if "text" in ct or "json" in ct or "javascript" in ct:
                    body = (await response.body()).decode("utf-8", errors="ignore")[:50000]
                entry.response = TrafficResponse(
                    status=response.status,
                    status_text=response.status_text,
                    headers=dict(response.headers),
                    body=body,
                    content_type=ct,
                    size_bytes=len(await response.body()) if ct else 0,
                )
            except Exception:
                pass

        page.on("request", _on_request)
        page.on("response", _on_response)

        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(wait * 1000)

        # Export HAR
        har = har_from_playwright_entries([
            {
                "request": {
                    "url": e.request.url,
                    "method": e.request.method,
                    "headers": e.request.headers,
                    "postData": e.request.post_data,
                    "queryString": {},
                },
                "response": {
                    "status": e.response.status,
                    "statusText": e.response.status_text,
                    "headers": e.response.headers,
                    "body": e.response.body,
                    "contentType": e.response.content_type,
                    "sizeBytes": e.response.size_bytes,
                },
                "timestamp": e.request.timestamp,
                "timing": {
                    "dns": e.response.timing_dns,
                    "connect": e.response.timing_connect,
                    "tls": e.response.timing_tls,
                    "ttfb": e.response.timing_ttfb,
                    "total": e.response.timing_total,
                },
            }
            for e in entries
        ])

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(har, f, indent=2, ensure_ascii=False)

        logger.info("HAR saved: %s (%d entries)", output, len(entries))
        await browser.close()
        return entries

    except ImportError:
        logger.error("playwright not installed: pip install playwright")
        return []


# ---------------------------------------------------------------------------
# Backend 2: mitmproxy
# ---------------------------------------------------------------------------

class MitmproxyHarvester:
    """
    Capture traffic via mitmproxy addon.
    Requires: pip install mitmproxy && mitmweb --version
    """

    def __init__(self, output: str = "output/traffic.har",
                 port: int = 8080, host: str = "127.0.0.1"):
        self.output = output
        self.port = port
        self.host = host
        self._entries: List[TrafficEntry] = []
        self._process: Optional[subprocess.Popen] = None

    def _write_addon(self) -> str:
        """Write the mitmproxy addon script to a temp file."""
        addon_code = '''
import json
from datetime import datetime, timezone
from mitmproxy import http

entries = []

def request(flow: http.HTTPFlow) -> None:
    flow.request.headers.pop("Proxy-Connection", None)
    entries.append({
        "request": {
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "headers": dict(flow.request.headers),
            "postData": flow.request.get_text()[:50000],
        },
        "response": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "timing": {},
    })

def response(flow: http.HTTPFlow) -> None:
    if entries:
        ct = flow.response.headers.get("Content-Type", "")
        body = ""
        if any(t in ct for t in ["text", "json", "javascript", "xml"]):
            body = flow.response.get_text()[:50000]
        entries[-1]["response"] = {
            "status": flow.response.status_code,
            "statusText": flow.response.reason,
            "headers": dict(flow.response.headers),
            "body": body,
            "contentType": ct,
            "sizeBytes": len(flow.response.content),
        }

def done() -> None:
    har = {
        "log": {
            "version": "1.2",
            "creator": {"name": "mitmproxy-addon", "version": "1.0"},
            "entries": [],
        }
    }
    for idx, e in enumerate(entries, 1):
        har["log"]["entries"].append({
            "_trafficSnifferId": idx,
            "startedDateTime": e["timestamp"],
            "time": 0,
            "request": {
                "method": e["request"]["method"],
                "url": e["request"]["url"],
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in e["request"]["headers"].items()],
                "postData": {"mimeType": e["response"].get("contentType", ""), "text": e["request"]["postData"]} if e["request"]["postData"] else {},
            },
            "response": {
                "status": e["response"].get("status", 0),
                "statusText": e["response"].get("statusText", ""),
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in e["response"].get("headers", {}).items()],
                "content": {
                    "size": e["response"].get("sizeBytes", 0),
                    "mimeType": e["response"].get("contentType", ""),
                    "text": e["response"].get("body", "")[:100000],
                },
            },
        })
    with open("OUTPUT_PLACEHOLDER", "w") as f:
        json.dump(har, f, indent=2, ensure_ascii=False)
    print(f"[mitmproxy] HAR saved with {len(entries)} entries")
'''
        addon_code = addon_code.replace("OUTPUT_PLACEHOLDER", self.output)
        path = Path("_tmp_mitm_addon.py")
        path.write_text(addon_code)
        return str(path)

    def start(self):
        """Start mitmproxy in transparent mode."""
        addon_path = self._write_addon()
        cmd = [
            "mitmdump",
            "--listen-host", self.host,
            "--listen-port", str(self.port),
            "-s", addon_path,
            "--set", "stream_large_bodies=1m",
            "--set", "connection_strategy=lazy",
        ]
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.info("mitmproxy started on %s:%d", self.host, self.port)
        time.sleep(2)

    def stop(self):
        """Stop mitmproxy."""
        if self._process:
            self._process.terminate()
            self._process.wait(timeout=10)
            logger.info("mitmproxy stopped")

    def get_proxy_url(self) -> str:
        """Return proxy URL for httpx/requests/Selenium/Playwright."""
        return f"http://{self.host}:{self.port}"


# ---------------------------------------------------------------------------
# HAR parser / endpoint extractor
# ---------------------------------------------------------------------------

def har_to_endpoints(har_path: str, include_static: bool = False) -> Dict[str, List[str]]:
    """
    Parse a HAR file and extract API endpoints.

    Returns:
        {
            "api": ["/api/users", "/v1/search"],
            "graphql": ["/graphql"],
            "static": ["/static/js/main.js", "/images/logo.png"],
            "websocket": ["wss://..."],
        }
    """
    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)

    api = set()
    static = set()
    websocket = set()
    graphql = set()

    static_ext = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                  ".woff", ".woff2", ".ttf", ".ico", ".map")

    for entry in har.get("log", {}).get("entries", []):
        url = entry.get("request", {}).get("url", "")
        path = httpx.URL(url).path

        if url.startswith("ws://") or url.startswith("wss://"):
            websocket.add(url)
            continue

        if "/graphql" in path.lower() or "/graphiql" in path.lower():
            graphql.add(url)
            continue

        is_static = any(path.endswith(ext) for ext in static_ext)
        if is_static and not include_static:
            static.add(url)
            continue

        # Detect API paths
        if re.search(r"/api/|/v\d+/|/rest/|/graphql", path, re.IGNORECASE):
            api.add(url)
        elif re.search(r"\.(json|xml)$", path, re.IGNORECASE):
            api.add(url)
        elif entry.get("request", {}).get("method") in ("POST", "PUT", "PATCH", "DELETE"):
            # Non-GET requests are likely API
            api.add(url)

    return {
        "api": sorted(api),
        "graphql": sorted(graphql),
        "websocket": sorted(websocket),
        "static": sorted(static) if include_static else [],
    }


def har_to_csv(har_path: str, output: str = "output/traffic.csv"):
    """Flatten HAR to CSV for analysis in Excel/Sheets."""
    import csv

    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)

    rows = []
    for entry in har.get("log", {}).get("entries", []):
        req = entry.get("request", {})
        resp = entry.get("response", {})
        rows.append({
            "id": entry.get("_trafficSnifferId", 0),
            "timestamp": entry.get("startedDateTime", ""),
            "method": req.get("method", ""),
            "url": req.get("url", ""),
            "status": resp.get("status", 0),
            "content_type": resp.get("content", {}).get("mimeType", ""),
            "size_bytes": resp.get("content", {}).get("size", 0),
            "body_preview": resp.get("content", {}).get("text", "")[:200],
        })

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    return output


def har_to_sqlite(har_path: str, output: str = "output/traffic.db"):
    """Import HAR into SQLite for querying."""
    conn = sqlite3.connect(output)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            method TEXT,
            url TEXT,
            status INTEGER,
            content_type TEXT,
            size_bytes INTEGER,
            body_preview TEXT
        )
    """)

    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)

    for entry in har.get("log", {}).get("entries", []):
        req = entry.get("request", {})
        resp = entry.get("response", {})
        cur.execute("""
            INSERT OR IGNORE INTO requests
            (id, timestamp, method, url, status, content_type, size_bytes, body_preview)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.get("_trafficSnifferId", 0),
            entry.get("startedDateTime", ""),
            req.get("method", ""),
            req.get("url", ""),
            resp.get("status", 0),
            resp.get("content", {}).get("mimeType", ""),
            resp.get("content", {}).get("size", 0),
            resp.get("content", {}).get("text", "")[:500],
        ))

    conn.commit()
    conn.close()
    return output


# ---------------------------------------------------------------------------
# High-level capture functions
# ---------------------------------------------------------------------------

async def capture_har(url: str, output: str = "output/traffic.har",
                      backend: str = "playwright", wait: float = 5.0,
                      headless: bool = True) -> List[TrafficEntry]:
    """
    Capture network traffic and save as HAR.

    backends:
      - playwright: CDP-based capture (Chromium)
      - mitmproxy:  full proxy capture (all browsers)
    """
    if backend == "playwright":
        return await capture_with_playwright(url, output=output, wait=wait, headless=headless)

    elif backend == "mitmproxy":
        harvester = MitmproxyHarvester(output=output)
        harvester.start()
        try:
            # Navigate via httpx through the proxy
            proxies = {"http://": harvester.get_proxy_url(), "https://": harvester.get_proxy_url()}
            async with httpx.AsyncClient(proxies=proxies, verify=False, timeout=30) as client:
                await client.get(url)
                await asyncio.sleep(wait)
        finally:
            harvester.stop()
        return []

    else:
        raise ValueError(f"Unknown backend: {backend}")


def analyze_har(har_path: str) -> Dict[str, Any]:
    """
    Full HAR analysis: endpoints, status codes, content types, sizes.
    """
    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)

    entries = har.get("log", {}).get("entries", [])
    analysis = {
        "total_entries": len(entries),
        "methods": {},
        "status_codes": {},
        "content_types": {},
        "total_bytes": 0,
        "endpoints": har_to_endpoints(har_path),
    }

    for entry in entries:
        req = entry.get("request", {})
        resp = entry.get("response", {})
        method = req.get("method", "GET")
        status = resp.get("status", 0)
        ct = resp.get("content", {}).get("mimeType", "unknown")
        size = resp.get("content", {}).get("size", 0)

        analysis["methods"][method] = analysis["methods"].get(method, 0) + 1
        analysis["status_codes"][status] = analysis["status_codes"].get(status, 0) + 1
        analysis["content_types"][ct] = analysis["content_types"].get(ct, 0) + 1
        analysis["total_bytes"] += size

    return analysis


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import httpx  # imported here for har_to_endpoints
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Network traffic sniffer")
    parser.add_argument("url", nargs="?", help="Target URL to capture")
    parser.add_argument("--backend", choices=["playwright", "mitmproxy"], default="playwright")
    parser.add_argument("--output", default="output/traffic.har")
    parser.add_argument("--wait", type=float, default=5.0)
    parser.add_argument("--mode", choices=["capture", "endpoints", "csv", "analyze"])
    parser.add_argument("--har", help="Existing HAR file to analyze")
    args = parser.parse_args()

    if args.mode == "endpoints" and args.har:
        eps = har_to_endpoints(args.har)
        for category, urls in eps.items():
            if urls:
                print(f"\n=== {category.upper()} ===")
                for u in urls[:20]:
                    print(f"  {u}")

    elif args.mode == "csv" and args.har:
        csv_path = har_to_csv(args.har)
        print(f"CSV saved: {csv_path}")

    elif args.mode == "analyze" and args.har:
        report = analyze_har(args.har)
        print(json.dumps(report, indent=2))

    elif args.url:
        entries = asyncio.run(capture_har(
            args.url,
            output=args.output,
            backend=args.backend,
            wait=args.wait,
        ))
        print(f"\nCaptured {len(entries)} entries")
        print(f"HAR saved: {args.output}")

        # Auto-extract endpoints
        try:
            eps = har_to_endpoints(args.output)
            for cat, urls in eps.items():
                if urls:
                    print(f"\n{cat}: {len(urls)} found")
                    for u in urls[:10]:
                        print(f"  {u}")
        except Exception:
            pass

    else:
        parser.print_help()
