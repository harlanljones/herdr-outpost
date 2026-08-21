"""Claude Code session probe.

Reads the freshest JSONL transcript under `~/.claude/projects/<slug(cwd)>/` and
pulls the most recent assistant message's model + token usage. Never invents
a context figure: if no transcript matches this cwd, returns {}.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Known context windows by model family. Unrecognized models fall back to the
# 200k default rather than a guess pulled from nowhere else in the codebase.
CONTEXT_WINDOWS = {
    "opus": 200_000,
    "sonnet": 200_000,
    "haiku": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000

# Only trust a transcript that was written to recently -- an old session left
# open in a closed pane must not masquerade as this agent's live state.
FRESHNESS_SECONDS = 6 * 60 * 60


def _slugify_cwd(cwd: str) -> str:
    # Claude Code's own project-directory convention: every path separator
    # becomes a hyphen, so /home/harlan/dev/x -> -home-harlan-dev-x
    return cwd.replace("/", "-")


def _context_window_for(model: str) -> int:
    lower = (model or "").lower()
    for key, window in CONTEXT_WINDOWS.items():
        if key in lower:
            return window
    return DEFAULT_CONTEXT_WINDOW


def _recent_transcripts(project_dir: str) -> list:
    try:
        candidates = [
            os.path.join(project_dir, f)
            for f in os.listdir(project_dir)
            if f.endswith(".jsonl")
        ]
    except OSError:
        return []
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates


def _process_start_time(pid: Optional[int]) -> Optional[float]:
    """Wall-clock start time of `pid`, via /proc, for disambiguating same-cwd sessions."""
    if not pid:
        return None
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            fields = f.read().split()
        # field 22 (1-indexed) is starttime in clock ticks since boot
        starttime_ticks = int(fields[21])
        clk_tck = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.read().split()[0])
        boot_time = time.time() - uptime_seconds
        return boot_time + (starttime_ticks / clk_tck)
    except (OSError, ValueError, IndexError):
        return None


def _select_transcript(project_dir: str, pid: Optional[int]) -> Optional[str]:
    """Pick the transcript belonging to this pane. When one repo hosts more than
    one live Claude Code session (two panes, same cwd), the newest-mtime file is
    ambiguous -- disambiguate by matching each transcript's creation time against
    this pane's process start time, nearest wins. A reporter (exact pane<->session
    mapping) supersedes this heuristic entirely once installed."""
    candidates = _recent_transcripts(project_dir)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    start = _process_start_time(pid)
    if start is None:
        return candidates[0]

    def distance(path: str) -> float:
        try:
            return abs(os.path.getctime(path) - start)
        except OSError:
            return float("inf")

    return min(candidates, key=distance)


def _last_assistant_usage(transcript_path: str) -> Optional[Dict[str, Any]]:
    """Scan from the end of the file for the last assistant message with usage."""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 8192
            data = b""
            pos = size
            lines: list = []
            # Read backwards in chunks until we have a handful of parsed
            # candidate lines or hit the start of the file.
            while pos > 0 and len(lines) < 40:
                read_size = min(block, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + data
                lines = data.split(b"\n")
    except OSError:
        return None

    for raw_line in reversed(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        model = message.get("model")
        if isinstance(usage, dict) and model:
            return {"model": model, "usage": usage}
    return None


def probe(cwd: str, pid: Optional[int] = None, harness: str = "") -> Dict[str, Any]:
    if harness and harness != "claude":
        return {}
    if not cwd or not os.path.isdir(CLAUDE_PROJECTS_DIR):
        return {}

    project_dir = os.path.join(CLAUDE_PROJECTS_DIR, _slugify_cwd(cwd))
    if not os.path.isdir(project_dir):
        return {}

    transcript = _select_transcript(project_dir, pid)
    if not transcript:
        return {}

    if time.time() - os.path.getmtime(transcript) > FRESHNESS_SECONDS:
        return {}

    found = _last_assistant_usage(transcript)
    if not found:
        return {}

    model = found["model"]
    usage = found["usage"]
    context_used = (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )

    return {
        "harness": "claude",
        "harness_version": "Claude Code",
        "model": model,
        "context_used": context_used,
        "context_limit": _context_window_for(model),
    }
