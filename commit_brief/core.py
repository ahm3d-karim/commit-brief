"""commit-brief core: collect git history and turn it into a standup digest.

Design rule: we send commit messages, file paths and change stats to the LLM —
never diffs. Cheaper, and code never leaves the repo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = (
    "You are a terse standup assistant for software teams. You write short, "
    "accurate digests of what developers shipped, from git history alone."
)

_NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")


@dataclass
class Commit:
    hash: str
    author: str
    author_date: str
    subject: str
    body: str
    refs: str
    files: list[str]  # "path (+a -d)" or "path (binary)"
    shortstat: str  # aggregate, e.g. "5 files changed, +120 -34"


def _parse_numstat_line(line: str) -> tuple[str, str | None, str | None] | None:
    m = _NUMSTAT_RE.match(line)
    if not m:
        return None
    added_raw, deleted_raw, path = m.groups()
    added = int(added_raw) if added_raw != "-" else None
    deleted = int(deleted_raw) if deleted_raw != "-" else None
    return path, added, deleted


def run_git(repo: str, args: list[str]) -> str:
    # stdin=DEVNULL: git must never inherit the MCP protocol pipe (or any
    # caller's stdin) — it could block on prompts or, on Windows/MSYS2, die
    # from console-state weirdness when spawned from a console-less server.
    env = {**os.environ, "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("git not found on PATH") from None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "(no stderr)"
        raise RuntimeError(f"git {' '.join(args)} failed (rc={proc.returncode}): {detail}")
    return proc.stdout


def find_repo(start: Path) -> Path | None:
    """Nearest .git above start — git's own walk-up behavior."""
    for p in (start, *start.parents):
        if (p / ".git").exists():
            return p
    return None


def collect_commits(
    repo: str = ".",
    since: str = "yesterday",
    until: str | None = None,
    authors: list[str] | None = None,
) -> list[Commit]:
    """Parse `git log` output into structured commits.

    `since`/`until` accept anything git accepts ('yesterday', '3 days ago',
    '2026-08-01'). `authors` maps to repeated --author flags (OR semantics).

    When the default repo ('.') is not a git repository, walks up from the
    current directory like git itself does; if none is found, raises a
    friendly error instead of git's raw fatal.
    """
    fmt = "%x1e%h%x1f%an%x1f%aI%x1f%s%x1f%b%x1d%D%x1d"
    args = [
        "log",
        f"--pretty=format:{fmt}",
        "--numstat",
        f"--since={since}",
    ]
    if until:
        args.append(f"--until={until}")
    for a in authors or []:
        args.append(f"--author={a}")

    try:
        out = run_git(repo, args)
    except RuntimeError as e:
        if repo in (".", "~") and "not a git repository" in str(e):
            found = find_repo(Path.cwd())
            if found is None:
                raise RuntimeError(
                    "no git repository here or in any parent directory — "
                    "cd into a repo or pass --repo <path>"
                ) from None
            out = run_git(str(found), args)
        else:
            raise
    commits: list[Commit] = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        # Chunk layout: main \x1d refs \x1d numstat-lines
        parts = chunk.split("\x1d")
        main, refs = parts[0], parts[1] if len(parts) > 1 else ""
        stat_text = "\x1d".join(parts[2:]) if len(parts) > 2 else ""

        main_lines = main.splitlines()
        fields = main_lines[0].split("\x1f")
        if len(fields) < 4:
            continue
        h, author, date, subject = fields[:4]
        body = "\x1f".join(fields[4:])
        if len(main_lines) > 1:
            body += "\n" + "\n".join(main_lines[1:])
        body = body.strip()[:600]

        files: list[str] = []
        total_added = total_deleted = 0
        for line in stat_text.splitlines():
            parsed = _parse_numstat_line(line)
            if parsed is None:
                continue
            path, added, deleted = parsed
            if added is None or deleted is None:
                files.append(f"{path} (binary)")
            else:
                files.append(f"{path} (+{added} -{deleted})")
                total_added += added
                total_deleted += deleted
        shortstat = (
            f"{len(files)} files changed, +{total_added} -{total_deleted}"
            if files
            else ""
        )
        commits.append(
            Commit(
                hash=h,
                author=author,
                author_date=date,
                subject=subject,
                body=body,
                refs=refs,
                files=files,
                shortstat=shortstat,
            )
        )
    return commits


def build_context(commits: list[Commit]) -> str:
    return json.dumps([asdict(c) for c in commits], indent=2)


def build_prompt(commits: list[Commit], bullets: bool) -> str:
    style = "a bullet list grouped by author" if bullets else "ONE paragraph"
    style_rule = (
        "Bullets, grouped by author, one or two lines each."
        if bullets
        else "A single paragraph, under 130 words."
    )
    return f"""Write {style} standup summary of what each developer shipped since the last standup, from the commit data below.

Rules:
- {style_rule}
- Name each author once; cover what they shipped.
- Focus on user-facing or project-relevant progress. Ignore trivial churn (typos, formatting).
- Never invent facts that are not in the data.
- Mention unfinished or in-flight work only if the data supports it (WIP commits, open refs).

Commits JSON:
{json.dumps([asdict(c) for c in commits], indent=2)}"""


def summarize(
    commits: list[Commit],
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    bullets: bool = False,
    dry_run: bool = False,
) -> str:
    if not commits:
        return "No commits in the window."
    prompt = build_prompt(commits, bullets)
    if dry_run:
        return prompt
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it, pass --api-key, "
            "or use --dry-run to preview without calling the API."
        )
    # Imported lazily so --dry-run / --json work without the SDK installed.
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model,
        max_tokens=350,
        temperature=0.3,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in msg.content if getattr(block, "type", "") == "text"
    ).strip()
