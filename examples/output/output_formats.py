"""
output_formats.py
===============
Output formatters for scraped data:

1. JSON      — structured dump, metadata + data
2. CSV       — flat table export
3. Markdown  — readable report
4. HTML      — standalone report page
5. SQLite    — store results in a local database
6. PDF       — generate PDF report (requires pdfkit/weasyprint)

Usage:
    from output_formats import save_json, save_csv, save_markdown, save_sqlite
"""

import json
import csv
import sqlite3
import logging
import html as html_mod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ScrapeRecord:
    url: str
    title: str = ""
    status_code: int = 0
    content_type: str = ""
    text: str = ""
    links: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    author: str = ""
    publish_date: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# 1. JSON
# ---------------------------------------------------------------------------

def save_json(records: List[ScrapeRecord], path: str,
              indent: int = 2, include_metadata: bool = True) -> str:
    """Export records to JSON file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = []
    for rec in records:
        d = asdict(rec)
        if not include_metadata:
            d.pop("metadata", None)
        data.append(d)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "count": len(data),
        "records": data,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent, ensure_ascii=False, default=str)

    logger.info("JSON saved: %s (%d records)", path, len(records))
    return path


# ---------------------------------------------------------------------------
# 2. CSV
# ---------------------------------------------------------------------------

def save_csv(records: List[ScrapeRecord], path: str,
             flatten_metadata: bool = True) -> str:
    """Export records to CSV. Metadata fields are flattened as metadata__key."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if not records:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return path

    # Build fieldnames
    base_fields = ["url", "title", "status_code", "content_type",
                   "author", "publish_date", "error", "timestamp"]
    list_fields = ["links", "images"]
    meta_fields = set()
    for rec in records:
        if flatten_metadata and rec.metadata:
            for k in rec.metadata:
                meta_fields.add(f"metadata__{k}")
    fieldnames = base_fields + sorted(meta_fields) + ["text", "links", "images"]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames,
                                quoting=csv.QUOTE_ALL, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row = asdict(rec)
            if flatten_metadata:
                for k, v in row.pop("metadata", {}).items():
                    row[f"metadata__{k}"] = str(v) if not isinstance(v, (list, dict)) else json.dumps(v)
            # Serialize lists
            for lf in list_fields:
                row[lf] = json.dumps(row.get(lf, []), ensure_ascii=False)
            row.setdefault("text", "")
            writer.writerow(row)

    logger.info("CSV saved: %s (%d records)", path, len(records))
    return path


# ---------------------------------------------------------------------------
# 3. Markdown report
# ---------------------------------------------------------------------------

