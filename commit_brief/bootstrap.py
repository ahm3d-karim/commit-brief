"""First-run bootstrap for commit-brief: tool checks, consent-based installs, config flag.

Runs once — the first_run_done flag is persisted in CONFIG_PATH (JSON). A second
call is a silent no-op. Zero dependencies (stdlib only), no ANSI (the caller may
add color).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("COMMIT_BRIEF_CONFIG", Path.home() / ".commit-brief.json")
)

FIRST_RUN_KEY = "first_run_done"

# display name -> (candidate executables, install hint)
TOOLS = [
    ("git", ["git"], "winget install -e --id Git.Git"),
    ("uv", ["uv"], 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'),
    ("python", ["python", "python3"], "winget install -e --id Python.Python.3.12"),
]

API_KEY_VARS = ("ANTHROPIC_API_KEY", "COMMIT_BRIEF_API_KEY")
NO_API_KEY_NOTE = (
    "no LLM API key found — summaries need one; --json and --dry-run work without it"
)


def _load_config() -> dict:
    """Read CONFIG_PATH as JSON; {} on missing, unreadable, or malformed file."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_config(config: dict) -> None:
    """Best-effort write of the full config dict; chmod 600 on POSIX."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
            fh.write("\n")
    except OSError:
        return
    if os.name != "nt":  # best-effort 0600 on POSIX
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass


def _which_tool(candidates: list[str]) -> str | None:
    """First match from the candidate names, or None."""
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def _prompt_install(name: str) -> bool:
    """Ask for consent; True only on explicit 'y' (EOF counts as decline)."""
    try:
        answer = input(f"{name} not found. Install it now? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"


def _check_tools(interactive: bool) -> None:
    """Print status for git/uv/python; offer consent-based installs when interactive."""
    for display, candidates, hint in TOOLS:
        path = _which_tool(candidates)
        if path:
            print(f"  ok {display}: {path}")
            continue
        print(f"  missing {display} — install with: {hint}")
        if not interactive:
            continue
        if _prompt_install(display):
            subprocess.run(hint, shell=True, check=False)
            path = _which_tool(candidates)
            if path:
                print(f"  ok {display}: {path}")
            else:
                print(f"  still missing {display}")
        else:
            print(f"  skipped — later: {hint}")


def _check_api_key() -> None:
    """Note a missing LLM API key; never prompts for one."""
    if any(os.environ.get(var) for var in API_KEY_VARS):
        return
    print(f"  note: {NO_API_KEY_NOTE}")


def bootstrap(interactive: bool) -> None:
    """First-run setup: check tools, consent-based installs, API key note, flag once.

    Persists {first_run_done: True} in CONFIG_PATH (merged with any existing
    keys). A subsequent call returns without printing anything.
    """
    config = _load_config()
    if config.get(FIRST_RUN_KEY):
        return
    _check_tools(interactive)
    _check_api_key()
    config[FIRST_RUN_KEY] = True
    _save_config(config)


if __name__ == "__main__":
    bootstrap(interactive=sys.stdin.isatty())
