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
import sys

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


# ---- interactive CLI + first-run bootstrap (v0.7) --------------------------


def test_bare_piped_prints_help_without_hanging(monkeypatch, capsys):
    """Bare invocation with piped stdin: help, exit 0, never blocks."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "commit-brief" in out


def test_ask_history_defaults(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    assert cli.ask_history_defaults() == ("yesterday", None)
    answers = iter(["3 days ago", "Alice, Bob"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    assert cli.ask_history_defaults() == ("3 days ago", ["Alice", "Bob"])


def test_find_repos_in_tree_no_duplicates(tmp_path, monkeypatch):
    """Running from inside a repo must list it exactly once (dedupe)."""
    repo = make_repo(tmp_path, [("Alice", "x", None)])
    monkeypatch.chdir(pathlib.Path(repo))
    found = cli.find_repos_in_tree(pathlib.Path(repo))
    assert len(found) == 1
    assert pathlib.Path(found[0]).resolve() == pathlib.Path(repo).resolve()


def test_bootstrap_declined_runs_nothing(tmp_path, monkeypatch, capsys):
    """First-run with everything missing + all declines: zero installs, flag saved."""
    from commit_brief import bootstrap as bs

    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("COMMIT_BRIEF_CONFIG", str(cfg))
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg)
    calls = []
    monkeypatch.setattr(bs.shutil, "which", lambda _c: None)
    monkeypatch.setattr(bs.subprocess, "run", lambda *a, **k: calls.append(a) or None)
    monkeypatch.setattr("builtins.input", lambda _p="": "n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COMMIT_BRIEF_API_KEY", raising=False)

    bs.bootstrap(interactive=True)
    assert calls == []  # declined -> no installer may run
    assert bs._load_config().get("first_run_done") is True

    capsys.readouterr()  # drain first-run output
    bs.bootstrap(interactive=True)
    assert capsys.readouterr().out == ""  # second call: silent no-op


# ---- API-key prompting + GitHub mode (v0.8) ---------------------------------


def test_resolve_api_key_precedence(tmp_path, monkeypatch):
    """env COMMIT_BRIEF_API_KEY > ANTHROPIC_API_KEY > config file."""
    from commit_brief import bootstrap as bs

    cfg = tmp_path / "c.json"
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COMMIT_BRIEF_API_KEY", raising=False)
    assert bs.resolve_api_key() is None
    bs._save_config({"anthropic_api_key": "sk-config-key-1234567890"})
    assert bs.resolve_api_key() == "sk-config-key-1234567890"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-a-1234567890123456")
    assert bs.resolve_api_key() == "sk-env-a-1234567890123456"
    monkeypatch.setenv("COMMIT_BRIEF_API_KEY", "sk-env-cb-123456789012345")
    assert bs.resolve_api_key() == "sk-env-cb-123456789012345"


def test_ensure_api_key_prompts_saves_rejects_bad(tmp_path, monkeypatch, capsys):
    from commit_brief import bootstrap as bs

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COMMIT_BRIEF_API_KEY", raising=False)
    cfg = tmp_path / "c.json"
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg)
    monkeypatch.setattr("builtins.input", lambda _p="": "sk-pasted-123456789012345678")
    assert bs.ensure_api_key(interactive=True) == "sk-pasted-123456789012345678"
    assert bs._load_config()["anthropic_api_key"] == "sk-pasted-123456789012345678"
    # non-interactive never prompts
    cfg2 = tmp_path / "c2.json"
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg2)
    assert bs.ensure_api_key(interactive=False) is None
    # malformed key rejected
    monkeypatch.setattr("builtins.input", lambda _p="": "too short")
    cfg3 = tmp_path / "c3.json"
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg3)
    assert bs.ensure_api_key(interactive=True) is None
    assert "invalid key format" in capsys.readouterr().out


def test_github_parse_selection():
    from commit_brief.github import parse_selection

    assert parse_selection("1 3,5", 10) == [1, 3, 5]
    assert parse_selection("2-4", 10) == [2, 3, 4]
    assert parse_selection("all", 5) == [1, 2, 3, 4, 5]
    assert parse_selection("11", 5) == []


def test_parser_has_github_flag():
    assert cli.build_parser().parse_args(["--github"]).github is True


def test_menu_routes_github_and_quit(monkeypatch, capsys):
    from commit_brief import bootstrap as bs

    monkeypatch.setattr(bs, "bootstrap", lambda interactive: None)
    monkeypatch.setattr(cli, "_menu_github_flow", lambda: 42)
    monkeypatch.setattr("builtins.input", lambda _p="": "2")
    assert cli.interactive_menu() == 42
    monkeypatch.setattr("builtins.input", lambda _p="": "q")
    capsys.readouterr()
    assert cli.interactive_menu() == 0
    assert "bye" in capsys.readouterr().out


# ---- multi-provider LLM (v0.9) ----------------------------------------------


def test_llm_provider_registry_complete():
    from commit_brief.llm import PROVIDERS

    for name in ("anthropic", "openai", "openrouter", "gemini", "xai",
                 "deepseek", "groq", "mistral", "ollama", "custom"):
        assert name in PROVIDERS
    assert PROVIDERS["ollama"]["key_env"] is None
    assert PROVIDERS["openai"]["key_env"] == "OPENAI_API_KEY"


def test_llm_summarize_dry_run_needs_no_key(tmp_path, monkeypatch):
    """dry-run returns the prompt for ANY provider with zero key/network."""
    from commit_brief.llm import summarize

    repo = make_repo(tmp_path, [("Alice", "feat: multi-provider", None)])
    commits = collect_commits(repo=repo, since="1 day ago")
    out = summarize(commits, provider="openai", dry_run=True)
    assert "feat: multi-provider" in out
    assert "Write ONE paragraph" in out
    out2 = summarize(commits, provider="groq", dry_run=True)
    assert "feat: multi-provider" in out2


def test_llm_summarize_rejects_unknown_provider(tmp_path, monkeypatch):
    from commit_brief.llm import summarize

    repo = make_repo(tmp_path, [("Alice", "x", None)])
    commits = collect_commits(repo=repo, since="1 day ago")
    with pytest.raises(RuntimeError):
        summarize(commits, provider="not-a-provider", dry_run=True)


def test_ensure_api_key_provider_aware(tmp_path, monkeypatch):
    from commit_brief import bootstrap as bs

    cfg = tmp_path / "c.json"
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COMMIT_BRIEF_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-env-12345678901234")
    assert bs.resolve_api_key(provider="openai") == "sk-openai-env-12345678901234"
    assert bs.resolve_api_key(provider="anthropic") is None
    # ollama never prompts, never needs a key
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    assert bs.ensure_api_key(provider="ollama", interactive=True) is None


def test_choose_provider_picker(monkeypatch):
    from commit_brief import llm

    monkeypatch.setattr("builtins.input", lambda _p="": "1")
    assert llm.choose_provider() == "anthropic"
    monkeypatch.setattr("builtins.input", lambda _p="": "openai")
    assert llm.choose_provider() == "openai"
    monkeypatch.setattr("builtins.input", lambda _p="": "q")
    assert llm.choose_provider() == "none"


def test_cli_provider_flags(monkeypatch, tmp_path, capsys):
    """--provider X --dry-run prints the prompt without any key."""
    repo = make_repo(tmp_path, [("Alice", "flag path", None)])
    assert cli.main(["--repo", str(repo), "--since", "1 day ago",
                     "--provider", "deepseek", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "flag path" in out
    assert cli.main(["--provider", "bogus", "--dry-run"]) == 2


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
