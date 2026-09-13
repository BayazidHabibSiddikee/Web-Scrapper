#!/usr/bin/env python3
"""
cookies.py
=========
Least-privilege cookie extraction from your local browsers — merged from
Agent-Reach/cookie_extract.py, but generalized to ANY domain (not a fixed
platform whitelist) so an agent can scrape authenticated pages.

Reads the cookie store of Chrome / Firefox / Edge / Brave / Opera and hands
you cookies scoped to exactly the domain you ask for. Never dumps everything.

Why: many pages (GitHub private, logged-in dashboards, sites behind a soft
login) only render for a real session. Instead of scripting a login (which
triggers 2FA/captcha), reuse the cookies you ALREADY have in your browser.

    python cookies.py list                                  # detected browsers
    python cookies.py extract github.com --browser chrome    # scoped cookies
    python cookies.py state github.com -o gh_state.json      # Playwright storage_state

    from cookies import get_cookies, auth_scrape, to_storage_state
    result = auth_scrape("https://github.com/settings/profile", browser="chrome")
    print(result.title, len(result.html))

SECURITY
    • Only the requested domain's cookies are read (least privilege).
    • Values are secrets: don't print or commit storage_state files.
    • Requires the browser to be CLOSED on some platforms (locked DB), and
      your OS keyring password on Linux Chrome/Edge (browser_cookie3 prompts).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("cookies")

SUPPORTED_BROWSERS = ("chrome", "firefox", "edge", "brave", "opera")

# SameStore attributes that Playwright's storage_state understands.
@dataclass
class Cookie:
    name: str
    value: str
    domain: str
    path: str = "/"
    expires: float = -1
    httpOnly: bool = False
    secure: bool = True
    sameSite: str = "Lax"

    def to_playwright(self) -> Dict[str, object]:
        return {"name": self.name, "value": self.value, "domain": self.domain,
                "path": self.path, "expires": self.expires,
                "httpOnly": self.httpOnly, "secure": self.secure,
                "sameSite": self.sameSite}


def _domain_matches(cookie_domain: str, want: str) -> bool:
    cd = (cookie_domain or "").lstrip(".").lower()
    want = want.lstrip(".").lower()
    return cd == want or want.endswith("." + cd) or cd.endswith("." + want)


def _load_jar(browser: str, domain: str) -> List[Cookie]:
    """Pull cookies for `domain` from `browser`, rookiepy-first then browser_cookie3."""
    browser = browser.lower()
    if browser not in SUPPORTED_BROWSERS:
        raise ValueError(f"unsupported browser {browser!r}; pick one of {SUPPORTED_BROWSERS}")

    # Fast path: rookiepy (Rust, no keyring prompts on some setups)
    try:
        import rookiepy
        fn = getattr(rookiepy, browser, None)
        if fn:
            raw = fn([f".{domain.lstrip('.')}"])
            return [Cookie(name=c.get("name", ""), value=c.get("value", ""),
                           domain=c.get("domain", ""), path=c.get("path", "/") or "/",
                           expires=float(c.get("expires", -1) or -1),
                           httpOnly=bool(c.get("httpOnly", False)),
                           secure=bool(c.get("secure", True)),
                           sameSite=_norm_samesite(c.get("sameSite")))
                    for c in raw]
    except Exception as exc:
        log.debug("rookiepy path unavailable (%s); trying browser_cookie3", exc)

    # Fallback: browser_cookie3
    try:
        import browser_cookie3
    except ImportError:
        raise RuntimeError(
            "cookie extraction needs browser_cookie3 (or rookiepy).\n"
            "Install: pip install browser-cookie3")
    getter = getattr(browser_cookie3, browser)
    jar = getter(domain_name=domain.lstrip("."))
    out = []
    for c in jar:
        if not _domain_matches(c.domain, domain):
            continue
        out.append(Cookie(name=c.name, value=c.value, domain=c.domain,
                         path=c.path or "/", expires=float(c.expires or -1),
                         httpOnly=bool(getattr(c, "_rest_only", False)),
                         secure=bool(getattr(c, "secure", True)),
                         sameSite="Lax"))
    return out


def _norm_samesite(v) -> str:
    if not v:
        return "Lax"
    v = str(v).capitalize()
    return v if v in ("Strict", "Lax", "None") else "Lax"


def get_cookies(domain: str, browser: str = "chrome") -> List[Cookie]:
    """Cookies scoped to `domain` from `browser`. Empty list if none/unreadable."""
    try:
        return _load_jar(browser, domain)
    except Exception as exc:
        log.warning("cookie read for %s in %s failed: %s", domain, browser, exc)
        return []


def cookie_header(domain: str, browser: str = "chrome") -> str:
    """'name=value; name2=value2' for direct HTTP requests."""
    return "; ".join(f"{c.name}={c.value}" for c in get_cookies(domain, browser))


def to_storage_state(domain: str, browser: str = "chrome",
                     origin_url: Optional[str] = None) -> Dict:
    """Build a Playwright storage_state dict (cookies only) for `domain`."""
    cookies = [c.to_playwright() for c in get_cookies(domain, browser)]
    state = {"cookies": cookies, "origins": []}
    if origin_url and cookies:
        state["origins"] = [{"origin": origin_url, "localStorage": []}]
    return state


def list_browsers() -> Dict[str, str]:
    """Which supported browsers have a readable cookie store on this machine."""
    found = {}
    for b in SUPPORTED_BROWSERS:
        try:
            probe = _load_jar(b, "example.com")
            found[b] = "readable"
        except Exception as exc:
            found[b] = str(exc)[:80]
    return found


def auth_scrape(url: str, browser: str = "chrome", wait: float = 3.0,
                screenshot: Optional[str] = None,
                full_page: bool = True) -> "object":
    """
    Scrape `url` reusing your browser's cookies for its host.

    Injects the scoped cookies into the Camoufox context (Playwright cookie
    dicts), so logged-in-only pages render without scripting 2FA.
    Returns a scraper.ScrapeResult.
    """
    from scraper import scrape, ScrapeConfig
    host = urlparse(url).netloc
    domain = host[4:] if host.startswith("www.") else host
    cookies = get_cookies(domain, browser)
    log.info("auth_scrape %s: %d cookie(s) from %s", domain, len(cookies), browser)

    result = scrape(ScrapeConfig(
        url=url, wait_seconds=wait, screenshot_path=screenshot,
        screenshot_full_page=full_page,
        cookies=[c.to_playwright() for c in cookies] or None,
    ))
    try:
        result.metadata["cookies_used"] = len(cookies)
        result.metadata["cookie_domain"] = domain
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Least-privilege browser cookie extraction")
    sub = ap.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list", help="which browsers are readable")

    x = sub.add_parser("extract", help="print cookie names/values for a domain (SECRET)")
    x.add_argument("domain")
    x.add_argument("-b", "--browser", default="chrome", choices=SUPPORTED_BROWSERS)
    x.add_argument("--header", action="store_true", help="emit a Cookie: header string")

    s = sub.add_parser("state", help="write a Playwright storage_state JSON (SECRET)")
    s.add_argument("domain")
    s.add_argument("-b", "--browser", default="chrome", choices=SUPPORTED_BROWSERS)
    s.add_argument("-o", "--output", default=None)

    a = sub.add_parser("scrape", help="scrape a URL using your browser's cookies")
    a.add_argument("url")
    a.add_argument("-b", "--browser", default="chrome", choices=SUPPORTED_BROWSERS)
    a.add_argument("-s", "--screenshot", default=None)

    args = ap.parse_args()

    if args.cmd == "list":
        print(json.dumps(list_browsers(), indent=2))
    elif args.cmd == "extract":
        if args.header:
            print(cookie_header(args.domain, args.browser))
        else:
            cs = get_cookies(args.domain, args.browser)
            print(json.dumps([{"name": c.name, "value": c.value, "domain": c.domain}
                              for c in cs], indent=2))
            print(f"\n{len(cs)} cookie(s) for {args.domain} in {args.browser} "
                  f"(SECRET — do not commit)", file=sys.stderr)
    elif args.cmd == "state":
        st = to_storage_state(args.domain, args.browser,
                              origin_url=f"https://{args.domain}")
        out = args.output or f"{args.domain.replace('.', '_')}_state.json"
        Path(out).write_text(json.dumps(st, indent=2))
        try:
            Path(out).chmod(0o600)
        except Exception:
            pass
        print(f"wrote {len(st['cookies'])} cookies → {out} (mode 600, SECRET)")
    elif args.cmd == "scrape":
        r = auth_scrape(args.url, browser=args.browser, screenshot=args.screenshot)
        print(f"URL: {r.url}\nTitle: {r.title}")
        print(f"cookies_used: {getattr(r,'metadata',{}).get('cookies_used')}")
        if r.error:
            print(f"[ERROR] {r.error}")
