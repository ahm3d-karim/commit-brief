"""commit_brief.hooks tests: conventional-commit validator + commit-msg hook.

Run: .venv/Scripts/python.exe -m pytest tests/test_hooks.py -q
(uv run is broken in git-bash on this machine.)

The e2e test needs `commit-brief` on PATH inside the git subprocess — it is
installed at ~/.local/bin/commit-brief.exe (uv tool install). If that dir is
missing from PATH in this test env it is prepended below; if the binary is
still unresolvable the e2e test is skipped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from commit_brief.hooks import (
    HOOK_MARKER,
    hook_installed,
    install_hook,
    uninstall_hook,
    validate_message,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# --- PATH: make `commit-brief` resolvable for the e2e git subprocess ---------
_LOCAL_BIN = Path.home() / ".local" / "bin"
if shutil.which("commit-brief") is None:
    for name in ("commit-brief.exe", "commit-brief"):
        if (_LOCAL_BIN / name).exists():
            os.environ["PATH"] = str(_LOCAL_BIN) + os.pathsep + os.environ["PATH"]
            break
_HAS_COMMIT_BRIEF = shutil.which("commit-brief") is not None


def _hook_env() -> dict:
    """Env for the e2e git subprocess.

    PYTHONPATH points at the repo root so the hook's transitional python
    fallback (`python -m commit_brief.hooks`) can import the package from any
    cwd; harmless once `commit-brief hook check` is the active path.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["GIT_PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(repo: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run git in `repo`; never inherit stdin (pager/console-state safety)."""
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
    )


def make_repo(tmp_path: Path) -> str:
    """Fresh git repo (main branch, local user config) as a string path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init", "-b", "main").check_returncode()
    _git(str(repo), "config", "user.email", "test@example.com").check_returncode()
    _git(str(repo), "config", "user.name", "Test Runner").check_returncode()
    return str(repo)


# --------------------------------------------------------------------------
# validator
# --------------------------------------------------------------------------

ACCEPT_CASES = [
    "feat: add login",
    "feat(api): add endpoint",
    "FIX: typo in docs",                      # uppercase type
    "Feat(ui): restyle header",               # mixed case
    "refactor(core)!: drop legacy shim",      # breaking change
    "ci: run linters",
    "test: cover the fallback path",
    "Merge branch 'main' into feature/x",
    "Revert \"feat: add login\"",
    "fixup! feat: add login",
    "squash! chore: tidy up",
    "amend! docs: readme",
    "feat: add login\n\nbody line one\nbody line two without a colon",  # multiline
]

REJECT_CASES = [
    ("", "empty"),
    ("   \n  ", "whitespace"),
    ("feat", "no colon"),
    ("feat: ", "empty subject"),
    ("feat:   ", "whitespace subject"),
    ("feat(api):", "empty subject after scope"),
    ("feat!: ", "empty subject after breaking"),
    ("bogus: whatever", "unknown type"),
    ("FIXED: typo", "unknown type (plural)"),
    ("feat(x y): nope", "invalid scope"),
    ("feat : spaced colon", "space before colon"),
    ("feat(xy", "unbalanced paren"),
]


@pytest.mark.parametrize("msg", ACCEPT_CASES)
def test_validate_accepts(msg: str) -> None:
    ok, reason = validate_message(msg)
    assert ok, f"expected accept for {msg!r}, got reason {reason!r}"
    assert reason


@pytest.mark.parametrize("msg,label", REJECT_CASES)
def test_validate_rejects(msg: str, label: str) -> None:
    ok, reason = validate_message(msg)
    assert not ok, f"expected reject for {msg!r} ({label})"
    assert reason


def test_validate_reject_reason_is_actionable() -> None:
    ok, reason = validate_message("bogus: x")
    assert not ok
    assert "unknown type" in reason and "feat" in reason
    ok, reason = validate_message("feat")
    assert not ok
    assert "missing" in reason


# --------------------------------------------------------------------------
# install / uninstall
# --------------------------------------------------------------------------


def test_install_writes_hook_with_marker(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    hook_path = Path(install_hook(repo))
    assert hook_path == Path(repo) / ".git" / "hooks" / "commit-msg"
    content = hook_path.read_text(encoding="utf-8")
    assert HOOK_MARKER in content
    assert "commit-brief hook check" in content
    if os.name != "nt":
        assert hook_path.stat().st_mode & 0o111  # executable on POSIX


def test_install_creates_missing_hooks_dir(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    hooks_dir = Path(repo) / ".git" / "hooks"
    shutil.rmtree(hooks_dir)
    install_hook(repo)
    assert (hooks_dir / "commit-msg").exists()


def test_hook_installed_true_and_false(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert hook_installed(repo) is False
    install_hook(repo)
    assert hook_installed(repo) is True


def test_uninstall_removes_installed_hook(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    install_hook(repo)
    assert uninstall_hook(repo) is True
    assert not (Path(repo) / ".git" / "hooks" / "commit-msg").exists()
    assert hook_installed(repo) is False


def test_uninstall_no_hook_returns_false(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert uninstall_hook(repo) is False


def test_uninstall_refuses_foreign_hook(tmp_path: Path) -> None:
    """A hook commit-brief did not install must survive uninstall."""
    repo = make_repo(tmp_path)
    hook = Path(repo) / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\n# my own pre-existing hook\nexit 0\n", encoding="utf-8")
    assert uninstall_hook(repo) is False
    assert hook.exists()
    assert hook_installed(repo) is False


# --------------------------------------------------------------------------
# module CLI
# --------------------------------------------------------------------------


def test_module_cli_check(tmp_path: Path) -> None:
    ok_file = tmp_path / "ok.txt"
    ok_file.write_text("feat: cli works\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "commit_brief.hooks", "--check", str(ok_file)],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    assert r.returncode == 0, r.stderr
    assert "commit-brief:" in r.stderr

    bad_file = tmp_path / "bad.txt"
    bad_file.write_text("nope\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "commit_brief.hooks", "--check", str(bad_file)],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    assert r.returncode == 1
    assert "missing" in r.stderr


# --------------------------------------------------------------------------
# end-to-end: the installed hook enforces conventional commits via git
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not _HAS_COMMIT_BRIEF,
    reason="'commit-brief' not on PATH — the installed hook needs it",
)
def test_hook_end_to_end_rejects_bad_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    install_hook(repo)
    env = _hook_env()

    bad = _git(repo, "commit", "--allow-empty", "-m", "bad message", env=env)
    assert bad.returncode != 0, f"bad commit was accepted:\n{bad.stdout}\n{bad.stderr}"

    good = _git(repo, "commit", "--allow-empty", "-m", "feat: good", env=env)
    assert good.returncode == 0, f"good commit was rejected:\n{good.stdout}\n{good.stderr}"
