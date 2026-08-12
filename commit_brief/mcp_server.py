"""commit-brief as an MCP server.

Exposes the same core as two tools for MCP clients (Claude Code, Codex, ...).
Run it:

    uv run --extra mcp python -m commit_brief.mcp_server

Register with Claude Code:

    claude mcp add commit-brief -- uv run --project <path-to-project> --extra mcp python -m commit_brief.mcp_server
"""

from __future__ import annotations

import json
import os

from mcp.server import FastMCP

from .core import DEFAULT_MODEL, collect_commits, summarize

mcp = FastMCP("commit-brief")


def _split_authors(author: str | None) -> list[str] | None:
    if not author:
        return None
    return [a.strip() for a in author.split(",") if a.strip()]


@mcp.tool()
def summarize_standup(
    repo: str = ".",
    since: str = "yesterday",
    until: str | None = None,
    author: str | None = None,
    bullets: bool = False,
    dry_run: bool = False,
) -> str:
    """Summarize git commits since a date into a standup digest.

    Args:
        repo: Path to the git repository (default: current working directory).
        since: Git date spec, e.g. 'yesterday', '3 days ago', '2026-08-01'.
        until: Optional upper date bound (same format as since).
        author: Comma-separated author name(s) to include, e.g. 'Ahmad Karim, ammmar04'.
        bullets: Return a bullet list grouped by author instead of one paragraph.
        dry_run: Return the exact prompt instead of calling the LLM (no API cost).
    """
    commits = collect_commits(
        repo=repo, since=since, until=until, authors=_split_authors(author)
    )
    if not commits:
        return f"No commits since '{since}' in {repo}."
    return summarize(
        commits,
        model=os.environ.get("COMMIT_BRIEF_MODEL", DEFAULT_MODEL),
        bullets=bullets,
        dry_run=dry_run,
    )


@mcp.tool()
def list_commits(
    repo: str = ".",
    since: str = "yesterday",
    until: str | None = None,
    author: str | None = None,
) -> str:
    """List commits in the window as JSON (hash, author, subject, body, refs, per-file stats).

    Args:
        repo: Path to the git repository (default: current working directory).
        since: Git date spec, e.g. 'yesterday', '3 days ago', '2026-08-01'.
        until: Optional upper date bound (same format as since).
        author: Comma-separated author name(s) to include.
    """
    commits = collect_commits(
        repo=repo, since=since, until=until, authors=_split_authors(author)
    )
    return json.dumps([c.__dict__ for c in commits], indent=2)


if __name__ == "__main__":
    mcp.run()
