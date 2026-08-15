"""Tests for commit_brief.state — the incremental digest state store.

Run (from the repo root):
  C:/Users/Ahmad Karim/Documents/Projects/Active/fde/commit-brief/.venv/Scripts/python.exe -m pytest tests/test_state.py -q
"""

from __future__ import annotations

import os
import subprocess

import pytest

from commit_brief import bootstrap as bs
from commit_brief.core import Commit, collect_commits
from commit_brief.state import (
    canonical_repo_key,
    commits_since_last,
    get_state,
    last_digested_sha,
    mark_digested,
)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the shared config at a throwaway file for every test."""
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("COMMIT_BRIEF_CONFIG", str(cfg))
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg)
    return cfg


def make_repo(tmp_path, name="repo", n=1):
    """Fresh git repo with n empty commits; returns (path, shas oldest-first)."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "-b", "main"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test Runner"],
        check=True, capture_output=True,
    )
    shas = []
    for i in range(n):
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", f"c{i + 1}"],
            check=True, capture_output=True,
        )
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        shas.append(out.stdout.strip())
    return str(repo), shas


def fake_commit(hash_: str) -> Commit:
    return Commit(
        hash=hash_, author="A", author_date="2026-08-15T00:00:00+00:00",
        subject="s", body="", refs="", files=[], shortstat="",
    )


# ---- canonical_repo_key ----------------------------------------------------


def test_canonical_repo_key_strips_trailing_newline(tmp_path):
    """git's --show-toplevel output (possibly CRLF-terminated on Windows)
    must be stripped and normalized to the same key as the input path."""
    repo, _ = make_repo(tmp_path, n=1)
    key = canonical_repo_key(repo)
    assert not key.endswith(("\n", "\r"))
    assert key == canonical_repo_key(repo)  # stable across calls
    assert key == os.path.normcase(os.path.abspath(repo))


def test_canonical_repo_key_fallback_when_not_a_repo(tmp_path):
    target = tmp_path / "not-a-repo"
    assert canonical_repo_key(str(target)) == os.path.normcase(
        os.path.abspath(str(target))
    )


# ---- mark / read roundtrip -------------------------------------------------


def test_mark_read_roundtrip(tmp_path, isolated_config):
    repo, shas = make_repo(tmp_path, n=1)
    assert last_digested_sha(repo) is None
    mark_digested(repo, shas[0])
    assert last_digested_sha(repo) == shas[0]
    entry = get_state()[canonical_repo_key(repo)]
    assert entry["sha"] == shas[0]
    assert entry["when"]  # ISO-8601 UTC timestamp present


def test_state_survives_reload_and_preserves_other_keys(tmp_path, isolated_config):
    repo, shas = make_repo(tmp_path, n=1)
    bs._save_config({"first_run_done": True, "llm_keys": {"default": "sk-x"}})
    mark_digested(repo, shas[0])
    reloaded = bs._load_config()  # fresh read from disk
    assert reloaded["digest_state"][canonical_repo_key(repo)]["sha"] == shas[0]
    assert reloaded["first_run_done"] is True
    assert reloaded["llm_keys"] == {"default": "sk-x"}
    assert last_digested_sha(repo) == shas[0]


# ---- commits_since_last ----------------------------------------------------


def test_commits_since_last_keeps_only_newer(tmp_path, isolated_config):
    repo, _ = make_repo(tmp_path, n=3)
    commits = collect_commits(repo=repo, since="1 day ago")  # newest first
    assert len(commits) == 3
    mark_digested(repo, commits[1].hash)  # digest the middle commit
    assert [c.hash for c in commits_since_last(commits, repo)] == [commits[0].hash]


def test_commits_since_last_saved_is_newest_returns_empty(tmp_path, isolated_config):
    repo, _ = make_repo(tmp_path, n=2)
    commits = collect_commits(repo=repo, since="1 day ago")
    mark_digested(repo, commits[0].hash)
    assert commits_since_last(commits, repo) == []


def test_commits_since_last_none_fallback(tmp_path, isolated_config):
    """No saved sha -> full list returned unchanged (same object)."""
    repo, _ = make_repo(tmp_path, n=1)
    commits = [fake_commit("a1")]
    assert commits_since_last(commits, repo) is commits


def test_commits_since_last_sha_not_found_fallback(tmp_path, isolated_config):
    """Saved sha missing from the list (rebase/force-push) -> unchanged."""
    repo, _ = make_repo(tmp_path, n=1)
    mark_digested(repo, "deadbeef")
    commits = [fake_commit("a1"), fake_commit("a2")]
    assert commits_since_last(commits, repo) == commits


def test_commits_since_last_accepts_dict_commits(tmp_path, isolated_config):
    """Plain dicts with a 'hash' key work as well as Commit dataclasses."""
    repo, _ = make_repo(tmp_path, n=1)
    mark_digested(repo, "b2")
    commits = [{"hash": "b3"}, {"hash": "b2"}, {"hash": "b1"}]
    assert [c["hash"] for c in commits_since_last(commits, repo)] == ["b3"]


# ---- multi-repo isolation --------------------------------------------------


def test_two_repos_do_not_collide(tmp_path, isolated_config):
    repo_a, shas_a = make_repo(tmp_path, name="repo_a", n=1)
    repo_b, shas_b = make_repo(tmp_path, name="repo_b", n=1)
    mark_digested(repo_a, shas_a[0])
    mark_digested(repo_b, shas_b[0])
    assert last_digested_sha(repo_a) == shas_a[0]
    assert last_digested_sha(repo_b) == shas_b[0]
    assert len(get_state()) == 2


# ---- resilience to bad config ----------------------------------------------


def test_missing_config_returns_none(tmp_path, isolated_config):
    repo, _ = make_repo(tmp_path, n=1)
    assert last_digested_sha(repo) is None
    assert get_state() == {}


def test_malformed_config_returns_none_not_crash(tmp_path, isolated_config):
    isolated_config.write_text("{not valid json!!", encoding="utf-8")
    repo, _ = make_repo(tmp_path, n=1)
    assert last_digested_sha(repo) is None
    assert get_state() == {}
    mark_digested(repo, "abc123")  # must recover and persist
    assert last_digested_sha(repo) == "abc123"
