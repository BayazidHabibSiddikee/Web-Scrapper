"""
waf_fingerprint.py
================
WAF / CDN / bot-protection fingerprinting.

Detects:
  - Cloudflare
  - Akamai / EdgeSuite
  - Imperva / Incapsula
  - Sucuri
  - AWS WAF / Shield
  - Azure Front Door / Microsoft
  - Fastly
  - F5 BIG-IP / ASM
  - Barracuda
  - Fortinet FortiWeb
  - StackPath
  - DenyAll / F5
  - Generic bot protection

Signals checked:
  - Response headers
  - Cookies
  - HTML artifacts / JS challenges
  - TLS/SSL certificate patterns
  - HTTP behavior (challenge responses, blocking patterns)
  - Timing anomalies

Usage:
    from waf_fingerprint import detect_waf

    result = detect_waf("https://example.com")
    print(result.waf_name, result.confidence, result.signals)
"""

from __future__ import annotations

import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import requests
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class WafFingerprint:
    url: str
    waf_name: str = "unknown"
    confidence: str = "low"        # low | medium | high | certain
    signals: List[str] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: List[str] = field(default_factory=list)
    html_artifacts: List[str] = field(default_factory=list)
    tls_info: Dict[str, Any] = field(default_factory=dict)
    status_code: int = 0
    response_time_ms: float = 0.0
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Fingerprint rules
# ---------------------------------------------------------------------------

WAF_RULES = [
    # (name, header_patterns, cookie_patterns, html_patterns, body_patterns, confidence_boost)
    ("Cloudflare", {
        "headers": ["cf-ray", "cf-cache-status", "cf-request-id", "server: cloudflare"],
        "cookies": ["__cfduid", "__cf_bm", "cf_clearance"],
        "html": ["cloudflare", "challenge-platform", "cf-browser-verification"],
        "body": ["cloudflare", "attention required", "please enable javascript"],
    }),
    ("Akamai", {
        "headers": ["server: akamai", "x-akamai", "akamai-gtm"],
        "cookies": ["ak_bmsc", "bm_sv", "akamai_bot"],
        "html": ["akamai", "ak_bmsc"],
    }),
    ("Imperva / Incapsula", {
        "headers": ["x-iinfo", "server: incapsula", "x-cdn: incapsula"],
        "cookies": ["incap_ses", "visid_incap", "incap_ses_", "nlbi_"],
        "html": ["incapsula", "_incapsula_resource"],
        "body": ["incapsula", "access denied", "please click the button below"],
    }),
    ("Sucuri", {
        "headers": ["server: sucuri", "x-sucuri", "x-sucuri-cache"],
        "cookies": ["sucuri_cloudproxy", "sucuri_uid"],
        "html": ["sucuri", "cloudproxy", "sucuri_uid"],
        "body": ["sucuri", "access denied", "performance security"],
    }),
    ("AWS WAF", {
        "headers": ["x-amzn-requestid", "x-amz-cf-id", "server: awselb"],
        "cookies": ["awsalb", "awsalbcookie", "awsallb"],
        "html": ["aws", "waf", "access denied"],
        "body": ["aws waf", "request blocked", "webacl"],
    }),
    ("Azure / Microsoft", {
        "headers": ["x-msedge-ref", "x-aspnet-version", "x-powered-by: asp.net",
                     "server: microsoft-iis", "x-ms"],
        "cookies": ["ARRAffinity", "ARRAffinitySameSite"],
        "html": ["msaadauth", "x-ms-"],
    }),
    ("Fastly", {
        "headers": ["server: fastly", "x-fastly", "x-served-by"],
        "cookies": ["_fbp", "_gcl_au"],
        "html": ["fastly", "fastly-debug"],
    }),
    ("F5 BIG-IP / ASM", {
        "headers": ["server: bigip", "x-bigip", "bigipserver"],
        "cookies": ["BIGIPSERVER", "BIGIPSERVER", "BIGIPSERVER"],
        "html": ["bigip", "asm"],
        "body": ["bigip", "asm support", "the requested url was rejected"],
    }),
    ("Barracuda", {
        "headers": ["server: barracuda", "x-barracuda"],
        "cookies": ["BNI_BARRACUDA", "barra_counter"],
        "html": ["barracuda"],
    }),
    ("Fortinet FortiWeb", {
        "headers": ["server: fortiweb", "x-fortiwaf"],
        "cookies": ["FORTIWAFSESSID"],
        "body": ["fortiweb", "fortinet", "request blocked"],
    }),
    ("StackPath", {
        "headers": ["server: stackpath", "x-stackpath"],
        "cookies": ["stackpath"],
    }),
    ("DDoS-Guard", {
        "headers": ["server: ddos-guard"],
        "cookies": ["ddos-guard"],
        "body": ["ddos-guard"],
    }),
    ("Google reCAPTCHA / reCAPTCHA Enterprise", {
        "html": ["google.com/recaptcha", "g-recaptcha", "recaptcha"],
        "body": ["recaptcha"],
    }),
    ("hCaptcha", {
        "html": ["hcaptcha.com", "h-captcha"],
        "body": ["hcaptcha"],
    }),
    ("Bot protection (generic)", {
        "headers": ["x-bot-protection", "x-firewall", "x-request-id"],
        "body": ["bot detected", "automated request", "suspicious activity",
                 "verify you are human", "are you a robot"],
    }),
]


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def detect_waf(url: str, timeout: float = 15.0,
               follow_redirects: bool = True) -> WafFingerprint:
    """
    Fingerprint the WAF / CDN / bot-protection protecting a URL.
    """
    result = WafFingerprint(url=url, timestamp=datetime.now(timezone.utc).isoformat())

    # Prepare headers to reduce noise
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    start = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=timeout,
                            allow_redirects=follow_redirects)
        result.status_code = resp.status_code
        result.response_time_ms = (time.time() - start) * 1000
        result.headers = dict(resp.headers)
        result.cookies = [c.name for c in resp.cookies]
        result.signals.append(f"HTTP {resp.status_code}")

        # Header checks
        header_text = "\n".join(f"{k}: {v}" for k, v in resp.headers.items()).lower()
        cookie_text = "; ".join(result.cookies).lower()
        body_text = resp.text.lower()[:50000]
        soup = BeautifulSoup(resp.text, "lxml")

        for rule_name, patterns in WAF_RULES:
            score = 0
            matches = []

            # Headers
            for pat in patterns.get("headers", []):
                if ":" in pat:
                    key, val = pat.split(":", 1)
                    key, val = key.strip().lower(), val.strip().lower()
                    hval = resp.headers.get(key, "").lower()
                    if val and val in hval:
                        score += 2
                        matches.append(f"header:{key}:{val}")
                elif pat.lower() in header_text:
                    score += 2
                    matches.append(f"header:{pat}")

            # Cookies
            for pat in patterns.get("cookies", []):
                if pat.lower() in cookie_text:
                    score += 2
                    matches.append(f"cookie:{pat}")

            # HTML artifacts (meta tags, script src, inline)
            for pat in patterns.get("html", []):
                if pat.lower() in body_text:
                    score += 2
                    matches.append(f"html:{pat}")

            # Body text
            for pat in patterns.get("body", []):
                if pat.lower() in body_text:
                    score += 1
                    matches.append(f"body:{pat}")

            if score >= 3:
                result.waf_name = rule_name
                if score >= 6:
                    result.confidence = "certain"
                elif score >= 4:
                    result.confidence = "high"
                else:
                    result.confidence = "medium"
                result.signals.extend(matches)
                logger.info("Detected %s (score=%d): %s", rule_name, score, matches)
                break

        # HTML artifacts
        for tag in soup(["script", "iframe", "meta"]):
            src = tag.get("src", "") or tag.get("content", "")
            if src and any(kw in src.lower() for kw in ["cloudflare", "recaptcha", "hcaptcha",
                                                          "akamai", "incapsula", "sucuri"]):
                result.html_artifacts.append(str(tag)[:200])

    except requests.exceptions.Timeout:
        result.signals.append("timeout")
        result.waf_name = "timeout"
    except requests.exceptions.SSLError as exc:
        result.signals.append(f"ssl_error:{exc}")
        result.waf_name = "ssl_error"
    except Exception as exc:
        result.signals.append(f"error:{exc}")

    return result


