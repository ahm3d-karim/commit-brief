"""commit-brief CLI."""

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
        epilog="""examples:
  commit-brief
  commit-brief --since '3 days ago' --repo ../lrs-platform
  commit-brief --author ammmar04 --bullets
  commit-brief --dry-run          # see exactly what the LLM would receive
  commit-brief --json             # raw commits, no API call""",
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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
