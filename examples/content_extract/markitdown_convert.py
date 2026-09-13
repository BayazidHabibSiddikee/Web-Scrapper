#!/usr/bin/env python3
"""
markitdown_convert.py
====================
Universal file → Markdown converter, merged from microsoft/markitdown.

Any scraped/stored artifact — PDF, DOCX, XLSX, PPTX, HTML, images (EXIF+OCR),
audio (transcription), EPUB, ZIP, YouTube URLs — becomes clean Markdown you
can feed an LLM or keep in a knowledge base.

Complements this toolkit: master_pipeline produces HTML/PNG/CSV; this turns
the messy binaries the web still serves (PDFs, slides, spreadsheets) into text.

Usage:
    python markitdown_convert.py report.pdf
    python markitdown_convert.py slides.pptx -o notes.md
    python markitdown_convert.py mixed/ --recursive     # whole folder
    python markitdown_convert.py https://arxiv.org/pdf/1706.03762
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SUPPORTED_HINT = "pdf, docx, xlsx, pptx, html, csv, json, xml, zip, epub, " \
                 "images (EXIF/OCR), audio (transcript), youtube URLs"


def convert(source: str, stream=None) -> str:
    """Convert a file path or URL to Markdown text."""
    from markitdown import MarkItDown
    md = MarkItDown(enable_plugins=False)
    if stream is not None:
        return md.convert_stream(stream, file_path=source).text_content
    return md.convert(source).text_content


def convert_tree(root: Path, out_dir: Path, recursive: bool = True) -> dict:
    """Convert every supported file under root/ into out_dir/<name>.md.
    Returns {source: output_path_or_error}."""
    patterns = ["**/*"] if recursive else ["*"]
    results = {}
    files = [p for pat in patterns for p in root.glob(pat) if p.is_file()]
    for f in files:
        if f.suffix.lower() not in _CONVERTIBLE or out_dir in f.parents:
            continue
        dest = out_dir / (f.stem + ".md")
        try:
            text = convert(str(f))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            results[str(f)] = str(dest)
        except Exception as exc:
            results[str(f)] = f"ERROR: {exc}"
    return results


_CONVERTIBLE = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".html", ".htm", ".csv", ".json", ".xml", ".zip", ".epub",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".webp",
    ".mp3", ".wav", ".m4a", ".flac",
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=f"File → Markdown converter ({SUPPORTED_HINT})")
    ap.add_argument("source", help="file, folder (with -r), or URL")
    ap.add_argument("-o", "--output", help="output .md path (default: stdout)")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="convert a whole folder of files")
    args = ap.parse_args()

    src = Path(args.source)
    if args.recursive and src.is_dir():
        out_dir = Path(args.output) if args.output else src / "markdown"
        results = convert_tree(src, out_dir)
        ok = {k: v for k, v in results.items() if not v.startswith("ERROR")}
        bad = {k: v for k, v in results.items() if v.startswith("ERROR")}
        print(f"converted {len(ok)} files → {out_dir}/")
        for k, v in bad.items():
            print(f"  {k}: {v}", file=sys.stderr)
    else:
        try:
            text = convert(args.source)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"saved → {args.output}")
        else:
            print(text)