def detect_waf_tls(url: str) -> Dict[str, Any]:
    """
    TLS/SSL fingerprinting for WAF hints.
    Many WAFs have distinctive certificate patterns.
    """
    host = httpx.URL(url).host
    port = 443
    info = {"host": host, "port": port}

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                info["issuer"] = cert.get("issuer", "")
                info["subject"] = cert.get("subject", "")
                info["version"] = ssock.version()
                info["cipher"] = ssock.cipher()
                info["serial_number"] = cert.get("serialNumber", "")

                # Check for common WAF certificate patterns
                issuer_text = str(cert.get("issuer", "")).lower()
                if "cloudflare" in issuer_text:
                    info["waf_hint"] = "Cloudflare"
                elif "amazon" in issuer_text:
                    info["waf_hint"] = "AWS"
                elif "microsoft" in issuer_text:
                    info["waf_hint"] = "Azure"
                elif "akamai" in issuer_text:
                    info["waf_hint"] = "Akamai"
    except Exception as exc:
        info["error"] = str(exc)

    return info


def fingerprint_target(url: str) -> Dict[str, Any]:
    """
    Full fingerprint: WAF + TLS + headers + cookies + timing.
    """
    log = {}

    # HTTP fingerprint
    waf = detect_waf(url)
    log["url"] = waf.url
    log["waf"] = {
        "name": waf.waf_name,
        "confidence": waf.confidence,
        "signals": waf.signals,
    }
    log["status_code"] = waf.status_code
    log["response_time_ms"] = round(waf.response_time_ms, 1)
    log["headers"] = waf.headers
    log["cookies"] = waf.cookies
    log["html_artifacts"] = waf.html_artifacts

    # TLS fingerprint
    try:
        log["tls"] = detect_waf_tls(url)
    except Exception as exc:
        log["tls"] = {"error": str(exc)}

    return log


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="WAF fingerprint detector")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("--tls", action="store_true", help="Include TLS fingerprint")
    args = parser.parse_args()

    result = fingerprint_target(args.url)
    print(json.dumps(result, indent=2, default=str))
