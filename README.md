# commit-brief

[![CI](https://github.com/ahm3d-karim/commit-brief/actions/workflows/ci.yml/badge.svg)](https://github.com/ahm3d-karim/commit-brief/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/commit-brief.svg)](https://pypi.org/project/commit-brief/)
[![Python versions](https://img.shields.io/pypi/pyversions/commit-brief.svg)](https://pypi.org/project/commit-brief/)

Turn yesterday's git log into a one-paragraph standup summary.

`commit-brief` reads your git history (commit messages, authors, branch refs,
per-file change stats — never diffs) and asks an LLM for a terse standup
digest. One command, two interfaces:

- `commit-brief` — standup digest at your terminal.
- `commit-brief mcp` — MCP server, so your agents (Claude Code, Codex, ...)
  can call the same core as a tool.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv) (or pipx).

```bash
# PyPI — CLI only (works once published):
uv tool install commit-brief

# with the MCP server (needs the mcp extra):
uv tool install "commit-brief[mcp]"

# Pre-publish fallback — install straight from GitHub:
uv tool install git+https://github.com/ahm3d-karim/commit-brief

# with the MCP server from git:
uv tool install "git+https://github.com/ahm3d-karim/commit-brief[mcp]"

# from a local checkout instead:
uv tool install ".[mcp]"

# pipx equivalent (from PyPI once published, or the git URL until then):
pipx install "commit-brief[mcp]"
```

Installs one command: `commit-brief`. Then set your API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or add it to your shell profile
```

Model default: `claude-sonnet-4-20250514` (override with `COMMIT_BRIEF_MODEL`).

## CLI

```bash
# from inside any repo — yesterday's commits, one paragraph
commit-brief

# other repos / windows / filters
commit-brief --repo ../other-repo --since '3 days ago'
commit-brief --since '7 days ago' --author 'Alice'
commit-brief --author 'Alice' --author 'Bob' --bullets

# no API key? see exactly what the LLM would receive
commit-brief --dry-run

# raw structured commits (hash, author, subject, body, refs, per-file stats)
commit-brief --json
```

`--since`/`--until` accept anything git accepts: `yesterday`, `3 days ago`,
`2026-08-01`. `--author` is repeatable (OR). Exit codes: 0 ok / no commits,
2 git or API error.

## Incremental digests

Running `commit-brief` every day re-summarizes the same commits. `--since-last`
digests only what is new since your last successful run (tracked per repo in
`~/.commit-brief.json` — rebase/force-push safe: it falls back to the full
window if the saved commit disappears):

```bash
commit-brief --since-last          # only new commits since the last digest
commit-brief --since-last --bullets
```

Every successful digest advances the pointer, so plain `commit-brief` and
`--since-last` cooperate. `--dry-run` and `--json` never touch the pointer.

## Commit hygiene hook

The digest is only as good as the commit messages it reads. Enforce
[conventional commits](https://www.conventionalcommits.org) at the source:

```bash
commit-brief hook install          # write .git/hooks/commit-msg (this repo)
commit-brief hook status           # installed? (safe — foreign hooks never touched)
commit-brief hook check <file>     # validate a message file (exit 0/1)
commit-brief hook uninstall        # remove only if commit-brief installed it
```

Rejects `feat:`, `fix:`, `feat(api):`, `fix!:`, `docs:` … anything that is not
`type(scope)!: subject` (Merge/Revert/fixup!/squash!/amend! pass through), with
a friendly reason on stderr. Per-repo, opt-in — your call whether you want it.

## Interactive menu + GitHub mode

Bare `commit-brief` (no args) opens a menu:

- **Local** — this folder, or pick any git repo in the current tree; asks
  for the commit window (default `yesterday`) and authors (default all)
- **GitHub** — sign in (reuses your `gh` CLI login, or paste a token once),
  pick repos from your account — numbers, ranges, search, or any
  `owner/repo` — and get a per-repo digest

First run checks git/uv/python and offers consent-based installs, and asks
for an API key exactly once if none is found anywhere (it is saved to
`~/.commit-brief.json` as the default key). After that it never asks
again — keys are resolved silently from the provider's env var
(`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, …), the saved config, or Hermes'
`.env` (so keys you already use with Hermes just work). Missing keys fail
with a clean error instead of nagging; `--json` and `--dry-run` never need
one. Non-interactive: `commit-brief --github` for GitHub mode with the
usual flags.

## Multi-provider LLM (BYOK)

Any provider, any key, any model. Pick interactively in the menu, or pass flags:

```bash
commit-brief --provider deepseek --since '3 days ago'
commit-brief --provider openai --model gpt-4o-mini
commit-brief --provider groq --dry-run        # prompt only, no key needed
commit-brief --provider custom --base-url http://localhost:8000/v1 --model llama3.1
```

Providers (mirrors Hermes' supported list): anthropic, openai, openrouter,
gemini, xai, deepseek, groq, mistral, ollama (local, no key), huggingface,
zai (GLM), minimax, minimax_cn, kimi (moonshot), dashscope, xiaomi,
kilocode, opencode_zen, opencode_go, fireworks, novita, arcee, gmi,
tencent, nvidia, stepfun, custom (any OpenAI-compatible endpoint).
`--model` accepts any model identifier the provider supports; `custom`
always asks for its base URL and model name, and never inherits another
provider's default. Keys resolve silently, in order: provider env var →
`~/.commit-brief.json` (`llm_keys`) → Hermes' `.env` → the saved default
key. You are asked for a key only on the very first startup, and only when
no key exists anywhere — never again.

## MCP server

The server exposes the same core as two tools: `summarize_standup` and
`list_commits` (params: `repo`, `since`, `until`, `author`, `bullets`,
`dry_run` — `dry_run` returns the prompt at zero API cost).

Run it (needs the `[mcp]` extra installed):

```bash
commit-brief mcp
# from a source checkout: uv run --extra mcp python -m commit_brief.mcp_server
```

Register with Claude Code:

```bash
claude mcp add commit-brief -- commit-brief mcp
```

Then just ask: *"summarize yesterday's commits in this repo"* or
*"what did each dev ship last week?"* — the agent calls the tool with the
right args.

Self-test the server end to end (spawns it, lists tools, exercises both
tools against a repo — pass a path or set `CBR_TEST_REPO`; defaults to the
current directory):

```bash
commit-brief mcp-test
commit-brief mcp-test /path/to/repo
```

## Design notes

- **Messages + stats, never diffs.** The LLM sees subjects, bodies, authors,
  branch refs, and per-file `(+a -d)` counts. Cheap, and code never leaves
  the repo.
- **Git subprocess hygiene:** git is spawned with `stdin=DEVNULL` and
  `GIT_PAGER=cat`. Without this, a git spawned inside an MCP stdio server on
  Windows/MSYS2 gets SIGTERM'd (rc 143) because it inherits the protocol pipe
  / console state. This is the fix — don't remove it.
- **`mcp` is pinned `<2`** because 2.x replaced FastMCP with a new API; the
  stdio protocol is identical, so 1.x keeps the server code simple.

## Testing

```bash
uv run --extra test pytest       # core + CLI suite (unit tests, no API needed)
commit-brief mcp-test            # end-to-end MCP smoke test (spawns the server)
```

CI runs the pytest suite on every push (GitHub Actions, `.github/workflows/ci.yml`).
The MCP smoke test is intentionally excluded from pytest — it spawns servers
and exits the process — so it stays a self-serve command.

## Project layout

```
commit_brief/core.py       git log parsing + prompt building + LLM call
commit_brief/cli.py        one command: standup (default) + mcp / mcp-test
commit_brief/mcp_server.py FastMCP wrapper (2 tools)
commit_brief/mcp_test.py   end-to-end MCP smoke test (used by mcp-test)
scripts/test_mcp_client.py thin shim over commit_brief.mcp_test
```
