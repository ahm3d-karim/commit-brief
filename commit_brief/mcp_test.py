"""End-to-end smoke test for the MCP server over stdio.

Runs as `commit-brief mcp-test [repo]` or via scripts/test_mcp_client.py.
Spawns the real server (`python -m commit_brief.mcp_server`), lists tools,
and exercises both tools against a git repo.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


def _text(result) -> str:
    first = result.content[0]
    if hasattr(first, "text"):
        return first.text
    if isinstance(first, dict):
        return first.get("text", str(first))
    return str(first)


async def _run(repo: str) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "commit_brief.mcp_server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            res = await session.call_tool(
                "summarize_standup", {"repo": repo, "since": "3 days ago", "dry_run": True}
            )
            print("summarize_standup(dry_run) -> starts with:", repr(_text(res)[:60]))

            res = await session.call_tool(
                "list_commits", {"repo": repo, "since": "2 days ago"}
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
                    {"repo": repo, "since": "7 days ago", "author": name, "dry_run": True},
                )
                text = _text(res)
                print(f"author-filtered dry_run -> top author '{name}' appears:", name in text)


def run_smoke_test(repo: str) -> int:
    """Run the smoke test. Exits the process — Windows stdio teardown hangs
    waiting for the server process, so bypass anyio cleanup on both paths.
    Flush first: os._exit skips Python's buffered stdout."""
    try:
        asyncio.run(_run(repo))
    except Exception as e:  # noqa: BLE001 - report and exit; it's a smoke test
        print(f"smoke test FAILED: {e}", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    print("smoke test: OK")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
