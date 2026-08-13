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

# provider -> env var holding its API key (None: provider needs no key)
PROVIDER_KEY_ENVS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "ollama": None,
    "huggingface": "HF_TOKEN",
    "zai": "GLM_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax_cn": "MINIMAX_CN_API_KEY",
    "kimi": "KIMI_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "xiaomi": "XIAOMI_API_KEY",
    "kilocode": "KILOCODE_API_KEY",
    "opencode_zen": "OPENCODE_ZEN_API_KEY",
    "opencode_go": "OPENCODE_GO_API_KEY",
    "custom": None,
}

NO_API_KEY_NOTE = (
    "no LLM API key yet — you will be asked for one when a summary is needed; "
    "--json and --dry-run never need one"
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
    """Note a missing LLM API key (any provider); never prompts for one."""
    if any(os.environ.get(var) for var in API_KEY_VARS):
        return
    if any(var and os.environ.get(var) for var in PROVIDER_KEY_ENVS.values()):
        return
    config = _load_config()
    llm_keys = config.get("llm_keys")
    if isinstance(llm_keys, dict) and any(
        (value or "").strip() for value in llm_keys.values()
    ):
        return
    if (config.get("anthropic_api_key") or "").strip():
        return
    print(f"  note: {NO_API_KEY_NOTE}")


def resolve_api_key(provider: str = "anthropic") -> str | None:
    """Resolve the API key for a provider, or None.

    Precedence: provider env var (e.g. ANTHROPIC_API_KEY) -> config
    ['llm_keys'][provider] -> legacy config['anthropic_api_key'] (anthropic
    only). COMMIT_BRIEF_API_KEY still applies as a legacy override for
    anthropic, ahead of ANTHROPIC_API_KEY. Values are stripped; an
    empty/whitespace-only value counts as unset. Providers without an env var
    (ollama, custom) fall straight through to the config.
    """
    if provider == "anthropic":
        # legacy generic override keeps precedence over the provider env
        value = (os.environ.get("COMMIT_BRIEF_API_KEY") or "").strip()
        if value:
            return value
    env_var = PROVIDER_KEY_ENVS.get(provider)
    if env_var:
        value = (os.environ.get(env_var) or "").strip()
        if value:
            return value
    config = _load_config()
    llm_keys = config.get("llm_keys")
    if isinstance(llm_keys, dict):
        value = (llm_keys.get(provider) or "").strip()
        if value:
            return value
    if provider == "anthropic":
        return (config.get("anthropic_api_key") or "").strip() or None
    return None


def ensure_api_key(
    provider: str = "anthropic", interactive: bool = False
) -> str | None:
    """Return a usable API key for a provider, prompting and persisting if needed.

    A resolved key (env or config) is returned as-is. Otherwise, when
    interactive, the user is asked to paste a key; a loosely valid answer is
    merged into CONFIG_PATH under config['llm_keys'][provider] (existing keys,
    incl. first_run_done, are preserved) and returned. Returns None when no
    key exists and none can be obtained. Keyless providers (ollama) return
    None without prompting.

    Backward-compatible positional bool: ensure_api_key(True) still means
    interactive=True for anthropic.
    """
    if isinstance(provider, bool):
        provider, interactive = "anthropic", provider
    if provider == "ollama":  # keyless provider: never prompts
        return None
    resolved = resolve_api_key(provider)
    if resolved:
        return resolved
    if not interactive:
        return None
    prompt = (
        f"{provider} API key not found. Paste one now (skippable — --json and "
        "--dry-run work without it) [Enter to skip]: "
    )
    try:
        key = input(prompt).strip()
    except EOFError:
        return None
    if not key:
        return None
    if provider != "custom" and (len(key) < 20 or " " in key):
        print("invalid key format")
        return None
    config = _load_config()
    llm_keys = config.get("llm_keys")
    llm_keys = dict(llm_keys) if isinstance(llm_keys, dict) else {}
    llm_keys[provider] = key
    new_config = {**config, "llm_keys": llm_keys}
    if provider == "anthropic":
        # migration nicety: the legacy flat key is folded into llm_keys and the
        # legacy slot is kept in sync (not dropped) — current tests still read
        # config['anthropic_api_key'] directly after a save.
        new_config["anthropic_api_key"] = key
    _save_config(new_config)
    return key


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
