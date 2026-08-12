"""Thin shim — the smoke test lives in commit_brief.mcp_test.

Usage: uv run --extra mcp python scripts/test_mcp_client.py [path-to-git-repo]
Defaults to $CBR_TEST_REPO, then the current directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commit_brief.mcp_test import run_smoke_test  # noqa: E402

sys.exit(
    run_smoke_test(
        sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CBR_TEST_REPO", ".")
    )
)
