#!/usr/bin/env python3
"""
mcp_server.py
============
Model Context Protocol server — exposes the whole web-scraper toolkit to any
MCP-capable AI agent (Claude Desktop, Claude Code, etc.) as native tools.

Run (stdio transport):
    python mcp_server.py

Register in Claude Desktop / Code config, e.g.:
    {
      "mcpServers": {
        "web-scraper": {
          "command": "/abs/path/web-scraper/venv/bin/python",
          "args": ["/abs/path/web-scraper/mcp_server.py"]
        }
      }
    }

Each tool maps 1:1 to agent_tools.TOOL_REGISTRY, so behaviour is identical to
calling the façade from Python.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from agent_tools import TOOL_REGISTRY, call_tool

# JSON-schema inputSpec per tool. Kept explicit (not auto-derived) so the
# agent sees required fields and enums — better tool-use accuracy.
SCHEMAS = {
    "scrape": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "profile": {"type": "string", "description": "auto | cloudflare | stealth_max | bot_detected | crawler"},
            "screenshot": {"type": "boolean"},
            "scrapling": {"type": "boolean", "description": "use Scrapling fetchers"},
            "solve_captcha": {"type": "boolean"},
            "export": {"type": "boolean"},
        },
        "required": ["url"],
    },
    "fetch": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "mode": {"type": "string", "enum": ["http", "stealth", "dynamic"]},
            "selectors": {"type": "object", "description": "name → CSS selector"},
            "impersonate": {"type": "string", "description": "chrome | firefox | safari | edge"},
            "max_chars": {"type": "integer"},
        },
        "required": ["url"],
    },
    "grab_images": {
        "type": "object",
        "properties": {
            "urls": {"type": "array", "items": {"type": "string"}},
            "url": {"type": "string"},
            "out_dir": {"type": "string"},
        },
    },
    "extract_forms": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    "fill_form": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "values": {"type": "object", "description": "field name/id/label → value"},
            "submit": {"type": "boolean"},
            "form_index": {"type": "integer"},
            "screenshot": {"type": "string"},
        },
        "required": ["url", "values"],
    },
    "solve_captcha": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "type": {"type": "string", "enum": ["auto", "cloudflare", "recaptcha", "recaptcha-v3", "hcaptcha", "turnstile"]},
            "site_key": {"type": "string"},
            "pre_fill": {"type": "object"},
            "submit_selector": {"type": "string"},
            "free_first": {"type": "boolean", "description": "default true — try Scrapling's keyless CF/Turnstile solver before the paid escalation"},
            "screenshot": {"type": "string"},
        },
        "required": ["url"],
    },
    "maps_leads": {
        "type": "object",
        "properties": {
            "keywords": {"type": "array", "items": {"type": "string"}},
            "city": {"type": "string"},
            "lat": {"type": "string"},
            "lon": {"type": "string"},
            "depth": {"type": "integer"},
            "email": {"type": "boolean"},
            "socials": {"type": "boolean"},
            "out": {"type": "string"},
        },
        "required": ["keywords"],
    },
    "file_to_markdown": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "file path or URL"},
            "output": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        "required": ["source"],
    },
    "saas_extract": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "subpages": {"type": "integer"},
        },
        "required": ["url"],
    },
    "rss_read": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["url"],
    },
    "auth_scrape": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "browser": {"type": "string", "enum": ["chrome", "firefox", "edge", "brave", "opera"]},
            "screenshot": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        "required": ["url"],
    },
    "convert_html": {
        "type": "object",
        "properties": {
            "html": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        "required": ["html"],
    },
    "sitemap_crawl": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["url"],
    },
}

async def _list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(name=name, title=name, description=meta["desc"],
                       inputSchema=SCHEMAS.get(name, {"type": "object", "properties": {}}))
            for name, meta in TOOL_REGISTRY.items()
        ]
    )


async def _call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    name = params.name
    arguments = params.arguments or {}
    if name not in TOOL_REGISTRY:
        return types.CallToolResult(
            content=[types.TextContent(type="text",
                       text=f"unknown tool {name!r}; available: {', '.join(TOOL_REGISTRY)}")],
            is_error=True)
    # Heavy tools do blocking browser/IO work — run off the event loop.
    result = await asyncio.to_thread(call_tool, name, arguments)
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structured_content=result,
        is_error=not result.get("ok", False))


def build_server() -> Server:
    return Server(
        "web-scraper",
        title="Web Scraper Toolkit",
        description="Stealth scraping, forms, captchas, maps, file→markdown — for AI agents",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


async def main():
    app = build_server()
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
