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


class MissingKeyError(McpError):
    pass


class KeyRejectedError(McpError):
    pass


def server_key() -> str | None:
    """A key configured on the server: CONNECTIONSHUB_API_KEY, else the Claude Code connectionshub MCP config."""
    return os.environ.get("CONNECTIONSHUB_API_KEY") or _claude_config_key()


def _auth_header(api_key: str | None) -> dict[str, str]:
    # connectionshub is scoped to the connection calculators, so it needs its own key,
    # not a general Calcs.com one. A key the user entered in the UI wins over the server's.
    key = (api_key or "").strip() or server_key()
    if not key:
        raise MissingKeyError("No connectionshub API key: enter yours in the app, or set CONNECTIONSHUB_API_KEY.")
    if not key.lower().startswith("bearer "):
        key = f"Bearer {key}"
    return {"Authorization": key}


@asynccontextmanager
async def session(api_key: str | None = None):
    async with create_mcp_http_client(headers=_auth_header(api_key)) as http:
        async with streamable_http_client(MCP_URL, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as s:
                try:
                    await s.initialize()
                except Exception as e:
                    if await _key_rejected(http):
                        raise KeyRejectedError("connectionshub rejected the API key.") from e
                    raise
                yield s


async def _key_rejected(http) -> bool:
    """The MCP SDK hides HTTP status codes, so ask the endpoint directly whether it refused the key."""
    probe = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "detail-read", "version": "0"}}}
    try:
        r = await http.post(MCP_URL, json=probe, headers={"Accept": "application/json, text/event-stream"})
    except Exception:
        return False
    return r.status_code in (401, 403)


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
