"""Commit-message hygiene: conventional-commits validator + git commit-msg hook.

`validate_message()` powers two paths:
  * `commit-brief hook check <msgfile>` — the primary hook path (wired in cli.py)
  * `python -m commit_brief.hooks --check <msgfile>` — module fallback

`install_hook()` / `uninstall_hook()` / `hook_installed()` manage
`.git/hooks/commit-msg`; the hook script delegates back to `commit-brief hook
check` and rejects non-conventional messages. Zero dependencies (stdlib only).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# conventional-commits validation
# --------------------------------------------------------------------------

TYPES = (
    "feat", "fix", "docs", "style", "refactor",
    "perf", "test", "build", "ci", "chore", "revert",
)

# 'type(scope)!: subject' — type case-insensitive, scope [\w.-]+, subject
# non-empty. No trailing-period enforcement.
_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[\w.-]+)\))?(?P<breaking>!)?: (?P<subject>.+)$"
)

# git-generated messages that bypass conventional-commit enforcement
# (prefixes lowercase; matched case-insensitively)
_PASSTHROUGH_PREFIXES = (
    ("merge ", "merge commit"),
    ("revert ", "revert commit"),
    ("fixup! ", "fixup! commit"),
    ("squash! ", "squash! commit"),
    ("amend! ", "amend! commit"),
)


def validate_message(msg: str) -> tuple[bool, str]:
    """Validate a commit message against conventional commits.

    Accepts 'type(scope)!: subject', 'type!: subject', 'type: subject' (type
    case-insensitive, scope [\\w.-]+), plus git-generated messages starting
    'Merge ', 'Revert ', 'fixup! ', 'squash! ' or 'amend! '. Only the first
    line is checked — bodies are free-form. Returns (ok, reason); reason is
    short and shown to the developer whose commit was rejected.
    """
    lines = msg.splitlines()
    first = lines[0].strip() if lines else ""
    if not first:
        return False, "empty commit message"
    lowered = first.lower()
    for prefix, label in _PASSTHROUGH_PREFIXES:
        if lowered.startswith(prefix):
            return True, label
    m = _CONVENTIONAL_RE.match(first)
    if m:
        ctype = m.group("type").lower()
        if ctype not in TYPES:
            return False, (
                f"unknown type '{ctype}' — expected one of: {', '.join(TYPES)}"
            )
        return True, "ok"
    if ":" not in first:
        return False, "missing ': ' — expected 'type(scope): subject'"
    head, _, subject = first.partition(":")
    if not subject.strip():
        return False, "empty subject — expected 'type(scope): subject'"
    return False, "malformed header — expected 'type(scope): subject'"


# --------------------------------------------------------------------------
# commit-msg hook management
# --------------------------------------------------------------------------

HOOK_MARKER = "installed by commit-brief"

_HOOK_SCRIPT = f"""\
#!/bin/sh
# {HOOK_MARKER} — conventional commit enforcement
if commit-brief hook --help >/dev/null 2>&1; then
  # primary path: the `hook check` subcommand
  commit-brief hook check "$1" || {{
    echo '' >&2
    echo 'commit-brief: commit rejected — see above' >&2
    exit 1
  }}
elif python -c "import commit_brief" >/dev/null 2>&1; then
  # transitional fallback for installs whose `hook` subcommand has not
  # landed yet: validate via the module CLI instead
  python -m commit_brief.hooks --check "$1" || {{
    echo '' >&2
    echo 'commit-brief: commit rejected — see above' >&2
    exit 1
  }}
else
  echo 'commit-brief: warning: validator unavailable — commit allowed' >&2
fi
"""


def install_hook(repo: str) -> str:
    """Install the commit-msg hook in `repo`; returns the hook file path.

    Creates .git/hooks when missing. The hook delegates to
    `commit-brief hook check` and rejects non-conventional messages.
    """
    hooks_dir = Path(repo) / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "commit-msg"
    hook_path.write_text(_HOOK_SCRIPT, encoding="utf-8", newline="\n")
    if os.name != "nt":  # POSIX: hooks must be executable (best-effort)
        try:
            hook_path.chmod(0o755)
        except OSError:
            pass
    return str(hook_path)


def uninstall_hook(repo: str) -> bool:
    """Remove the commit-msg hook if commit-brief installed it.

    Never deletes a hook it did not write (foreign hooks survive — the file
    must contain the HOOK_MARKER). Returns True when removed, False when the
    hook is absent or foreign.
    """
    hook_path = Path(repo) / ".git" / "hooks" / "commit-msg"
    if not hook_path.exists():
        return False
    try:
        content = hook_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if HOOK_MARKER not in content:
        return False
    try:
        hook_path.unlink()
    except OSError:
        return False
    return True


def hook_installed(repo: str) -> bool:
    """True when the repo has a commit-msg hook installed by commit-brief."""
    hook_path = Path(repo) / ".git" / "hooks" / "commit-msg"
    if not hook_path.exists():
        return False
    try:
        content = hook_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return HOOK_MARKER in content


# --------------------------------------------------------------------------
# module CLI: python -m commit_brief.hooks --check <msgfile>
# (fallback for the hook script while the `hook` subcommand is being wired)
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """`--check <msgfile>`: validate a commit message file.

    Prints the verdict to stderr; exits 0 on a conventional message, 1 on a
    rejected one (the exit code the hook script relies on), 2 on usage/IO
    errors.
    """
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 2 and argv[0] == "--check":
        msgfile = argv[1]
        try:
            msg = Path(msgfile).read_text(encoding="utf-8")
        except OSError as e:
            print(f"commit-brief: cannot read {msgfile}: {e}", file=sys.stderr)
            return 2
        ok, reason = validate_message(msg)
        print(f"commit-brief: {reason}", file=sys.stderr)
        return 0 if ok else 1
    print("usage: python -m commit_brief.hooks --check <msgfile>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
