"""Thin async client for the connectionshub (Calcs.com) MCP server."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

MCP_URL = os.environ.get("CALCS_MCP_URL", "https://api.calcs.com/mcp")


class McpError(RuntimeError):
    pass


def _claude_config_key() -> str | None:
    """The key the connectionshub MCP server is configured with in Claude Code, if any."""
    path = Path.home() / ".claude.json"
    try:
        server = json.loads(path.read_text())["mcpServers"]["connectionshub"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None
    return (server.get("headers") or {}).get("Authorization")


def _auth_header() -> dict[str, str]:
    # connectionshub is scoped to the connection calculators, so it needs its own key,
    # not a general Calcs.com one.
    key = os.environ.get("CONNECTIONSHUB_API_KEY") or _claude_config_key()
    if not key:
        raise McpError(
            "No connectionshub key: set CONNECTIONSHUB_API_KEY or configure the connectionshub MCP server in Claude Code."
        )
    if not key.lower().startswith("bearer "):
        key = f"Bearer {key}"
    return {"Authorization": key}


@asynccontextmanager
async def session():
    async with create_mcp_http_client(headers=_auth_header()) as http:
        async with streamable_http_client(MCP_URL, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s


async def call(s: ClientSession, tool: str, **args: Any) -> Any:
    """Call a tool and return its decoded JSON payload."""
    res = await s.call_tool(tool, args)
    text = "".join(c.text for c in res.content if getattr(c, "type", None) == "text")
    if res.is_error:
        raise McpError(f"{tool} failed: {text[:2000]}")
    if res.structured_content:
        return res.structured_content
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
