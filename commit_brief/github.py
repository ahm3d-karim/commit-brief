"""commit-brief GitHub mode — standup digests for any GitHub repo, zero deps.

Connect flow: authenticated `gh` CLI > GITHUB_TOKEN env > token saved in
CONFIG_PATH (shared with bootstrap.py) > interactive consent menu
(`gh auth login` / paste a token / skip). Then list your repos (paginated),
pick several (numbers, ranges, 'all', name search, or arbitrary owner/repo
fetched via the API), shallow-clone each into a temp dir and print a standup
digest per repo through the existing core.collect_commits + core.summarize
pipeline.

Network: api.github.com (REST) + `git clone`. No third-party dependencies.

Usage (cli.py wires this up):
    from commit_brief.github import github_mode
    rc = github_mode("yesterday", authors=None, api_key=None, model=None)
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import bootstrap
from .core import collect_commits
from .llm import summarize as llm_summarize

API = "https://api.github.com"
USER_AGENT = "commit-brief/0.7.0"
TOKEN_CONFIG_KEY = "github_token"
NOT_CONNECTED_MSG = "commit-brief: GitHub not connected"
NO_SELECTION_MSG = "commit-brief: no repos selected"

# --------------------------------------------------------------------------
# terminal styling — zero-dep ANSI; inert when piped or NO_COLOR
# (same pattern as cli.py / the agentize CLI)
# --------------------------------------------------------------------------


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _enable_windows_vt() -> None:
    """Windows: enable ANSI processing on the console — the colorama.init()
    equivalent, zero deps. `os.system('')` is the classic toggle; the ctypes
    branch (kernel32 console mode) covers consoles the toggle misses, e.g.
    raw \\x1b leakage on Python 3.11 cmd.exe."""
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
        for std in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
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
# GitHub API
# --------------------------------------------------------------------------


class GitHubError(Exception):
    """API failure with HTTP status (0 = transport/parse error) — per-repo
    errors must not kill the run."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def gh_token() -> str | None:
    """Reuse an authenticated gh CLI if present — zero setup for gh users."""
    try:
        r = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    t = r.stdout.strip()
    return t if r.returncode == 0 and t else None


