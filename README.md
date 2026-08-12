# commit-brief

Turn yesterday's git log into a one-paragraph standup summary.

`commit-brief` reads your git history (commit messages, authors, branch refs,
per-file change stats — never diffs) and asks an LLM for a terse standup
digest. Two interfaces, one core:

- **CLI** — you at a terminal, Monday morning.
- **MCP server** — your agents (Claude Code, Codex, ...) call it as a tool.

## Setup

```bash
cd ~/Documents/fde/commit-brief
uv sync                      # installs deps incl. the CLI entry point
export ANTHROPIC_API_KEY=sk-ant-...   # or set it in your shell profile
```

Model default: `claude-sonnet-4-20250514` (override with `COMMIT_BRIEF_MODEL`).

## CLI

```bash
# from inside any repo — yesterday's commits, one paragraph
commit-brief

# other repos / windows / filters
commit-brief --repo ../lrs-platform --since '3 days ago'
commit-brief --since '7 days ago' --author 'Muhammad Ammar Faisal'
commit-brief --author ahm3dkarim --author ammmar04 --bullets

# no API key? see exactly what the LLM would receive
commit-brief --dry-run

# raw structured commits (hash, author, subject, body, refs, per-file stats)
commit-brief --json
```

`--since`/`--until` accept anything git accepts: `yesterday`, `3 days ago`,
`2026-08-01`. `--author` is repeatable (OR). Exit codes: 0 ok / no commits,
2 git or API error.

## MCP server

The server exposes the same core as two tools: `summarize_standup` and
`list_commits` (params: `repo`, `since`, `until`, `author`, `bullets`,
`dry_run` — `dry_run` returns the prompt at zero API cost).

Run it standalone:

```bash
uv run --extra mcp python -m commit_brief.mcp_server
```

Register with Claude Code (from any repo, or `--scope project` inside the
repo you want it in):

```bash
claude mcp add commit-brief -- uv run --project "C:/Users/Ahmad Karim/Documents/fde/commit-brief" --extra mcp python -m commit_brief.mcp_server
```

Then just ask: *"summarize yesterday's commits in the LRS repo"* or
*"what did ammmar04 ship last week?"* — the agent calls the tool with the
right args.

Smoke-test the server end to end (spawns it over stdio, lists tools, calls
each tool against the LRS repo):

```bash
uv run --extra mcp python scripts/test_mcp_client.py
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

## Project layout

```
commit_brief/core.py       git log parsing + prompt building + LLM call
commit_brief/cli.py        argparse CLI
commit_brief/mcp_server.py FastMCP wrapper (2 tools)
scripts/test_mcp_client.py end-to-end MCP smoke test
```
