#!/usr/bin/env python3
"""
captcha_flow.py
==============
Complete live-page CAPTCHA workflow — **free-first**:

    Cloudflare interstitial / Turnstile  → Scrapling solve_cloudflare (NO key, NO cost)
    reCAPTCHA v2/v3, hCaptcha            → 2captcha/anti-captcha (paid key required)

The free path uses Scrapling's AsyncStealthySession(solve_cloudflare=True,
humanize=True), which passes the CF challenge in-browser and even clicks
interactive Turnstile boxes. The same live browser context is then reused for
pre-filling forms and clicking submit — so a login flow behind Cloudflare works
end-to-end with zero API keys. Only when a real reCAPTCHA/hCaptcha widget
survives the free pass does the flow escalate to the paid solver (and only if
CAPTCHA_API_KEY is set).

    from captcha_flow import solve_captcha_on_page

    # Free: CF-protected login
    result = solve_captcha_on_page("https://site.com/login",
                                   pre_fill={"email": "me@x.com", "password": "s3cret"},
                                   submit_selector="button[type=submit]")

    # Paid (reCAPTCHA v2/v3, hCaptcha)
    export CAPTCHA_API_KEY=***
    python captcha_flow.py https://site.com/form --type recaptcha

CLI:
    python captcha_flow.py URL --detect-only     # inspect widgets (free)
    python captcha_flow.py URL                   # free-first, paid escalation
    python captcha_flow.py URL --paid-only       # skip the Scrapling free pass
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from security_utils import assert_public_url

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("captcha_flow")

# Detect widget type + site keys from the live DOM (one JS pass).
DETECT_JS = """
() => {
  const out = [];
  document.querySelectorAll('[data-sitekey]').forEach(el => {
    const wrap = (el.closest('.g-recaptcha') ? 'recaptcha' :
                  el.closest('.h-captcha') ? 'hcaptcha' :
                  el.closest('.cf-turnstile') ? 'turnstile' : null);
    out.push({type: wrap || 'unknown', sitekey: el.getAttribute('data-sitekey')});
  });
  document.querySelectorAll('iframe').forEach(fr => {
    const src = fr.src || '';
    if (src.includes('recaptcha/api2/anchor') || src.includes('/recaptcha/enterprise/'))
      out.push({type: 'recaptcha', sitekey: (src.match(/[?&]k=([^&]+)/) || [])[1] || null});
    else if (src.includes('challenges.cloudflare.com') || src.includes('/turnstile/'))
      out.push({type: 'turnstile', sitekey: (src.match(/[?&]sitekey=([^&#]+)/) || [])[1] || null});
    else if (src.includes('hcaptcha.com/__state') || src.includes('newassets.hcaptcha.com'))
      out.push({type: 'hcaptcha', sitekey: (src.match(/proxy=([^&#]+)/) || [])[1] || null});
  });
  if (window.grecaptcha && typeof grecaptcha.execute === 'function' && !out.some(o => o.type==='recaptcha'))
    out.push({type: 'recaptcha-v3-maybe', sitekey: null});
  const seen = new Set();
  return out.filter(o => { const k = o.type + '|' + o.sitekey;
                            if (seen.has(k)) return false; seen.add(k); return true; });
}
"""

# Token injection per provider — writes hidden response fields so the
# surrounding form sees a solved captcha on submit.
_INJECT_JS = """
(kind, token) => {
  const set = (el, v) => { el.value = v; el.innerHTML = v;
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true})); };
  if (kind === 'recaptcha') {
    ['g-recaptcha-response', 'g-recaptcha-response-100000'].forEach(id => {
      const ta = document.getElementById(id); if (ta) set(ta, token); });
    return true;
  }
  if (kind === 'hcaptcha') {
    const ta = document.getElementById('h-captcha-response');
    if (ta) set(ta, token); return !!ta;
  }
  if (kind === 'turnstile') {
    const ta = document.getElementsByName('cf-turnstile-response')[0];
    if (ta) set(ta, token); return !!ta;
  }
  return false;
}
"""

_CF_MARKERS = (b"just a moment", b"cf-browser-verification", b"challenge-platform",
               b"attention required")


def _looks_challenged(body) -> bool:
    if isinstance(body, str):
        body = body.encode("utf-8", "ignore")
    sample = (body or b"")[:6000].lower()
    return any(m in sample for m in _CF_MARKERS)


@dataclass
class CaptchaFlowResult:
    url: str
    detected: list = field(default_factory=list)
    solved_type: Optional[str] = None      # cloudflare-free | turnstile-free | recaptcha | hcaptcha | turnstile
    paid: bool = False                     # consumed solver credits?
    token: Optional[str] = None
    injected: bool = False
    submitted: bool = False
    session: dict = field(default_factory=dict)   # free-pass metadata
    final_url: str = ""
    screenshot_path: Optional[str] = None
    error: Optional[str] = None


def _solver():
    from examples.captcha_solver.captcha_solver import CaptchaSolver
    return CaptchaSolver(service=os.getenv("CAPTCHA_SERVICE", "2captcha"),
                         api_key=os.getenv("CAPTCHA_API_KEY", ""))


def detect_captchas(page) -> list:
    """Return [{'type','sitekey'}, ...] widgets present on a Playwright page."""
    return _run_sync_page(page)


def _run_sync_page(page) -> list:
    import asyncio, concurrent.futures

    async def _go():
        return await page.evaluate(DETECT_JS)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_go())
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, _go).result()


async def _open_scrapling(url: str, headless: bool, timeout_ms: int):
    """
    Free pass: Scrapling's AsyncStealthySession with solve_cloudflare.
    Solves CF interstitials AND clicks interactive Turnstile boxes.
    Returns (page, cleanup, meta) — the page is a FRESH page in the solved
    context, so cookies/turnstile clearance carry over for form interaction.
    """
    from scrapling.fetchers import AsyncStealthySession
    session = AsyncStealthySession(headless=headless, solve_cloudflare=True,
                                   humanize=True, os_randomize=True,
                                   timeout=timeout_ms)
    await session.start()
    meta = {"engine": "scrapling-stealth+cf", "challenge_cleared": None,
            "first_status": None}
    try:
        resp = await session.fetch(url)
        meta["first_status"] = getattr(resp, "status", None)
        meta["challenge_cleared"] = (meta["first_status"] == 200
                                     and not _looks_challenged(getattr(resp, "body", b"")))
        final = assert_public_url(getattr(resp, "url", None) or url)
        page = await session.context.new_page()
        await page.goto(final, wait_until="domcontentloaded", timeout=timeout_ms / 1000)
        await page.wait_for_timeout(1200)

        async def cleanup():
            try:
                await session.close()
            except Exception:
                pass
        return page, cleanup, meta
    except Exception:
        try:
            await session.close()
        except Exception:
            pass
        raise


async def _open_fallback(url: str, headless: bool):
    """Plain Camoufox (own CF handling is weaker) or Chromium."""
    try:
        from camoufox.async_api import AsyncCamoufox
        cm = AsyncCamoufox(headless=headless, args=["--no-sandbox"])
        browser = await cm.__aenter__()
        ctx = await browser.new_context(locale="en-US")
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2000)
        meta = {"engine": "camoufox"}
        return page, (lambda: cm.__aexit__(None, None, None)), meta
    except Exception as exc:
        log.warning("Camoufox unavailable (%s), using plain Playwright", exc)
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=headless, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)

        async def cleanup():
            await browser.close()
            await pw.stop()
        return page, cleanup, {"engine": "playwright"}


async def _solve_async(
    url: str = None,
    page=None,
    captcha_type: str = "auto",       # auto|recaptcha|recaptcha-v3|hcaptcha|turnstile|cloudflare
    site_key: str = None,
    action: str = "submit",           # v3 action hint
    min_score: float = 0.3,           # v3 threshold
    pre_fill: Optional[dict] = None,  # fields to fill after solving (login flows)
    submit_selector: Optional[str] = None,
    free_first: bool = True,          # try Scrapling's keyless CF/Turnstile solver
    solve_timeout: int = 90,
    headless: bool = True,
    screenshot: Optional[str] = None,
) -> CaptchaFlowResult:
    """
    Free-first CAPTCHA flow:
      1. Scrapling solve_cloudflare (free) clears CF interstitials / clicks Turnstile
      2. re-detect remaining widgets on the now-authenticated context
      3. reCAPTCHA / hCaptcha that survives → paid solver (needs CAPTCHA_API_KEY)
      4. optional pre_fill + submit in the same session
    """
    res = CaptchaFlowResult(url=url or "")
    own_page = page is None
    cleanup = None
    timeout_ms = max(int(solve_timeout), 60) * 1000
    try:
        if own_page:
            if free_first:
                try:
                    page, cleanup, meta = await _open_scrapling(url, headless, timeout_ms)
                    res.session = meta
                    log.info("Free CF pass: status=%s cleared=%s",
                             meta.get("first_status"), meta.get("challenge_cleared"))
                except Exception as exc:
                    log.warning("Scrapling free pass failed (%s) — falling back", exc)
                    page, cleanup, meta = await _open_fallback(url, headless)
                    res.session = meta
            else:
                page, cleanup, meta = await _open_fallback(url, headless)
                res.session = meta
        res.url = page.url

        widgets = await page.evaluate(DETECT_JS)
        res.detected = widgets

        hard = [w for w in widgets if w["type"] in ("recaptcha", "hcaptcha")
                and w.get("sitekey")]
        cf_like = [w for w in widgets if w["type"] in ("turnstile", "unknown")]

        widget = None
        kind = ""
        if captcha_type == "auto":
            if hard:
                widget = hard[0]
                kind = widget["type"]
            elif cf_like:
                widget = cf_like[0]
                kind = "turnstile"
        else:
            widget = next((w for w in widgets if w["type"].startswith(captcha_type)), None)
            kind = captcha_type

        key = site_key or (widget or {}).get("sitekey")

        # ── Nothing left to solve: the free pass did it all (or there was no captcha)
        async def _snap():
            if screenshot:
                Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
                try:
                    await page.screenshot(path=screenshot, full_page=True)
                    res.screenshot_path = screenshot
                except Exception:
                    pass

        if not key and not hard:
            cleared = res.session.get("challenge_cleared")
            if cleared or (widget and kind == "turnstile" and res.session.get("engine", "").startswith("scrapling")):
                res.solved_type = "cloudflare-free" if cleared else "turnstile-free"
                res.injected = True   # clearance lives in the session cookies
                log.info("Solved for free via Scrapling (%s)", res.solved_type)
            else:
                res.error = (f"no captcha widget detected (found {len(widgets)}: "
                             f"{[w.get('type') for w in widgets]}) — pass site_key="
                             f"explicitly or check the page")
                await _snap()
                return res
        elif kind == "turnstile" and res.session.get("challenge_cleared") \
                and res.session.get("engine", "").startswith("scrapling"):
            # Scrapling's free solver clicks interactive Turnstile boxes in the
            # same session. Verify the response token actually landed before
            # claiming it solved — test widgets (3x… sitekeys) pass trivially,
            # real ones must yield a cf-turnstile-response value.
            has_token = await page.evaluate(
                "() => { const t = document.getElementsByName('cf-turnstile-response')[0];"
                " return !!(t && t.value && t.value.length > 20); }")
            res.solved_type = "turnstile-free" if has_token else "cloudflare-free"
            res.injected = has_token
            res.session["turnstile_token_present"] = has_token
            log.info("Turnstile free pass: token_present=%s", has_token)
            if not has_token and os.getenv("CAPTCHA_API_KEY"):
                # Free click didn't yield a usable token — escalate to paid
                log.info("Escalating Turnstile to paid solver")
                solver = _solver()
                r = solver.solve_turnstile(site_key=key, page_url=res.url)
                if r.success:
                    res.solved_type, res.token, res.paid, res.injected = \
                        "turnstile", r.token, True, \
                        await page.evaluate(_INJECT_JS, "turnstile", r.token)
            elif not has_token and submit_selector:
                log.warning("Turnstile token absent and no CAPTCHA_API_KEY; continuing "
                            "(page is readable). Set the key to escalate to paid solving.")
        else:
            # ── reCAPTCHA / hCaptcha / paid Turnstile escalation
            if not kind.startswith(("recaptcha", "hcaptcha", "turnstile")):
                kind = "recaptcha" if hard else "turnstile"
            if not os.getenv("CAPTCHA_API_KEY"):
                res.error = (f"{kind} widget needs the PAID solver but CAPTCHA_API_KEY is unset. "
                             f"Cloudflare/Turnstile challenges were handled free; see README "
                             f"§ CAPTCHA caveats. sitekey seen: {key[:16]}...")
                return res
            solver = _solver()
            log.info("Escalating %s (sitekey=%s...) to %s", kind, key[:12],
                     solver._solver.__class__.__name__)
            if kind.startswith("recaptcha"):
                r = solver.solve_recaptcha_v3(site_key=key, page_url=res.url,
                                              action=action, min_score=min_score) \
                    if "v3" in kind else \
                    solver.solve_recaptcha_v2(site_key=key, page_url=res.url)
            elif kind == "hcaptcha":
                r = solver.solve_hcaptcha(site_key=key, page_url=res.url)
            else:
                r = solver.solve_turnstile(site_key=key, page_url=res.url)
            if not r.success:
                res.error = f"solver failed: {r.error}"
                return res
            res.solved_type, res.token, res.paid = kind, r.token, True
            inject_kind = "recaptcha" if kind.startswith("recaptcha") else kind
            res.injected = await page.evaluate(_INJECT_JS, inject_kind, r.token)
            log.info("Token injected=%s (%.1fs)", res.injected, r.elapsed_seconds)

        # ── Interaction in the solved session: fill + submit
        if pre_fill:
            for name, value in pre_fill.items():
                try:
                    await page.fill(f'[name="{name}"]', str(value), timeout=3000)
                except Exception:
                    try:
                        await page.fill(f'#{name}', str(value), timeout=1500)
                    except Exception as e:
                        log.warning("pre_fill %s failed: %s", name, e)

        if submit_selector:
            try:
                async with page.expect_navigation(timeout=20000):
                    await page.click(submit_selector, timeout=5000)
                res.submitted = True
            except Exception:
                try:
                    await page.click(submit_selector, timeout=3000)
                    res.submitted = True
                except Exception as e:
                    res.error = f"submit click failed after solving: {e}"
        await page.wait_for_timeout(2500)
        res.final_url = page.url

        if screenshot:
            Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=screenshot, full_page=True)
            res.screenshot_path = screenshot
        return res
    except Exception as exc:
        res.error = str(exc)
        return res
    finally:
        if own_page and cleanup:
            try:
                await cleanup()
            except Exception:
                pass


def solve_captcha_on_page(url: str = None, page=None, **kwargs) -> CaptchaFlowResult:
    """Sync wrapper (loop-safe, same pattern as scraper.scrape)."""
    import asyncio, concurrent.futures
    if url:
        url = assert_public_url(url)
    coro = _solve_async(url=url, page=page, **kwargs)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(
        description="Free-first CAPTCHA flow: Scrapling CF/Turnstile solver, "
                    "paid reCAPTCHA/hCaptcha escalation (needs CAPTCHA_API_KEY)")
    ap.add_argument("url")
    ap.add_argument("--type", default="auto",
                    choices=["auto", "cloudflare", "recaptcha", "recaptcha-v3",
                             "hcaptcha", "turnstile"])
    ap.add_argument("--site-key", default=None)
    ap.add_argument("--action", default="submit", help="reCAPTCHA v3 action hint")
    ap.add_argument("--pre-fill", default=None, help='JSON: {"username":"u","password":"***"}')
    ap.add_argument("--submit", default=None, help="CSS selector to click after solving")
    ap.add_argument("--paid-only", action="store_true",
                    help="skip Scrapling's free solve_cloudflare pass")
    ap.add_argument("--detect-only", action="store_true",
                    help="open page and list widgets; no key needed")
    ap.add_argument("-s", "--screenshot", default="output/captcha_flow.png")
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args()

    if args.detect_only:
        import asyncio
        async def _detect():
            page, cleanup, meta = await _open_scrapling(args.url, not args.no_headless, 90000)
            try:
                w = await page.evaluate(DETECT_JS)
                if args.screenshot:
                    Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=args.screenshot, full_page=True)
                return meta, w
            finally:
                await cleanup()
        meta, widgets = asyncio.run(_detect())
        print(json.dumps({"session": meta, "widgets": widgets}, indent=2))
        sys.exit(0)

    res = solve_captcha_on_page(
        args.url, captcha_type=args.type, site_key=args.site_key, action=args.action,
        pre_fill=json.loads(args.pre_fill) if args.pre_fill else None,
        submit_selector=args.submit, screenshot=args.screenshot,
        headless=not args.no_headless, free_first=not args.paid_only)
    print(json.dumps({"detected": res.detected, "solved_type": res.solved_type,
                      "paid": res.paid, "injected": res.injected,
                      "submitted": res.submitted, "session": res.session,
                      "final_url": res.final_url, "screenshot": res.screenshot_path,
                      "error": res.error, "token_head": (res.token or "")[:40]},
                     indent=2, ensure_ascii=False))