def api_call(token: str, path: str, method: str = "GET",
             body: dict | None = None) -> dict | list:
    """Minimal GitHub REST client. Errors raise GitHubError with status."""
    req = urllib.request.Request(f"{API}{path}", method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", USER_AGENT)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            payload = e.read()
        except OSError:
            payload = b""
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        message = ""
        try:
            message = json.loads(payload).get("message", "") or ""
        except (ValueError, AttributeError):
            pass
        detail = (message or payload).strip()[:300]
        raise GitHubError(
            e.code, f"GitHub API {e.code} on {path}: {detail or str(e)}"
        ) from None
    except urllib.error.URLError as e:
        raise GitHubError(0, f"network error on {path}: {e.reason}") from None
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise GitHubError(0, f"non-JSON response from {path}") from None


def auth_header(token: str) -> str:
    """git -c value: Basic-auth header with the token base64'd — the token
    never lands in a remote URL or in git config."""
    cred = base64.b64encode(("x-access-token:" + token).encode()).decode()
    return "http.extraheader=AUTHORIZATION: Basic " + cred


def _valid_token(token: str) -> bool:
    """True if the token authenticates against /user."""
    try:
        api_call(token, "/user")
        return True
    except GitHubError:
        return False


# --------------------------------------------------------------------------
# connect
# --------------------------------------------------------------------------


def connect_github() -> str:
    """Return a working token: gh CLI > GITHUB_TOKEN env > saved config >
    interactive consent (gh auth login / paste a token / skip)."""
    for token in (gh_token(), os.environ.get("GITHUB_TOKEN")):
        if token and _valid_token(token):
            return token
    cfg = bootstrap._load_config()
    token = cfg.get(TOKEN_CONFIG_KEY)
    if token and _valid_token(token):
        return token
    print(warn("GitHub not connected."))
    print()
    print("  1.  Run `gh auth login` (recommended)")
    print("  2.  Paste a personal access token (saved locally)")
    print("  3.  Skip")
    attempts = 0
    while attempts < 3:
        try:
            ans = input("\n  How do you want to connect? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(NOT_CONNECTED_MSG) from None
        if ans in ("1", "gh", "login"):
            if not shutil.which("gh"):
                print(err("  gh CLI not installed — use option 2 (paste a token)."))
                continue
            print(dim("  Running `gh auth login` — follow the prompts…"))
            try:
                rc = subprocess.run(["gh", "auth", "login"]).returncode
            except OSError:
                rc = 1
            token = gh_token() if rc == 0 else None
            if token and _valid_token(token):
                print(ok("Connected via gh CLI"))
                return token
            attempts += 1
            print(warn("  gh still not authenticated — paste a token instead."))
            continue
        if ans in ("2", "token", "paste"):
            print(dim("  Create one: https://github.com/settings/tokens  (scope: repo)"))
            try:
                token = input("  Token: ").strip()
            except (EOFError, KeyboardInterrupt):
                raise SystemExit(NOT_CONNECTED_MSG) from None
            if not token:
                continue
            try:
                api_call(token, "/user")
            except GitHubError as e:
                attempts += 1
                print(err(f"  token rejected: {e}"))
                continue
            bootstrap._save_config(
                {**bootstrap._load_config(), TOKEN_CONFIG_KEY: token}
            )
            print(ok("Connected with token (saved to config)"))
            return token
        if ans in ("3", "skip", "q", "quit"):
            raise SystemExit(NOT_CONNECTED_MSG)
        print("  ?")
    raise SystemExit(NOT_CONNECTED_MSG)


# --------------------------------------------------------------------------
# repo listing + selection
# --------------------------------------------------------------------------


def list_repos(token: str) -> list[dict]:
    """All repos the token can see, most recently updated first."""
    repos: list[dict] = []
    page = 1
    while True:
        batch = api_call(token, f"/user/repos?per_page=100&page={page}&sort=updated")
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def parse_selection(text: str, n: int) -> list[int]:
    """'1 3,5' -> [1,3,5]; '2-4' -> [2,3,4]; 'all'/'*'/'' -> 1..n.
    Out-of-range indices are silently dropped; non-numeric input raises
    ValueError (so callers can fall back to search)."""
    text = text.strip().lower()
    if text in ("all", "*", ""):
        return list(range(1, n + 1))
    out: set[int] = set()
    for part in re.split(r"[\s,]+", text):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(i for i in out if 1 <= i <= n)


def _dedupe(repos: list[dict]) -> list[dict]:
    """Drop duplicate repos (by full_name), keeping first-seen order."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in repos:
        name = r.get("full_name") or ""
        if name and name not in seen:
            seen.add(name)
            out.append(r)
    return out


def _resolve_tokens(token: str, repos: list[dict], text: str) -> list[dict]:
    """Mixed input: numbers, ranges, name search terms and owner/repo specs.
    owner/repo entries are fetched via the API, so repos the token can see
    but doesn't own are allowed. Failures print per token to stderr."""
    selected: list[dict] = []
    for w in [t for t in re.split(r"[\s,]+", text.strip()) if t]:
        try:
            idx = parse_selection(w, len(repos))
            if idx:
                selected.extend(repos[i - 1] for i in idx)
                continue
        except ValueError:
            pass
        if "/" in w:
            try:
                selected.append(api_call(token, f"/repos/{w}"))
            except GitHubError as e:
                print(err(f"  {w}: {e}"), file=sys.stderr)
            continue
        hits = [r for r in repos if w.lower() in r["name"].lower()]
        if not hits:
            print(f"  no repos match '{w}'")
        selected.extend(hits)
    return _dedupe(selected)


def pick_repos(token: str, repos: list[dict]) -> list[dict]:
    """Interactive numbered multi-select. Accepts numbers ('1 3', '2-5'),
    'all' (or empty input), name search terms, and owner/repo specs. Up to
    3 bad inputs, then exits. Returns deduped repo dicts."""
    print(f"\nYour GitHub repos ({len(repos)}):")
    for i, r in enumerate(repos, 1):
        flags = []
        if r.get("private"):
            flags.append("private")
        if r.get("fork"):
            flags.append("fork")
        tag = f"  ({', '.join(flags)})" if flags else ""
        print(f"  {i:>3}.  {r['full_name']}{tag}")
    for _ in range(3):
        try:
            ans = input(
                "\nPick repos — numbers (1 3, 2-5), all, search, or owner/repo: "
            )
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(NO_SELECTION_MSG) from None
        try:
            idx = parse_selection(ans, len(repos))
        except ValueError:
            idx = None
        if idx is not None:
            if idx:
                return _dedupe([repos[i - 1] for i in idx])
        else:
            selected = _resolve_tokens(token, repos, ans)
            if selected:
                return selected
        print("  no match — try again")
    raise SystemExit(NO_SELECTION_MSG)


# --------------------------------------------------------------------------
# per-repo digest
# --------------------------------------------------------------------------


def _clone_repo(token: str, repo: dict, dest: Path) -> None:
    """Shallow clone into `dest`; extraheader auth for private repos.
    Raises RuntimeError on failure."""
    url = repo.get("clone_url") or f"https://github.com/{repo['full_name']}.git"
    cmd = ["git", "clone", "--depth", "1", "--quiet"]
    if repo.get("private"):
        cmd += ["-c", auth_header(token)]
    cmd += [url, str(dest)]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("git not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"clone of {repo['full_name']} timed out") from None
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or "(no stderr)"
        raise RuntimeError(f"clone of {repo['full_name']} failed: {detail[:200]}")


def _rmtree_force(path: str) -> None:
    """rmtree that survives Windows read-only file attributes."""

    def _onerr(func, p, _exc_info):
        try:
            os.chmod(p, 0o777)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, ignore_errors=True, onerror=_onerr)


def _digest_repo(
    token: str,
    repo: dict,
    since: str,
    authors: list[str] | None,
    api_key: str | None,
    model: str | None,
    provider: str,
    base_url: str | None,
) -> str:
    """Clone one repo and produce its standup digest. Returns the digest
    text, or '' when there are no commits in the window. Raises
    GitHubError/RuntimeError on failure (github_mode catches per repo)."""
    name = repo["full_name"]
    work = Path(tempfile.mkdtemp(prefix="commit-brief-"))
    try:
        _clone_repo(token, repo, work)
        commits = collect_commits(
            str(work), since=since, until=None, authors=authors
        )
        if not commits:
            print(f"No commits since {since} in {name}.")
            return ""
        return llm_summarize(
            commits,
            provider=provider,
            model=model,  # None → llm_setup resolves the registry default (or prompts for custom)
            api_key=api_key,
            base_url=base_url,
        )
    finally:
        _rmtree_force(str(work))


def github_mode(
    since: str,
    authors: list[str] | None,
    api_key: str | None,
    model: str | None = None,
    provider: str = "anthropic",
    base_url: str | None = None,
) -> int:
    """GitHub mode end to end: connect -> list -> pick -> per-repo digest.
    Returns 0 when every selected repo produced a digest, 1 if any failed."""
    start = time.time()
    token = connect_github()
    print(dim("Loading your repos…"))
    try:
        repos = list_repos(token)
    except GitHubError as e:
        print(err(str(e)), file=sys.stderr)
        return 1
    if not repos:
        print(warn("No repositories found for this account."))
        return 0
    chosen = pick_repos(token, repos)
    failed = 0
    for i, repo in enumerate(chosen, 1):
        name = repo["full_name"]
        print(dim(f"  [{i}/{len(chosen)}] {name}…"))
        try:
            digest = _digest_repo(token, repo, since, authors, api_key, model,
                                  provider, base_url)
        except (GitHubError, RuntimeError) as e:
            failed += 1
            print(err(f"  {name}: {e}"), file=sys.stderr)
            continue
        if not digest:
            continue
        print()
        print(bold(f"{name} — commits since {since}"))
        print(digest)
        print()
    elapsed = time.time() - start
    print(ok(f"Done — {len(chosen)} repo(s) in {elapsed:.0f}s"))
    return 1 if failed else 0


if __name__ == "__main__":
    # Standalone smoke entry — the real CLI lives in cli.py.
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m commit_brief.github",
        description="commit-brief GitHub mode (standalone)",
    )
    ap.add_argument("--since", default="yesterday")
    ap.add_argument("--author", action="append", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    raise SystemExit(github_mode(a.since, a.author, a.api_key, a.model))
