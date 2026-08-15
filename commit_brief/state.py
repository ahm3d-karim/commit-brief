"""Incremental digest state: last-digested commit SHA per repo.

Persisted in the shared JSON config (bootstrap.CONFIG_PATH) under the
top-level "digest_state" key, so a run never re-digests commits it has
already summarized. Reuses bootstrap's config IO — no separate state
file, and other config keys are never touched.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from commit_brief import bootstrap, core

DIGEST_STATE_KEY = "digest_state"


def canonical_repo_key(repo: str) -> str:
    """Canonical key for a repo path: absolute top-level path.

    Resolves the top level via `git rev-parse --show-toplevel` and strips
    any trailing whitespace (on Windows the output may end in CRLF).
    Falls back to os.path.abspath(repo) when git fails (not a repo, or
    git missing). On Windows the key is normalized to lowercase
    backslash form so 'C:/Repo' and 'c:\\repo' map to the same entry;
    on POSIX normcase is the identity.
    """
    try:
        out = core.run_git(repo, ["rev-parse", "--show-toplevel"])
    except RuntimeError:
        return os.path.normcase(os.path.abspath(repo))
    path = out.strip()
    if not path:
        return os.path.normcase(os.path.abspath(repo))
    return os.path.normcase(os.path.abspath(path))


def get_state() -> dict:
    """Raw digest_state mapping: repo key -> {"sha": ..., "when": ...}."""
    config = bootstrap._load_config()
    state = config.get(DIGEST_STATE_KEY)
    return state if isinstance(state, dict) else {}


def last_digested_sha(repo: str) -> str | None:
    """SHA of the last digested commit for repo, or None when never digested."""
    entry = get_state().get(canonical_repo_key(repo))
    if not isinstance(entry, dict):
        return None
    sha = entry.get("sha")
    return sha if isinstance(sha, str) and sha else None


def mark_digested(repo: str, sha: str) -> None:
    """Persist sha as the last digested commit for repo.

    Merged into the existing config under "digest_state"; all other
    config keys are preserved untouched. `when` is an ISO-8601 UTC
    timestamp.
    """
    config = bootstrap._load_config()
    state = config.get(DIGEST_STATE_KEY)
    state = dict(state) if isinstance(state, dict) else {}
    state[canonical_repo_key(repo)] = {
        "sha": sha,
        "when": datetime.now(timezone.utc).isoformat(),
    }
    bootstrap._save_config({**config, DIGEST_STATE_KEY: state})


def _commit_sha(commit) -> str | None:
    """Hash of a Commit-like item (core.Commit dataclass or plain dict)."""
    if isinstance(commit, dict):
        return commit.get("hash")
    return getattr(commit, "hash", None)


def commits_since_last(commits: list, repo: str) -> list:
    """Commits newer than the saved digest point (newest-first input).

    Given collect_commits() output (newest first), keeps every commit
    up to — but not including — the saved sha, then stops. When no sha
    was saved, or the saved sha no longer appears in the list (rebase,
    force-push, rewritten history), the list is returned unchanged: the
    digest falls back to the full window.
    """
    saved = last_digested_sha(repo)
    if saved is None:
        return commits
    for index, commit in enumerate(commits):
        if _commit_sha(commit) == saved:
            return commits[:index]
    return commits
