"""Git repo/branch probe -- local working tree only, no SSH remotes in v1."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional


def probe(cwd: str, pid: Optional[int] = None, harness: str = "") -> Dict[str, Any]:
    if not cwd or not os.path.isdir(cwd):
        return {}

    try:
        toplevel = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if toplevel.returncode != 0:
            return {}
        repo_path = toplevel.stdout.strip()
        repo_name = os.path.basename(repo_path.rstrip("/")) or repo_path

        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        branch_name = branch.stdout.strip() if branch.returncode == 0 else ""

        dirty = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True, text=True, timeout=2,
        )
        is_dirty = bool(dirty.stdout.strip()) if dirty.returncode == 0 else None

        return {
            "git_repo": repo_name,
            "git_branch": branch_name,
            "git_dirty": is_dirty,
        }
    except (subprocess.TimeoutExpired, OSError):
        return {}
