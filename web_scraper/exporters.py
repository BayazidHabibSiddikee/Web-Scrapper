"""Safe local export formats for scrape results."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

FORMATS = {"json", "md", "csv", "sqlite"}


def _csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def export_result(result: Any, output: str, format_name: str) -> str:
    fmt = format_name.lower()
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported export format: {format_name}")
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = result.__dict__ if hasattr(result, "__dict__") else dict(result)
    if fmt == "json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    elif fmt == "md":
        lines = [f"# {data.get('title') or data.get('url', 'Scraped page')}", "", f"URL: {data.get('url', '')}", "", data.get("text", "")]
        path.write_text("\n".join(lines), encoding="utf-8")
    elif fmt == "csv":
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["url", "title", "text", "backend", "error"])
            writer.writerow([_csv_safe(data.get(key)) for key in ("url", "title", "text", "backend", "error")])
    else:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, title TEXT, text TEXT, backend TEXT, error TEXT)")
            connection.execute("INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?)", (data.get("url"), data.get("title"), data.get("text"), data.get("backend"), data.get("error")))
    return str(path)


__all__ = ["FORMATS", "export_result"]
