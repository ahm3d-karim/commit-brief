"""commit-brief CLI — standup digest, MCP server, and self-test in one command."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .bootstrap import ensure_api_key
from .core import DEFAULT_MODEL, collect_commits, find_repo
from .llm import PROVIDERS, choose_provider, summarize as llm_summarize

# --------------------------------------------------------------------------
# terminal styling — zero-dep ANSI; inert when piped or NO_COLOR
# (pattern copied from the agentize CLI)
# --------------------------------------------------------------------------


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _enable_windows_vt() -> None:
    """Windows: enable ANSI processing on the console — the colorama.init()
    equivalent, zero deps. `os.system('')` is the classic toggle; the ctypes
    branch (kernel32 console mode) covers consoles the toggle misses, e.g.
    raw \x1b leakage on Python 3.11 cmd.exe."""
    if os.name != "nt":
        return
    try:
        os.system("")
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        for stream, std in ((sys.stdout, -11), (sys.stderr, -12)):
            handle = k32.GetStdHandle(std)
            mode = wintypes.DWORD()
            if handle not in (0, None) and handle != wintypes.HANDLE(-1).value \
                    and k32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                k32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass  # piped/redirected streams: color_enabled() already falls back


_enable_windows_vt()


def _s(code: str, s: str) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if color_enabled() else s


def ok(s: str) -> str:
    return _s("32", "✓ " + s)


def warn(s: str) -> str:
    return _s("33", "⚠ " + s)


def err(s: str) -> str:
    return _s("31", "✗ " + s)


def green(s: str) -> str:
    return _s("32", s)


def cyan(s: str) -> str:
    return _s("36", s)


def dim(s: str) -> str:
    return _s("2", s)


def bold(s: str) -> str:
    return _s("1", s)


# --------------------------------------------------------------------------
# repo tree walk — downward, depth <= 3 (unlike core.find_repo, which
# walks UP from cwd like git itself)
# --------------------------------------------------------------------------

PRUNE_DIRS = {
    ".git", "node_modules", ".next", ".nuxt", "dist", "build", "out",
    ".venv", "venv", "env", "__pycache__", ".cache", ".turbo", ".parcel-cache",
    "target", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".eggs", ".idea", ".vscode", ".yarn", ".pnpm-store", ".serverless",
    ".vercel", ".expo", ".terraform", ".gitlab", "site-packages", ".docusaurus",
    ".storybook-static", "cdk.out", "AppData", ".hermes", ".local",
}


def _same_path(a: Path, b: Path) -> bool:
    if os.name == "nt":
        return str(a).lower() == str(b).lower()
    return a == b


def find_repos_in_tree(base: Path) -> list[Path]:
    """Git repos under `base`, depth <= 3. cwd is always first (and listed
    once even when cwd itself is a repo — no duplicates)."""
    base = base.resolve()
    found: list[Path] = [base]
    for dirpath, dirnames, _ in os.walk(base):
        depth = dirpath[len(str(base)):].count(os.sep)
        # .git must be detected BEFORE pruning (it's in PRUNE_DIRS);
        # never descend into .git internals.
        if ".git" in dirnames:
            p = Path(dirpath).resolve()
            if not _same_path(p, base) and not any(_same_path(p, f) for f in found):
                found.append(p)
            dirnames.remove(".git")
        if depth >= 3:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
    found.sort(key=lambda p: (0 if _same_path(p, base) else 1, str(p).lower()))
    return found


def pick_local_repo(base: Path | None = None) -> Path:
    """Numbered pick of git repos in the tree (depth <= 3). Enter = current."""
    base = (base or Path.cwd()).resolve()
    found = find_repos_in_tree(base)
    if len(found) == 1:
        print(dim("  no other git repos in this tree — using current folder"))
        return base
    print()
    for i, p in enumerate(found, 1):
        label = " (current)" if _same_path(p, base) else ""
        shown = p.name if _same_path(p, base) else p.relative_to(base).as_posix()
        print(f"  {green(str(i) + '.')}  {shown}{dim(label)}")
    for _ in range(3):
        try:
            ans = input("\n  Select repo [Enter = current]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return base
        if not ans:
            return base
        try:
            i = int(ans)
            if 1 <= i <= len(found):
                return found[i - 1]
        except ValueError:
            pass
        print("  ?")
    return base


def ask_history_defaults() -> tuple[str, list[str] | None]:
    """Interactive: commit window (default yesterday) and authors (default all)."""
    try:
        since = input("  Commits since? [yesterday] ").strip() or "yesterday"
        raw = input("  Authors? [all, comma-separated] ").strip()
    except (EOFError, KeyboardInterrupt):
        return "yesterday", None
    authors = [a.strip() for a in raw.split(",") if a.strip()] or None
    return since, authors


def _menu_local_flow() -> int:
    """Local path: pick a repo from this tree, set window/authors, digest."""
    repo = pick_local_repo()
    if not (repo / ".git").exists():
        parent = find_repo(repo)
        if parent is not None:
            print(dim(f"  current folder is inside {parent} — using that repo"))
            repo = parent
    print(dim(f"  Selected: {repo}"))
    since, authors = ask_history_defaults()

    t0 = time.monotonic()
    try:
        commits = collect_commits(str(repo), since, None, authors)
    except RuntimeError as e:
        print(f"commit-brief: {e}", file=sys.stderr)
        return 2

    if not commits:
        print(f"No commits since '{since}' in {repo}.")
        return 0

    provider = choose_provider()
    if provider == "none":
        print(warn("  skipped — no provider picked"))
        return 0
    print(dim(f"  summarizing {len(commits)} commits with {provider}…"), file=sys.stderr)
    api_key = ensure_api_key(provider, interactive=True)
    try:
        out = llm_summarize(commits, provider=provider, api_key=api_key,
                            model=os.environ.get("COMMIT_BRIEF_MODEL"))
    except RuntimeError as e:
        print(f"commit-brief: {e}", file=sys.stderr)
        return 2

    print()
    print(out)
    print()
    print(ok(f"Done — {time.monotonic() - t0:.1f}s"))
    return 0


def _menu_github_flow() -> int:
    """GitHub path: sign in if needed, pick repos from the account, digest."""
    from .github import github_mode

    since, authors = ask_history_defaults()
    provider = choose_provider()
    if provider == "none":
        print(warn("  skipped — no provider picked"))
        return 0
    api_key = ensure_api_key(provider, interactive=True)
    return github_mode(since, authors, api_key,
                       model=os.environ.get("COMMIT_BRIEF_MODEL"),
                       provider=provider)


def interactive_menu() -> int:
    """Bare `commit-brief`: pick local or GitHub, then run the pipeline."""
    # first-run setup: tool checks + consent installs (runs once, then no-op)
    from .bootstrap import bootstrap

    bootstrap(interactive=True)
    print()
    print(bold(cyan("  ⚡ commit-brief — standup digest from git history")))
    print(dim("  ───────────────────────────────────────────────"))
    while True:
        print()
        print("  1.  Local — this folder (or pick from this tree)")
        print("  2.  GitHub — sign in, pick repos from your account")
        print("  q.  Quit")
        try:
            choice = input("\n  Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  bye")
            return 0
        if choice == "1":
            return _menu_local_flow()
        if choice == "2":
            return _menu_github_flow()
        if choice in ("q", "quit", "exit"):
            print("  bye")
            return 0
        print("  ?")


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
  commit-brief mcp-test .           self-test the MCP server against a repo

interactive:
  commit-brief (no arguments, tty)  guided menu: repo -> since -> authors -> digest""",
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
        default=None,
        help="Model ID (default: per-provider registry model; env: COMMIT_BRIEF_MODEL)",
    )
    p.add_argument(
        "--api-key", default=None, help="LLM API key for the selected provider"
    )
    p.add_argument(
        "--provider",
        metavar="NAME",
        default=None,
        help="LLM provider: anthropic, openai, openrouter, gemini, xai, "
        "deepseek, groq, mistral, ollama, custom (default: anthropic)",
    )
    p.add_argument(
        "--base-url", metavar="URL",
        help="OpenAI-compatible base URL (with --provider custom)",
    )
    p.add_argument(
        "--github",
        action="store_true",
        help="GitHub mode: sign in, pick repos from your account, digest each",
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
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        # bare invocation → interactive menu (or help when stdin is piped)
        if sys.stdin.isatty():
            return interactive_menu()
        build_parser().print_help()
        return 0
    args = build_parser().parse_args(argv)
    if args.command:
        return args.func(args)

    if args.github:
        from .github import github_mode

        provider = args.provider or "anthropic"
        if provider not in PROVIDERS:
            print(f"commit-brief: unknown provider '{provider}'", file=sys.stderr)
            return 2
        api_key = args.api_key or ensure_api_key(provider, sys.stdin.isatty())
        return github_mode(args.since, args.author, api_key, args.model,
                           provider=provider, base_url=args.base_url)

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

    provider = args.provider or "anthropic"
    if provider not in PROVIDERS:
        print(f"commit-brief: unknown provider '{provider}'", file=sys.stderr)
        return 2
    api_key = args.api_key if args.dry_run else (
        args.api_key or ensure_api_key(provider, sys.stdin.isatty())
    )
    try:
        out = llm_summarize(
            commits,
            provider=provider,
            model=args.model,
            api_key=api_key,
            base_url=args.base_url,
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
