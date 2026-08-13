"""commit-brief LLM layer: zero-dependency multi-provider chat completions.

Every provider — Anthropic, OpenAI, OpenRouter, Gemini, xAI, DeepSeek,
Groq, Mistral, local Ollama, and user-defined OpenAI-compatible endpoints —
is driven through ONE OpenAI-style POST to /chat/completions via stdlib
urllib. No SDKs, no requests, no third-party deps.

Design rule (shared with core): commit messages, file paths and change
stats go to the LLM — never diffs.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import urllib.error
import urllib.request

from . import bootstrap
from .bootstrap import ensure_api_key, resolve_api_key
from .core import SYSTEM_PROMPT, build_prompt

USER_AGENT = "commit-brief/0.8.0"

# Provider registry — display names, OpenAI-compatible base URLs, default
# models, and the env var each provider's key is read from. `ollama` is
# local and keyless; `custom` is filled in interactively.
PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-20250514",
        "key_env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4",
        "key_env": "OPENROUTER_API_KEY",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "key_env": "GEMINI_API_KEY",
    },
    "xai": {
        "name": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-2",
        "key_env": "XAI_API_KEY",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
    },
    "mistral": {
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "key_env": "MISTRAL_API_KEY",
    },
    "ollama": {
        "name": "Ollama (local)",
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.2",
        "key_env": None,
    },
    "huggingface": {
        "name": "Hugging Face",
        "base_url": "https://router.huggingface.co/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "key_env": "HF_TOKEN",
    },
    "zai": {
        "name": "Z.AI (GLM)",
        "base_url": "https://api.z.ai/api/paas/v4",
        "model": "glm-4.6",
        "key_env": "GLM_API_KEY",
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-Text-01",
        "key_env": "MINIMAX_API_KEY",
    },
    "minimax_cn": {
        "name": "MiniMax CN",
        "base_url": "https://api.minimax.chat/v1",
        "model": "MiniMax-Text-01",
        "key_env": "MINIMAX_CN_API_KEY",
    },
    "kimi": {
        "name": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2-0711-preview",
        "key_env": "KIMI_API_KEY",
    },
    "dashscope": {
        "name": "Alibaba (DashScope)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "xiaomi": {
        "name": "Xiaomi MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "MiMo-7B-RL",
        "key_env": "XIAOMI_API_KEY",
    },
    "kilocode": {
        "name": "Kilo Code",
        "base_url": "https://api.kilocode.ai/v1",
        "model": "glm-4.6",
        "key_env": "KILOCODE_API_KEY",
    },
    "opencode_zen": {
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "model": "opencode/gpt-5.6-sol",
        "key_env": "OPENCODE_ZEN_API_KEY",
    },
    "opencode_go": {
        "name": "OpenCode Go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "deepseek-v4-flash",
        "key_env": "OPENCODE_GO_API_KEY",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "key_env": "FIREWORKS_API_KEY",
    },
    "novita": {
        "name": "Novita AI",
        "base_url": "https://api.novita.ai/v3/openai",
        "model": "deepseek/deepseek-r1-distill-llama-70b",
        "key_env": "NOVITA_API_KEY",
    },
    "arcee": {
        "name": "Arcee AI",
        "base_url": "https://api.arcee.ai/v2",
        "model": "arcee-nova",
        "key_env": "ARCEEAI_API_KEY",
    },
    "gmi": {
        "name": "GMI Cloud",
        "base_url": "https://api.gmi.cloud/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "key_env": "GMI_API_KEY",
    },
    "tencent": {
        "name": "Tencent TokenHub",
        "base_url": "https://api.tokenhub.tencent.com/v1",
        "model": "hunyuan-turbos",
        "key_env": "TOKENHUB_API_KEY",
    },
    "nvidia": {
        "name": "NVIDIA Build",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "deepseek-ai/deepseek-v3",
        "key_env": "NVIDIA_API_KEY",
    },
    "stepfun": {
        "name": "StepFun",
        "base_url": "https://api.stepfun.com/v1",
        "model": "step-2-16k",
        "key_env": "STEPFUN_API_KEY",
    },
    "custom": {
        "name": "Custom (OpenAI-compatible)",
        "base_url": None,
        "model": None,
        "key_env": None,
    },
}

# --------------------------------------------------------------------------
# terminal styling — zero-dep ANSI; inert when piped or NO_COLOR
# (pattern copied from commit_brief/cli.py)
# --------------------------------------------------------------------------


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _enable_windows_vt() -> None:
    """Windows: enable ANSI processing on the console — the colorama.init()
    equivalent, zero deps. `os.system('')` is the classic toggle; the ctypes
    branch (kernel32 console mode) covers consoles the toggle misses."""
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
        for _stream, std in ((sys.stdout, -11), (sys.stderr, -12)):
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


def dim(s: str) -> str:
    return _s("2", s)


def green(s: str) -> str:
    return _s("32", s)


def cyan(s: str) -> str:
    return _s("36", s)


def bold(s: str) -> str:
    return _s("1", s)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _trim(text: str, limit: int = 300) -> str:
    """Collapse whitespace and cap length for error messages."""
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _ask(prompt: str) -> str:
    """input() that degrades to '' on EOF/Ctrl-C (never blocks a pipe)."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _read_secret(provider: str, name: str) -> str:
    """Interactive getpass prompt for a provider key.

    When stdin is not a terminal (piped stdin, MCP protocol pipe) it must
    never be consumed by a prompt — defer to bootstrap's provider-aware
    helper in non-interactive mode instead.
    """
    if not sys.stdin.isatty():
        return (ensure_api_key(provider, interactive=False) or "").strip()
    try:
        return getpass.getpass(f"  {name} API key (stored locally): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


# --------------------------------------------------------------------------
# provider selection
# --------------------------------------------------------------------------


def choose_provider() -> str:
    """Numbered interactive picker; returns a PROVIDERS key or 'none'.

    Accepts a number, a provider key ('openai'), or a display name
    ('Google Gemini', case-insensitive). 'q'/'skip'/'none' (and EOF or
    three bad answers) -> 'none' — meaning: no provider selected.
    Enter accepts the first provider (Anthropic).
    """
    keys = list(PROVIDERS)
    print()
    print(dim("  LLM provider:"))
    for i, key in enumerate(keys, 1):
        p = PROVIDERS[key]
        marker = dim(" (local, no key)") if key == "ollama" else ""
        print(f"  {green(str(i) + '.')}  {p['name']}{marker}")
    for _ in range(3):
        try:
            answer = input("\n  Provider [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "none"
        if not answer:
            return keys[0]
        low = answer.lower()
        if low in ("q", "quit", "skip", "none"):
            return "none"
        if low in PROVIDERS:
            return low
        for key, p in PROVIDERS.items():
            if low == p["name"].lower():
                return key
        try:
            i = int(answer)
            if 1 <= i <= len(keys):
                return keys[i - 1]
        except ValueError:
            pass
        print("  ?")
    return "none"


# --------------------------------------------------------------------------
# per-provider configuration
# --------------------------------------------------------------------------


_UNSET = object()


def llm_setup(provider: str, key: object = _UNSET,
              model: str | None = None, base_url: str | None = None) -> dict:
    """Resolve key, base URL and model for `provider`, prompting as needed.

    `key` semantics: omitted (_UNSET) → resolve env/config, getpass if the
    provider needs one; passed (even None — 'already asked, user declined')
    → never prompts. model/base_url override registry defaults; custom
    providers are asked for base URL and model name when not provided.
    getpass-sourced keys are persisted under config['llm_keys'][provider].
    """
    p = PROVIDERS.get(provider)
    if p is None:
        raise RuntimeError(f"unknown LLM provider: {provider!r}")
    may_prompt = key is _UNSET
    if key is _UNSET:
        key = resolve_api_key(provider)
    base_url = p["base_url"] if base_url is None else base_url
    model = p["model"] if model is None else model
    if provider == "custom":
        if not base_url:
            base_url = _ask(
                "  Custom base URL (OpenAI-compatible, e.g. http://localhost:8000/v1): "
            )
        if not model:
            model = _ask("  Model name (e.g. llama3.1): ")
        if not base_url or not model:
            print(
                "commit-brief: a custom provider needs a base URL and a model name",
                file=sys.stderr,
            )
            raise SystemExit(2)
    if p["key_env"] and not key and may_prompt:
        key = _read_secret(provider, p["name"])
        if key:
            config = bootstrap._load_config()
            bootstrap._save_config(
                {**config, "llm_keys": {**config.get("llm_keys", {}), provider: key}}
            )
    return {"provider": provider, "key": key, "base_url": base_url, "model": model}


# --------------------------------------------------------------------------
# prompt construction — mirrors core.summarize exactly (same SYSTEM_PROMPT,
# same commit JSON, same word limits and bullets instructions)
# --------------------------------------------------------------------------


def build_summary_prompt(commits, bullets: bool) -> str:
    """The exact prompt core.summarize sends, with SYSTEM_PROMPT prefixed.

    core.build_prompt is reused so the two paths can never drift apart:
    commits serialized to JSON (hash/author/author_date/subject/body/files/
    shortstat), a single paragraph under 130 words or bullets grouped by
    author.
    """
    return f"{SYSTEM_PROMPT}\n\n{build_prompt(commits, bullets)}"


# --------------------------------------------------------------------------
# the ONE call path: OpenAI-compatible POST /chat/completions (stdlib urllib)
# --------------------------------------------------------------------------


def call_llm(provider: str, prompt: str, cfg: dict) -> str:
    """One OpenAI-compatible chat-completions call; returns the reply text.

    cfg comes from llm_setup(): {provider, key, base_url, model}. The
    Authorization header is only sent when a key is present (local
    endpoints like Ollama don't need one). Failures raise RuntimeError
    with a trimmed 'LLM call failed (<provider>): ...' message.
    """
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    model = (cfg.get("model") or "").strip()
    if not base:
        raise RuntimeError(f"LLM call failed ({provider}): no base URL configured")
    if not model:
        raise RuntimeError(f"LLM call failed ({provider}): no model configured")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,  # reasoning models burn budget before content
        "temperature": 0.3,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    key = (cfg.get("key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        raise RuntimeError(
            f"LLM call failed ({provider}): HTTP {exc.code} {_trim(detail)}"
        ) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(
            f"LLM call failed ({provider}): {_trim(str(reason))}"
        ) from None
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            f"LLM call failed ({provider}): {_trim(str(exc))}"
        ) from None
    try:
        payload = json.loads(raw.decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, IndexError, TypeError):
        raise RuntimeError(f"LLM call failed ({provider}): malformed response") from None
    if not isinstance(content, str):
        raise RuntimeError(
            f"LLM call failed ({provider}): malformed response "
            f"(content is {type(content).__name__})"
        ) from None
    return content.strip()


def summarize(
    commits,
    provider: str | None = "anthropic",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    bullets: bool = False,
    dry_run: bool = False,
) -> str:
    """Multi-provider standup summary — same contract as core.summarize.

    Empty commit list -> 'No commits in the window.'. dry_run returns the
    exact prompt without any network call. provider None/'none' raises
    RuntimeError. model/base_url/api_key override llm_setup's values when
    given; a provider that requires a key raises when none is available.
    """
    if not commits:
        return "No commits in the window."
    if not provider or provider == "none":
        raise RuntimeError(
            "no LLM provider selected — pick one with choose_provider(), "
            "set COMMIT_BRIEF_PROVIDER, or pass provider=..."
        )
    if provider not in PROVIDERS:
        raise RuntimeError(
            f"unknown LLM provider '{provider}' — known: {', '.join(PROVIDERS)}"
        )
    prompt = build_summary_prompt(commits, bullets)
    if dry_run:
        return prompt
    if api_key is None:
        api_key = resolve_api_key(provider)  # env/config only — never prompts
    cfg = llm_setup(provider, key=api_key, model=model, base_url=base_url)
    if not cfg.get("key") and PROVIDERS[provider]["key_env"]:
        p = PROVIDERS[provider]
        raise RuntimeError(
            f"{p['name']} API key is not set. Export {p['key_env']}, "
            "pass api_key=..., or use dry_run=True to preview without "
            "calling the API."
        )
    return call_llm(provider, prompt, cfg)
