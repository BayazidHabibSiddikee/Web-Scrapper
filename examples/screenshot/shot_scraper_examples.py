"""
shot_scraper_examples.py
=======================
shot-scraper is a tiny CLI tool for screenshots from Playwright/Chromium.
Great for quick one-liners or batch screenshots without writing code.

Install:
    pip install shot-scraper

Usage:
    shot-scraper https://example.com -o shot.png
    shot-scraper https://example.com -o shot.png --full-page
    shot-scraper https://example.com -o shot.png --width 1440 --height 900
    shot-scraper https://example.com -o shot.png --hover "a.menu"
    shot-scraper https://example.com -o shot.png --click "#login-btn"
    shot-scraper https://example.com -o shot.png --wait 5000
    shot-scraper https://example.com -o shot.png --wait-until networkidle

Batch from a URL list:
    shot-scraper urls.txt -o shots/{0}.png

With JavaScript execution:
    shot-scraper https://example.com -o shot.png \\
        --javascript "document.body.style.background='red'"
"""

import subprocess
from pathlib import Path
from typing import List, Optional


def screenshot(url: str, output: str, full_page: bool = True,
               width: int = 1920, height: int = 1080,
               wait: int = 3000, wait_until: str = "networkidle",
               hover: Optional[str] = None, click: Optional[str] = None,
               js: Optional[str] = None) -> bool:
    """Take a screenshot using shot-scraper CLI."""
    cmd = ["shot-scraper", url, "-o", output,
           "--width", str(width), "--height", str(height),
           "--wait", str(wait), "--wait-until", wait_until]
    if full_page:
        cmd.append("--full-page")
    if hover:
        cmd.extend(["--hover", hover])
    if click:
        cmd.extend(["--click", click])
    if js:
        cmd.extend(["--javascript", js])

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[+] {output}")
        return True
    print(f"[!] shot-scraper error: {result.stderr[:300]}")
    return False


def batch_screenshots(urls: List[str], out_dir: str = "shots",
                      full_page: bool = True) -> List[str]:
    """Batch screenshot via shot-scraper URL file."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    url_file = Path("_tmp_urls.txt")
    url_file.write_text("\n".join(urls))

    saved = []
    for idx, url in enumerate(urls, 1):
        slug = url.replace("https://", "").replace("http://", "")
        slug = "".join(c if c.isalnum() or c in "-_." else "_" for c in slug)[:50]
        output = str(Path(out_dir) / f"{idx:03d}_{slug}.png")
        if screenshot(url, output, full_page=full_page):
            saved.append(output)

    url_file.unlink(missing_ok=True)
    return saved


def screenshot_with_auth(url: str, output: str, auth: str,
                         full_page: bool = True) -> bool:
    """Screenshot a basic-auth protected page. auth = 'user:pass'"""
    cmd = ["shot-scraper", url, "-o", output, "--auth", auth]
    if full_page:
        cmd.append("--full-page")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[+] {output}")
        return True
    print(f"[!] Error: {result.stderr[:300]}")
    return False


def multi_viewport_screenshots(url: str, base_name: str,
                               viewports: List[tuple]) -> List[str]:
    """Same URL, multiple viewport sizes (mobile/tablet/desktop)."""
    saved = []
    for vp in viewports:
        w, h, label = vp
        output = f"{base_name}_{label}.png"
        screenshot(url, output, full_page=False, width=w, height=h)
        saved.append(output)
    return saved


if __name__ == "__main__":
    # Example: common viewports
    viewports = [
        (375, 812, "iphone"),
        (768, 1024, "ipad"),
        (1920, 1080, "desktop"),
    ]
    multi_viewport_screenshots(
        "https://example.com",
        "examples/screenshot/example_viewport",
        viewports,
    )
