"""antigravity-cli probe.

Per-session context usage is not recoverable -- antigravity-cli stores
conversations as protobuf blobs inside per-conversation SQLite files
(`~/.gemini/antigravity-cli/conversations/*.db`), not as readable JSON/JSONL.
What *is* readable:

- the currently configured model, from `~/.gemini/antigravity-cli/settings.json`
- recent activity by workspace, from `~/.gemini/antigravity-cli/history.jsonl`
- quota-window usage (5-hour / weekly rate limits), via the Omarchy agent-leaderboard
  collector already present on this machine, when it is present -- degrading
  silently to no quota when it is not, rather than shelling out to a script
  that may not exist on someone else's machine.

Quota is reported honestly as a *window*, never squeezed into `context_used`/
`context_limit`, which mean per-session token accounting -- a fact this
harness cannot supply.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, Optional

ANTIGRAVITY_DIR = os.path.expanduser("~/.gemini/antigravity-cli")
SETTINGS_PATH = os.path.join(ANTIGRAVITY_DIR, "settings.json")
HISTORY_PATH = os.path.join(ANTIGRAVITY_DIR, "history.jsonl")

OMARCHY_COLLECTOR = os.path.expanduser(
    "~/.config/omarchy/plugins/mustafaokur.agent-leaderboard/collect-antigravity.py"
)


def _current_model() -> str:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get("model") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def _has_recent_activity(cwd: str) -> bool:
    """Cheap check: does history.jsonl mention this workspace in its tail?"""
    if not cwd or not os.path.isfile(HISTORY_PATH):
        return False
    try:
        with open(HISTORY_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return False

    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("workspace") == cwd:
            return True
    return False


def _quota() -> Optional[Dict[str, Any]]:
    if not os.path.isfile(OMARCHY_COLLECTOR):
        return None
    try:
        proc = subprocess.run(
            ["python3", OMARCHY_COLLECTOR, "--print"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return None

    limits = data.get("limits")
    if not isinstance(limits, list) or not limits:
        return None

    # Surface the tightest (highest-percent-used) window -- that's the one an
    # operator actually needs to see coming.
    tightest = max(
        (lim for lim in limits if isinstance(lim, dict) and isinstance(lim.get("percent"), (int, float))),
        key=lambda lim: lim["percent"],
        default=None,
    )
    if not tightest:
        return None

    return {
        "label": tightest.get("title") or tightest.get("label") or "quota window",
        "percent": tightest["percent"],
        "resets_at": tightest.get("resetsAt"),
    }


def probe(cwd: str, pid: Optional[int] = None, harness: str = "") -> Dict[str, Any]:
    if harness and harness not in ("antigravity", "antigravity-cli", "agy"):
        return {}
    if not os.path.isdir(ANTIGRAVITY_DIR):
        return {}
    if not _has_recent_activity(cwd):
        return {}

    result: Dict[str, Any] = {
        "harness": "antigravity",
        "harness_version": "antigravity-cli",
    }

    model = _current_model()
    if model:
        result["model"] = model

    quota = _quota()
    if quota:
        result["quota"] = quota

    return result
