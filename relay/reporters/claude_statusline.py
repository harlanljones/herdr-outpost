#!/usr/bin/env python3
"""Claude Code statusLine reporter for herdr-outpost.

Install as the `statusLine.command` in Claude Code's settings.json:

    {
      "statusLine": {
        "type": "command",
        "command": "/path/to/herdr-outpost/relay/reporters/claude_statusline.py"
      }
    }

Claude Code re-invokes this on every turn, piping a JSON object on stdin
(model, workspace, session/transcript path, cumulative cost) and expecting a
plain status-line string on stdout. This script does both jobs: it prints a
normal status line so nothing in Claude Code's UI regresses, AND -- as a
side effect -- posts the same turn's model/context/cost/git identity to the
herdr-outpost relay over the *same* `/event` ingress the herdr push plugin
already uses, tagged with `$HERDR_PANE_ID` so the relay can map it to the
exact pane, no cwd/pid guessing required.

Never fails the status line: any relay error is swallowed. A missing
HERDR_PANE_ID (this pane isn't running under herdr) makes the report a no-op.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

RELAY_HTTP_BASE = (
    os.environ.get("HERDR_OUTPOST_RELAY_HTTP")
    or os.environ.get("HERDR_RELAY_HTTP")
    or "http://127.0.0.1:8375"
)
RELAY_TOKEN = os.environ.get("HERDR_OUTPOST_RELAY_TOKEN") or os.environ.get("HERDR_RELAY_TOKEN") or ""


def _git_identity(cwd: str) -> dict:
    try:
        repo = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if repo.returncode != 0:
            return {}
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return {
            "git_repo": os.path.basename(repo.stdout.strip().rstrip("/")),
            "git_branch": branch.stdout.strip() if branch.returncode == 0 else "",
        }
    except (subprocess.TimeoutExpired, OSError):
        return {}


def _last_usage(transcript_path: str) -> dict:
    if not transcript_path or not os.path.isfile(transcript_path):
        return {}
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-20:]
    except OSError:
        return {}

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if isinstance(message, dict) and message.get("role") == "assistant":
            usage = message.get("usage")
            if isinstance(usage, dict):
                return {
                    "context_used": (
                        (usage.get("input_tokens") or 0)
                        + (usage.get("cache_creation_input_tokens") or 0)
                        + (usage.get("cache_read_input_tokens") or 0)
                    )
                }
    return {}


def report(payload: dict) -> None:
    pane_id = os.environ.get("HERDR_PANE_ID")
    if not pane_id:
        return

    model = (payload.get("model") or {}).get("display_name") or (payload.get("model") or {}).get("id") or ""
    workspace = payload.get("workspace") or {}
    cwd = workspace.get("current_dir") or payload.get("cwd") or ""
    cost = payload.get("cost") or {}
    transcript_path = payload.get("transcript_path") or ""

    event = {
        "pane_id": pane_id,
        "harness": "claude",
        "harness_version": "Claude Code",
        "model": model,
        "cwd": cwd,
        "cost_usd": cost.get("total_cost_usd"),
        **_git_identity(cwd),
        **_last_usage(transcript_path),
    }
    event = {k: v for k, v in event.items() if v not in (None, "")}

    body = json.dumps({"payload": event}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if RELAY_TOKEN:
        headers["Authorization"] = f"Bearer {RELAY_TOKEN}"

    req = urllib.request.Request(f"{RELAY_HTTP_BASE.rstrip('/')}/event", data=body, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=1.5)
    except (urllib.error.URLError, OSError, ValueError):
        pass  # relay unreachable -- the status line must still print


def status_line_text(payload: dict) -> str:
    model = (payload.get("model") or {}).get("display_name") or "Claude"
    cost = (payload.get("cost") or {}).get("total_cost_usd")
    parts = [model]
    if isinstance(cost, (int, float)):
        parts.append(f"${cost:.2f}")
    return " · ".join(parts)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    try:
        report(payload)
    except Exception:
        pass  # the status line must never fail because reporting did

    print(status_line_text(payload))


if __name__ == "__main__":
    main()
