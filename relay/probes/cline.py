"""cline session probe.

Scans `~/.cline/data/sessions/*/` for the session file whose `pid` matches this
pane's foreground process (exact identity, via `herdr pane process-info`),
falling back to matching by `cwd`/`workspace_root` when the pid lookup is
unavailable. Reads model, usage, git identity, and cost straight out of
cline's own session JSON -- see `<session>.json`'s
`metadata.usage{inputTokens,outputTokens,cacheReadTokens}`,
`metadata.git{url,branch}`, and `metadata.totalCost`.
"""

from __future__ import annotations

import glob
import json
import os
import time
from typing import Any, Dict, Optional

CLINE_SESSIONS_DIR = os.path.expanduser("~/.cline/data/sessions")
FRESHNESS_SECONDS = 6 * 60 * 60

# cline reports usage per its own context accounting; without a published
# per-model window table here, only expose used/limit when cline's provider
# metadata makes the limit unambiguous. Until then, report usage without a
# limit rather than guessing one.
DEFAULT_CONTEXT_WINDOW = None


def _session_files() -> list:
    pattern = os.path.join(CLINE_SESSIONS_DIR, "*", "*.json")
    return [p for p in glob.glob(pattern) if not p.endswith(".messages.json")]


def _load_session(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def probe(cwd: str, pid: Optional[int] = None, harness: str = "") -> Dict[str, Any]:
    if harness and harness != "cline":
        return {}
    if not os.path.isdir(CLINE_SESSIONS_DIR):
        return {}

    best_match: Optional[Dict[str, Any]] = None
    best_by_cwd: Optional[Dict[str, Any]] = None

    for path in _session_files():
        try:
            if time.time() - os.path.getmtime(path) > FRESHNESS_SECONDS:
                continue
        except OSError:
            continue

        data = _load_session(path)
        if not data:
            continue

        session_pid = data.get("pid")
        session_cwd = data.get("cwd") or data.get("workspace_root")

        try:
            if pid and session_pid and int(session_pid) == int(pid):
                best_match = data
                break  # exact pid match wins outright
        except (TypeError, ValueError):
            pass
        if cwd and session_cwd and os.path.normpath(session_cwd) == os.path.normpath(cwd):
            # keep the most recently modified cwd match if several exist
            try:
                is_newer = best_by_cwd is None or os.path.getmtime(path) > os.path.getmtime(
                    _session_path_for(best_by_cwd)
                )
            except OSError:
                is_newer = best_by_cwd is None
            if is_newer:
                best_by_cwd = data

    session = best_match or best_by_cwd
    if not session:
        return {}

    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
    git_info = metadata.get("git") if isinstance(metadata.get("git"), dict) else {}

    result: Dict[str, Any] = {
        "harness": "cline",
        "harness_version": "cline",
        "model": session.get("model") or "",
    }

    input_tokens = usage.get("inputTokens")
    cache_read = usage.get("cacheReadTokens")
    if input_tokens is not None or cache_read is not None:
        result["context_used"] = (input_tokens or 0) + (cache_read or 0)

    total_cost = metadata.get("totalCost")
    if isinstance(total_cost, (int, float)):
        result["cost_usd"] = total_cost

    branch = git_info.get("branch")
    if branch:
        result["git_branch"] = branch
    repo_url = git_info.get("url")
    if repo_url:
        result["git_repo"] = os.path.splitext(os.path.basename(repo_url.rstrip("/")))[0]

    task_title = metadata.get("title")
    if task_title:
        result["task_title"] = task_title

    return result


def _session_path_for(data: Dict[str, Any]) -> str:
    # helper only used to compare mtimes of already-loaded dicts; cline session
    # JSON doesn't self-report its own path, so this reconstructs it from the
    # session_id it does carry.
    session_id = data.get("session_id", "")
    return os.path.join(CLINE_SESSIONS_DIR, session_id, f"{session_id}.json")
