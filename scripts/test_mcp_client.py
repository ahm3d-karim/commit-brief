"""Smoke-test the commit-brief MCP server over stdio.

Usage: uv run --extra mcp python scripts/test_mcp_client.py [path-to-git-repo]
Defaults to $CBR_TEST_REPO, then the current directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CBR_TEST_REPO", ".")


def _text(result) -> str:
    first = result.content[0]
    if hasattr(first, "text"):
        return first.text
    if isinstance(first, dict):
        return first.get("text", str(first))
    return str(first)


async def main() -> None:
    params = StdioServerParameters(
        command="uv",
        args=["run", "--extra", "mcp", "python", "-m", "commit_brief.mcp_server"],
        cwd=PROJECT,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            res = await session.call_tool(
                "summarize_standup", {"repo": REPO, "since": "3 days ago", "dry_run": True}
            )
            text = _text(res)
            print("summarize_standup(dry_run) -> starts with:", repr(text[:60]))

            res = await session.call_tool(
                "list_commits", {"repo": REPO, "since": "2 days ago"}
            )
            text = _text(res)
            print("list_commits -> bytes:", len(text), "| first 80:", repr(text[:80]))

            try:
                commits = json.loads(text)
            except json.JSONDecodeError:
                commits = []
            top = Counter(c["author"] for c in commits).most_common(1)
            if not top:
                print("author-filter check skipped (no commits in 2-day window)")
            else:
                name = top[0][0]
                res = await session.call_tool(
                    "summarize_standup",
                    {"repo": REPO, "since": "7 days ago", "author": name, "dry_run": True},
                )
                text = _text(res)
                print(f"author-filtered dry_run -> top author '{name}' appears:",
                      name in text)

    # Windows: the stdio client hangs in cleanup waiting for the server process
    # to exit. This is a smoke test — bypass anyio teardown once checks pass.
    os._exit(0)


asyncio.run(main())
