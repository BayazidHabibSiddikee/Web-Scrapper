#!/usr/bin/env python3
"""
form_fill.py
===========
Browser form auto-detection, filling, and submission — code-driven (no LLM
needed, but shaped so an AI agent can call it: extract a JSON schema of the
form, then pass values back).

Built on the toolkit's stealth stack (Camoufox / Playwright) so forms behind
Cloudflare don't reject the driver.

Agent workflow:
    from form_fill import extract_forms, fill_form

    forms = extract_forms("https://httpbin.org/forms/post")
    # → [{form_index:0, action:..., fields:[{name,type,required,selector,options}...]}]

    result = fill_form("https://httpbin.org/forms/post",
                       values={"custname": "John Doe", "custemail": "j@x.com",
                               "size": "Medium", "topping": ["cheese", "bacon"],
                               "delivery": "now", "comments": "hi"},
                       submit=True)
    print(result.ok, result.final_url, result.screenshot_path)

Multi-form pages: pass form_index= to pick one. Field matching accepts
name=, id=, placeholder=, aria-label=, and visible <label for=...> text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# JS run inside the page to enumerate every form + its fields.
_EXTRACT_JS = """
() => {
  const norm = s => (s || '').trim().replace(/\\s+/g, ' ');
  const forms = Array.from(document.querySelectorAll('form'));
  const root = forms.length ? null : document;   // fields outside <form> too
  const targets = forms.length ? forms : [document];
  return targets.map((f, fi) => {
    const fields = [];
    const inputs = f.querySelectorAll('input, select, textarea');
    inputs.forEach((el, ii) => {
      const type = (el.type || el.tagName.toLowerCase()).toLowerCase();
      if (['hidden','submit','button','image','reset'].includes(type)) return;
      if (el.disabled || el.readOnly) return;
      let label = norm(el.getAttribute('aria-label'));
      if (!label && el.labels && el.labels.length) label = norm(el.labels[0].textContent);
      if (!label) {
        const prev = el.closest('p,div,label,td,li');
        if (prev) {
          const t = norm(prev.textContent);
          if (t && t.length < 80) label = t;
        }
      }
      const opts = (type === 'select' || type === 'select-one' || type === 'select-multiple')
        ? Array.from(el.options).slice(0, 60).map(o => norm(o.value || o.textContent))
            .filter(Boolean)
        : (type === 'radio' || type === 'checkbox')
        ? [norm(el.value)] : [];
      fields.push({
        index: ii,
        tag: el.tagName.toLowerCase(),
        type,
        name: el.name || null,
        id: el.id || null,
        placeholder: norm(el.getAttribute('placeholder')) || null,
        label: label || null,
        required: el.required || el.getAttribute('aria-required') === 'true',
        options: [...new Set(opts)].slice(0, 40),
        multiple: el.multiple || false,
        value: norm(el.value) || null,
      });
    });
    return {
      form_index: fi,
      id: f.id || null,
      action: f.action || (f.getAttribute && f.getAttribute('action')) || null,
      method: (f.method || 'get').toLowerCase(),
      is_implicit: !forms.length,
      fields,
    };
  });
}
"""

# Builds a selector for one field element inside the page.
_RESOLVE_JS = """
(spec) => {
  const esc = CSS.escape ? CSS.escape : (s => s.replace(/[^a-zA-Z0-9_-]/g, '\\\\$&'));
  const forms = Array.from(document.querySelectorAll('form'));
  const container = (spec.form_index != null && forms[spec.form_index])
      ? forms[spec.form_index] : document;
  let el = null;
  const tries = [];
  if (spec.name) tries.push(() => container.querySelector(`[name="${esc(spec.name)}"]`));
  if (spec.id) tries.push(() => container.querySelector(`#${esc(spec.id)}`));
  if (spec.index != null && (spec.name || spec.id || spec.label || spec.placeholder))
    ; // index used only as fallback below
  if (spec.label) tries.push(() => {
      for (const l of container.querySelectorAll('label')) {
        if (l.textContent.trim().replace(/\\s+/g,' ').includes(spec.label) && l.htmlFor) {
          const t = document.getElementById(l.htmlFor); if (t) return t;
        }
        const inner = l.querySelector('input,select,textarea');
        if (inner && l.textContent.trim().replace(/\\s+/g,' ').includes(spec.label)) return inner;
      }
      return null;
  });
  if (spec.placeholder) tries.push(() =>
      container.querySelector(`[placeholder*="${spec.placeholder}"]`));
  if (spec.index != null) tries.push(() => {
      const inputs = Array.from(container.querySelectorAll(
          'input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea'));
      return inputs[spec.index] || null;
  });
  for (const t of tries) { try { el = t(); } catch(e) {} if (el) break; }
  if (!el) return null;
  // Give it a stable data attribute so Playwright can target exactly this node
  el.setAttribute('data-ws-field', spec.key);
  return spec.key;
}
"""


@dataclass
class FormField:
    name: Optional[str]
    type: str
    required: bool = False
    label: Optional[str] = None
    placeholder: Optional[str] = None
    id: Optional[str] = None
    index: Optional[int] = None
    options: List[str] = field(default_factory=list)
    multiple: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type, "required": self.required,
                "label": self.label, "placeholder": self.placeholder,
                "id": self.id, "index": self.index,
                "options": self.options, "multiple": self.multiple}


@dataclass
class FormSpec:
    form_index: int
    action: Optional[str]
    method: str
    fields: List[FormField]
    is_implicit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"form_index": self.form_index, "action": self.action,
                "method": self.method, "is_implicit": self.is_implicit,
                "fields": [f.to_dict() for f in self.fields]}


@dataclass
class FillResult:
    ok: bool = False
    filled: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    missing_required: List[str] = field(default_factory=list)
    final_url: str = ""
    response_text: str = ""
    screenshot_path: Optional[str] = None
    error: Optional[str] = None


async def _page_factory(url: str, headless: bool, use_camoufox: bool):
    """Open the target in Camoufox (default) or plain Playwright Chromium."""
    if use_camoufox:
        try:
            from camoufox.async_api import AsyncCamoufox
            cm = AsyncCamoufox(headless=headless,
                               args=["--disable-blink-features=AutomationControlled",
                                     "--no-sandbox"])
            browser = await cm.__aenter__()
            ctx = await browser.new_context(locale="en-US")
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            return page, lambda: cm.__aexit__(None, None, None)
        except Exception as exc:
            log.warning("Camoufox unavailable (%s) — falling back to Playwright", exc)
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless,
                                       args=["--disable-blink-features=AutomationControlled",
                                             "--no-sandbox"])
    page = await browser.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(1000)
    return page, lambda: _cleanup(pw, browser)


async def _cleanup(pw, browser):
    try:
        await browser.close()
        await pw.stop()
    except Exception:
        pass


def _run(coro):
    """Run an async coroutine from sync land, loop-safe (same trick as scraper.py)."""
    import asyncio, concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


async def _extract_async(url: str, headless: bool) -> List[FormSpec]:
    page, cleanup = await _page_factory(url, headless, use_camoufox=True)
    try:
        raw = await page.evaluate(_EXTRACT_JS)
    finally:
        await cleanup()
    forms = []
    for f in raw:
        fields = [FormField(name=x.get("name"), type=x.get("type", "text"),
                            required=bool(x.get("required")), label=x.get("label"),
                            placeholder=x.get("placeholder"), id=x.get("id"),
                            index=x.get("index"), options=x.get("options") or [],
                            multiple=bool(x.get("multiple")))
                  for x in f.get("fields", [])]
        forms.append(FormSpec(form_index=f["form_index"], action=f.get("action"),
                              method=f.get("method", "get"), fields=fields,
                              is_implicit=bool(f.get("is_implicit"))))
    return forms


def extract_forms(url: str, headless: bool = True) -> List[FormSpec]:
    """Return the list of forms on the page (empty list = no page/no forms)."""
    return _run(_extract_async(url, headless))


async def _fill_async(url: str, values: Dict[str, Any], submit: bool, form_index: int,
                      headless: bool, screenshot: Optional[str],
                      after_wait: float) -> FillResult:
    page, cleanup = await _page_factory(url, headless, use_camoufox=True)
    result = FillResult()
    try:
        forms = await page.evaluate(_EXTRACT_JS)
        fdata = forms[form_index] if form_index < len(forms) else (forms[0] if forms else None)
        by_key: Dict[str, Dict] = {}
        if fdata:
            for x in fdata["fields"]:
                for k in filter(None, (x.get("name"), x.get("id"))):
                    by_key[k.lower()] = x

        for key, value in values.items():
            spec = by_key.get(str(key).lower())
            ftype = (spec or {}).get("type", "text")
            handle = {"name": (spec or {}).get("name"), "id": (spec or {}).get("id"),
                      "label": None, "placeholder": None,
                      "index": (spec or {}).get("index") if spec is None else None,
                      "form_index": form_index, "key": f"ws_{abs(hash((key,))) % 10**8}"}
            # allow label-based lookup too
            if spec is None and fdata:
                for x in fdata["fields"]:
                    if key.lower() in (x.get("label") or "").lower():
                        spec = x
                        handle.update({"name": x.get("name"), "id": x.get("id"),
                                       "index": x.get("index")})
                        ftype = x.get("type", "text")
                        break
            sel = await page.evaluate(_RESOLVE_JS, handle)
            if not sel:
                result.failed.append(f"{key} (field not found)")
                continue
            try:
                el = page.locator(f'[data-ws-field="{handle["key"]}"]')
                if ftype in ("select-one", "select"):
                    await _select_with_fallback(el, value)
                elif ftype == "select-multiple" or (isinstance(value, list) and ftype.startswith("select")):
                    await el.select_option(label=[str(v) for v in value])
                elif ftype == "checkbox":
                    name = (spec or {}).get("name")
                    if isinstance(value, list) and name:
                        for v in value:  # e.g. topping: ["cheese","bacon"]
                            await page.check(f'[name="{name}"][value="{v}"]')
                    else:
                        if value:
                            await el.check()
                        else:
                            await el.uncheck()
                elif ftype == "radio":
                    await _check_radio(page, el, spec, value)
                elif ftype in ("email", "tel", "url", "number", "date", "time", "text", "search"):
                    await el.fill(str(value))
                else:  # textarea, contenteditable, unknown
                    await el.fill(str(value))
                result.filled.append(f"{key}={value}")
            except Exception as exc:
                result.failed.append(f"{key}: {exc}")

        # report missing required (from discovered form data)
        if fdata:
            given = {str(k).lower() for k in values}
            for x in fdata["fields"]:
                if x.get("required"):
                    ident = (x.get("name") or x.get("id") or x.get("label") or "").lower()
                    if ident and ident not in given:
                        result.missing_required.append(ident)

        if submit and not result.missing_required:
            submitted = False
            # 1) click a real submit button in the target form
            btn = page.locator(
                f'form:nth-of-type({form_index + 1}) [type=submit], '
                f'form:nth-of-type({form_index + 1}) button[type=submit]'
            )
            try:
                if await btn.count() == 0:
                    btn = page.locator('form [type=submit]').first
                async with page.expect_navigation(wait_until="load", timeout=15000):
                    await btn.first.click(timeout=4000)
                submitted = True
            except Exception:
                pass
            # 2) programmatic submit (works when there's no clickable button)
            if not submitted:
                try:
                    async with page.expect_navigation(wait_until="load", timeout=15000):
                        await page.evaluate("""
                            (fi) => {
                              const forms = document.querySelectorAll('form');
                              const f = forms[fi] || forms[0];
                              if (f) { f.requestSubmit ? f.requestSubmit() : f.submit(); }
                            }
                        """, form_index)
                    submitted = True
                except Exception:
                    pass
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await page.wait_for_timeout(int(after_wait * 1000))

        result.ok = bool(result.filled) and not result.error
        result.final_url = page.url
        try:
            body = await page.evaluate("document.body.innerText")
            result.response_text = re.sub(r"\n{3,}", "\n\n", body).strip()[:4000]
        except Exception:
            pass
        if screenshot:
            Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=screenshot, full_page=True)
            result.screenshot_path = screenshot
    except Exception as exc:
        result.error = str(exc)
    finally:
        await cleanup()
    return result


async def _select_with_fallback(el, value):
    """Try value= first, then label=, then visible text."""
    for kwargs in ({"value": str(value)}, {"label": str(value)}):
        try:
            await el.select_option(**kwargs)
            return
        except Exception:
            continue
    # last resort: case-insensitive option text
    opts = await el.locator("option").all_inner_texts()
    target = str(value).strip().casefold()
    for text in opts:
        if text.strip().casefold() == target:
            await el.select_option(label=text.strip())
            return
    raise ValueError(f"no option matching {value!r} in {opts[:10]}")


async def _check_radio(page, el, spec, value):
    """Tick the radio whose value OR visible label matches (case-insensitive)."""
    name = (spec or {}).get("name")
    if not name:
        await el.check()
        return
    target = str(value).strip().casefold()
    try:
        await page.check(f'[name="{name}"][value="{value}"]', timeout=1500)
        return
    except Exception:
        pass
    try:
        await page.check(f'[name="{name}"][value="{target}"]', timeout=1500)
        return
    except Exception:
        pass
    # match by label text: click the <label> wrapping that radio
    clicked = await page.evaluate("""
        ([name, target]) => {
          const inputs = Array.from(document.querySelectorAll(`input[name="${name}"][type=radio]`));
          for (const inp of inputs) {
            const lbl = inp.closest('label');
            const text = (lbl ? lbl.textContent : '').trim().replace(/\\s+/g,' ').casefold();
            const own = (inp.getAttribute('value') || '').casefold();
            if (text === target || own === target || text.includes(target)) {
              inp.click(); return true;
            }
          }
          return false;
        }
    """, [name, target])
    if not clicked:
        raise ValueError(f"no radio option matching {value!r} for name={name}")


def fill_form(url: str, values: Dict[str, Any], submit: bool = True,
              form_index: int = 0, headless: bool = True,
              screenshot: Optional[str] = None, after_wait: float = 2.5) -> FillResult:
    """Detect, fill (and optionally submit) the form at url with values."""
    return _run(_fill_async(url, values, submit, form_index, headless,
                            screenshot, after_wait))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    json_mode = False
    ap = argparse.ArgumentParser(description="Form extractor + filler (stealth browser)")
    ap.add_argument("url")
    sub = ap.add_subparsers(dest="cmd")
    e = sub.add_parser("extract", help="list forms as JSON schema (for agents)")
    f = sub.add_parser("fill", help="fill fields from --values JSON")
    f.add_argument("--values", required=True, help='JSON dict: {"fieldname": "value", ...}')
    f.add_argument("--no-submit", action="store_true")
    f.add_argument("--form", type=int, default=0)
    f.add_argument("-s", "--screenshot", default="output/form_filled.png")
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.cmd == "extract" or args.cmd is None:
        import json
        forms = extract_forms(args.url, headless=not args.no_headless)
        print(json.dumps([f.to_dict() for f in forms], indent=2, ensure_ascii=False))
    elif args.cmd == "fill":
        import json
        r = fill_form(args.url, json.loads(args.values), submit=not args.no_submit,
                      form_index=args.form, headless=not args.no_headless,
                      screenshot=args.screenshot)
        print(json.dumps({"ok": r.ok, "filled": r.filled, "failed": r.failed,
                          "missing_required": r.missing_required,
                          "final_url": r.final_url, "screenshot": r.screenshot_path,
                          "response_head": r.response_text[:300]}, indent=2, ensure_ascii=False))