def save_markdown(records: List[ScrapeRecord], path: str,
                  include_links: bool = True, include_text: bool = True) -> str:
    """Export records as a readable Markdown report."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Scrape Report",
        f"",
        f"**Generated:** {datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}  ",
        f"**Records:** {len(records)}",
        f"",
        "---",
        "",
    ]

    for i, rec in enumerate(records, 1):
        status = f" (HTTP {rec.status_code})" if rec.status_code else ""
        error_tag = f" ⚠️ ERROR: {rec.error}" if rec.error else ""
        lines.append(f"## {i}. {rec.title or rec.url}{status}{error_tag}")
        lines.append(f"")
        lines.append(f"**URL:** [{rec.url}]({rec.url})  ")
        if rec.author:
            lines.append(f"**Author:** {rec.author}  ")
        if rec.publish_date:
            lines.append(f"**Date:** {rec.publish_date}  ")
        if rec.timestamp:
            lines.append(f"**Scraped at:** {rec.timestamp}  ")
        lines.append("")

        if rec.content_type:
            lines.append(f"**Content-Type:** `{rec.content_type}`  ")
        if rec.metadata:
            lines.append("**Metadata:**")
            for k, v in rec.metadata.items():
                lines.append(f"- `{k}`: {v}")
            lines.append("")

        if include_links and rec.links:
            lines.append("### Links")
            lines.append("")
            for link in rec.links[:20]:
                lines.append(f"- {link}")
            if len(rec.links) > 20:
                lines.append(f"- ... and {len(rec.links) - 20} more")
            lines.append("")

        if include_text and rec.text:
            preview = rec.text[:1000] + "..." if len(rec.text) > 1000 else rec.text
            lines.append("### Content Preview")
            lines.append("")
            lines.append("```")
            lines.append(preview)
            lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Markdown saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# 4. HTML report
# ---------------------------------------------------------------------------

def save_html_report(records: List[ScrapeRecord], path: str,
                     title: str = "Scrape Report") -> str:
    """Export records as a standalone HTML report."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for rec in records:
        status = f"HTTP {rec.status_code}" if rec.status_code else "N/A"
        error_html = f'<div class="error">⚠️ {html_mod.escape(rec.error)}</div>' if rec.error else ""
        links_html = "".join(
            f'<li><a href="{html_mod.escape(l)}" target="_blank">{html_mod.escape(l)}</a></li>'
            for l in rec.links[:20]
        )
        text_preview = html_mod.escape(rec.text[:600] + "..." if len(rec.text) > 600 else rec.text)
        meta_items = "".join(
            f"<li><strong>{html_mod.escape(str(k))}:</strong> {html_mod.escape(str(v))}</li>"
            for k, v in rec.metadata.items()
        )

        rows.append(f"""
        <div class="record">
            <div class="header">
                <h2><a href="{html_mod.escape(rec.url)}" target="_blank">
                    {html_mod.escape(rec.title or rec.url)}
                </a></h2>
                <span class="status {('error' if rec.error else 'ok')}">{status}</span>
            </div>
            <div class="meta">
                <span>URL: <code>{html_mod.escape(rec.url)}</code></span>
                {f'<span>Author: {html_mod.escape(rec.author)}</span>' if rec.author else ''}
                {f'<span>Date: {html_mod.escape(rec.publish_date)}</span>' if rec.publish_date else ''}
                {f'<span>Type: <code>{html_mod.escape(rec.content_type)}</code></span>' if rec.content_type else ''}
            </div>
            {error_html}
            {f'<ul class="meta-list">{meta_items}</ul>' if meta_items else ''}
            {f'<div class="section"><h3>Links ({len(rec.links)})</h3><ul>{links_html}</ul></div>' if rec.links else ''}
            <div class="section">
                <h3>Content Preview</h3>
                <pre>{text_preview}</pre>
            </div>
        </div>
        """)

    body = "\n".join(rows)
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_mod.escape(title)}</title>
<style>
    body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1100px; margin: 0 auto; padding: 2rem; background: #0d1117; color: #c9d1d9; }}
    h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 0.5rem; }}
    .record {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }}
    .header {{ display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 0.5rem; }}
    h2 {{ margin: 0; font-size: 1.1rem; color: #58a6ff; }}
    a {{ color: #58a6ff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .status {{ padding: 0.25rem 0.6rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
    .status.ok {{ background: #238636; color: #fff; }}
    .status.error {{ background: #da3633; color: #fff; }}
    .meta {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 0.75rem 0; font-size: 0.85rem; color: #8b949e; }}
    .meta code {{ color: #c9d1d9; background: #0d1117; padding: 0.1rem 0.3rem; border-radius: 3px; }}
    .error {{ background: #3d0c0c; border: 1px solid #da3633; color: #f85149; padding: 0.75rem; border-radius: 6px; margin: 0.75rem 0; }}
    .meta-list {{ margin: 0.5rem 0; padding-left: 1.5rem; }}
    .section {{ margin-top: 1rem; }}
    .section h3 {{ font-size: 0.9rem; color: #8b949e; margin-bottom: 0.5rem; }}
    ul {{ margin: 0.25rem 0; padding-left: 1.5rem; }}
    li {{ margin: 0.2rem 0; word-break: break-all; }}
    pre {{ background: #0d1117; border: 1px solid #30363d; padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.8rem; max-height: 300px; overflow-y: auto; }}
</style>
</head>
<body>
<h1>{html_mod.escape(title)}</h1>
<p>Generated: {datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")} | {len(records)} records</p>
{body}
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(full_html)
    logger.info("HTML report saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# 5. SQLite
# ---------------------------------------------------------------------------

def save_sqlite(records: List[ScrapeRecord], path: str = "scrape_results.db") -> str:
    """Save records to a SQLite database."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scrape_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            status_code INTEGER,
            content_type TEXT,
            text TEXT,
            links TEXT,
            images TEXT,
            author TEXT,
            publish_date TEXT,
            metadata TEXT,
            error TEXT,
            timestamp TEXT
        )
    """)
    for rec in records:
        try:
            cur.execute("""
                INSERT OR REPLACE INTO scrape_results
                (url, title, status_code, content_type, text, links, images,
                 author, publish_date, metadata, error, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec.url, rec.title, rec.status_code, rec.content_type,
                rec.text[:50000], json.dumps(rec.links), json.dumps(rec.images),
                rec.author, rec.publish_date, json.dumps(rec.metadata),
                rec.error, rec.timestamp or datetime.now(timezone.utc).isoformat(),
            ))
        except sqlite3.Error as exc:
            logger.warning("SQLite insert failed for %s: %s", rec.url, exc)

    conn.commit()
    conn.close()
    logger.info("SQLite saved: %s (%d records)", path, len(records))
    return path


def query_sqlite(db_path: str, url_pattern: str = "%") -> List[dict]:
    """Query SQLite results."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM scrape_results WHERE url LIKE ?", (url_pattern,))
    return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# 6. Batch output helper
# ---------------------------------------------------------------------------

def export_all(records: List[ScrapeRecord], base_name: str = "scrape") -> Dict[str, str]:
    """Export records to ALL formats at once."""
    base = Path(base_name)
    base.parent.mkdir(parents=True, exist_ok=True)

    outputs = {
        "json": save_json(records, str(base.with_suffix(".json"))),
        "csv": save_csv(records, str(base.with_suffix(".csv"))),
        "markdown": save_markdown(records, str(base.with_suffix(".md"))),
        "html": save_html_report(records, str(base.with_suffix(".html"))),
        "sqlite": save_sqlite(records, str(base.with_suffix(".db"))),
    }
    return outputs


if __name__ == "__main__":
    # Demo with fake records
    demo = [
        ScrapeRecord(
            url="https://example.com",
            title="Example Domain",
            status_code=200,
            content_type="text/html",
            text="Example Domain\n\nThis domain is for use in illustrative examples...",
            links=["https://www.iana.org/domains/example"],
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
        ScrapeRecord(
            url="https://example.org",
            title="Example.org",
            status_code=200,
            text="Another example...",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    ]
    outputs = export_all(demo, "output/demo")
    print("\nExported:")
    for fmt, path in outputs.items():
        print(f"  {fmt:10s}: {path}")
