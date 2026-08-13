"""Core + CLI tests for commit-brief.

Run: uv run --extra test pytest
The MCP smoke test is intentionally NOT here (it spawns servers and
os._exit()s) — use `commit-brief mcp-test` for that.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from commit_brief import cli
from commit_brief.core import collect_commits, summarize


def make_repo(tmp_path, commits):
    """Create a real git repo. commits: list of (author, subject, iso_date|None)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test Runner"],
                   check=True, capture_output=True)
    for author, subject, date in commits:
        (repo / "file.txt").write_text(subject + "\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        env = dict(os.environ)
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        subprocess.run(
            ["git", "-C", str(repo), "-c", f"user.name={author}",
             "-c", "user.email=dev@example.com", "commit", "-m", subject],
            check=True, capture_output=True, env=env,
        )
    return str(repo)


def test_parses_commits_with_stats(tmp_path):
    repo = make_repo(tmp_path, [("Alice", "feat: add thing", None), ("Bob", "fix: bug", None)])
    commits = collect_commits(repo=repo, since="1 day ago")
    assert [c.subject for c in commits] == ["fix: bug", "feat: add thing"]  # newest first
    assert {c.author for c in commits} == {"Alice", "Bob"}
    assert commits[0].files and "+" in commits[0].files[0]


def test_author_filter(tmp_path):
    repo = make_repo(tmp_path, [("Alice", "a1", None), ("Bob", "b1", None)])
    commits = collect_commits(repo=repo, since="1 day ago", authors=["Bob"])
    assert [c.subject for c in commits] == ["b1"]


def test_since_respects_dates(tmp_path):
    repo = make_repo(
        tmp_path,
        [("Alice", "old", "2026-01-01T00:00:00"), ("Alice", "new", None)],
    )
    assert [c.subject for c in collect_commits(repo=repo, since="2026-06-01")] == ["new"]
    assert len(collect_commits(repo=repo, since="2025-06-01")) == 2


def test_empty_window(tmp_path):
    repo = make_repo(tmp_path, [("Alice", "old", "2026-01-01T00:00:00")])
    assert collect_commits(repo=repo, since="yesterday") == []


def test_not_a_repo_raises(tmp_path):
    with pytest.raises(RuntimeError):
        collect_commits(repo=str(tmp_path / "nope"))


def test_walk_up_from_subdir(tmp_path, monkeypatch):
    """Running inside a subdir of a repo resolves the repo like git does."""
    repo = make_repo(tmp_path, [("Alice", "feat: x", None)])
    sub = pathlib.Path(repo) / "sub" / "deeper"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    commits = collect_commits(repo=".")
    assert [c.subject for c in commits] == ["feat: x"]


def test_no_repo_anywhere_friendly_message(tmp_path, monkeypatch):
    """Outside any repo: friendly actionable error, never git's raw fatal."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        collect_commits(repo=".")
    msg = str(exc.value)
    assert "no git repository here or in any parent" in msg
    assert "rc=128" not in msg and "fatal:" not in msg
    assert "--repo" in msg


def test_summarize_dry_run(tmp_path):
    repo = make_repo(tmp_path, [("Alice", "feat: x", None)])
    out = summarize(collect_commits(repo=repo, since="1 day ago"), dry_run=True)
    assert out.startswith("Write ONE paragraph")
    assert "Alice" in out


def test_summarize_empty():
    assert summarize([], dry_run=True) == "No commits in the window."


def test_cli_json(tmp_path, capsys):
    repo = make_repo(tmp_path, [("Alice", "feat: x", None)])
    rc = cli.main(["--repo", repo, "--since", "1 day ago", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out)[0]["author"] == "Alice"


def test_cli_dry_run(tmp_path, capsys):
    repo = make_repo(tmp_path, [("Alice", "feat: x", None)])
    rc = cli.main(["--repo", repo, "--since", "1 day ago", "--dry-run"])
    assert rc == 0
    assert "Write ONE paragraph" in capsys.readouterr().out


def test_cli_no_commits(tmp_path, capsys):
    repo = make_repo(tmp_path, [("Alice", "old", "2026-01-01T00:00:00")])
    rc = cli.main(["--repo", repo])  # default since=yesterday excludes it
    assert rc == 0
    assert "No commits since" in capsys.readouterr().out


def test_cli_missing_key(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path, [("Alice", "feat: x", None)])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = cli.main(["--repo", repo, "--since", "1 day ago"])
    assert rc == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_bad_repo(tmp_path, capsys):
    rc = cli.main(["--repo", str(tmp_path / "nope")])
    assert rc == 2


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "mcp" in out and "mcp-test" in out


def test_subcommand_parsing():
    assert cli.build_parser().parse_args(["mcp"]).command == "mcp"
    assert cli.build_parser().parse_args(["mcp-test", "some/repo"]).command == "mcp-test"
