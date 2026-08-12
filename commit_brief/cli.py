"""commit-brief CLI — standup digest, MCP server, and self-test in one command."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .core import DEFAULT_MODEL, collect_commits, summarize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="commit-brief",
        description="Turn yesterday's git log into a one-paragraph standup summary.",
        epilog="""subcommands:
  mcp         run the MCP server (stdio) so agents can call the tools
  mcp-test    end-to-end smoke test of the MCP server

examples:
  commit-brief                      standup digest for yesterday (current repo)
  commit-brief --since '3 days ago' --repo ../other-repo
  commit-brief --author 'Alice' --bullets
  commit-brief --dry-run            see exactly what the LLM would receive
  commit-brief --json               raw commits, no API call
  commit-brief mcp                  start the MCP server
  commit-brief mcp-test .           self-test the MCP server against a repo""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo", default=".", help="path to git repo (default: current dir)")
    p.add_argument(
        "--since",
        default="yesterday",
        help="git date spec: 'yesterday', '3 days ago', '2026-08-01'",
    )
    p.add_argument("--until", default=None, help="git date spec, upper bound")
    p.add_argument(
        "--author", action="append", default=None, help="filter by author (repeatable)"
    )
    p.add_argument(
        "--bullets", action="store_true", help="bullets grouped by author, not one paragraph"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact prompt sent to the LLM; no API call",
    )
    p.add_argument("--json", action="store_true", help="print raw commits as JSON; no API call")
    p.add_argument(
        "--model",
        default=os.environ.get("COMMIT_BRIEF_MODEL", DEFAULT_MODEL),
        help="Anthropic model (env: COMMIT_BRIEF_MODEL)",
    )
    p.add_argument(
        "--api-key", default=None, help="Anthropic API key (default: ANTHROPIC_API_KEY env)"
    )

    sub = p.add_subparsers(dest="command", metavar="")
    mcp_p = sub.add_parser("mcp", help="run the MCP server (stdio)")
    mcp_p.set_defaults(func=cmd_mcp)
    test_p = sub.add_parser("mcp-test", help="end-to-end smoke test of the MCP server")
    test_p.add_argument(
        "repo",
        nargs="?",
        default=None,
        help="git repo to test against (default: $CBR_TEST_REPO or current dir)",
    )
    test_p.set_defaults(func=cmd_mcp_test)
    return p


def cmd_mcp(_args) -> int:
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        print(
            "commit-brief: MCP support not installed.\n"
            "Install it with: uv tool install 'commit-brief[mcp]'",
            file=sys.stderr,
        )
        return 2
    mcp_main()
    return 0


def cmd_mcp_test(args) -> int:
    try:
        from .mcp_test import run_smoke_test
    except ImportError:
        print(
            "commit-brief: MCP support not installed.\n"
            "Install it with: uv tool install 'commit-brief[mcp]'",
            file=sys.stderr,
        )
        return 2
    repo = args.repo or os.environ.get("CBR_TEST_REPO") or "."
    return run_smoke_test(repo)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command:
        return args.func(args)

    try:
        commits = collect_commits(args.repo, args.since, args.until, args.author)
    except RuntimeError as e:
        print(f"commit-brief: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([c.__dict__ for c in commits], indent=2))
        return 0

    if not commits:
        print(f"No commits since '{args.since}' in {args.repo}.")
        return 0

    try:
        out = summarize(
            commits,
            model=args.model,
            api_key=args.api_key,
            bullets=args.bullets,
            dry_run=args.dry_run,
        )
    except RuntimeError as e:
        print(f"commit-brief: {e}", file=sys.stderr)
        return 2

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
