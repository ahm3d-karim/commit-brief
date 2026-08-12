"""Smoke-test the commit-brief MCP server over stdio.

Usage: uv run --extra mcp python scripts/test_mcp_client.py
"""

from __future__ import annotations

import asyncio

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

PROJECT = r"C:/Users/Ahmad Karim/Documents/fde/commit-brief"
LRS = r"C:/Users/Ahmad Karim/Documents/LRS/LRS_PORTAL_EC/lrs-platform"


def _text(result) -> str:
    first = result.content[0]
    if hasattr(first, "text"):
        return first.text
    if isinstance(first, dict):
        return first.get("text", str(first))
    return str(first)


async def main() -> None:
    params = StdioServerParameters(
        command=r"C:/Users/Ahmad Karim/Documents/fde/commit-brief/.venv/Scripts/python.exe",
        args=["-u", "-m", "commit_brief.mcp_server"],
        cwd=PROJECT,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            res = await session.call_tool(
                "summarize_standup", {"repo": LRS, "since": "3 days ago", "dry_run": True}
            )
            text = _text(res)
            print("summarize_standup(dry_run) -> starts with:", repr(text[:300]))

            res = await session.call_tool(
                "list_commits", {"repo": LRS, "since": "2 days ago"}
            )
            text = _text(res)
            print("list_commits -> bytes:", len(text), "| first 80:", repr(text[:80]))

            res = await session.call_tool(
                "summarize_standup",
                {"repo": LRS, "since": "7 days ago", "author": "Muhammad Ammar Faisal", "dry_run": True},
            )
            text = _text(res)
            print("author-filtered dry_run -> contains only Ammar commits:",
                  "Muhammad Ammar Faisal" in text and "ahm3dkarim" not in text)

    # Windows: the stdio client hangs in cleanup waiting for the server process
    # to exit. This is a smoke test — bypass anyio teardown once checks pass.
    import os

    os._exit(0)


asyncio.run(main())
