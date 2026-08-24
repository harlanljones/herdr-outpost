"""Subagent tree extraction per harness.

Derives the parent->child subagent sessions rooted at a pane's main harness
session, read-only from each harness's own on-disk state:

- opencode: SQLite store (~/.local/share/opencode/opencode.db), table
  ``session`` with a real ``parent_id`` column. The pane's root session id
  comes from herdr's ``agent_session.value`` (exact identity, no guessing).
- claude: transcript layout ~/.claude/projects/<slug>/<sessionId>/subagents/
  agent-*.jsonl; the root dir is disambiguated by process start time the same
  way probes.claude_code picks transcripts.
- cline / antigravity: no derivable parent links -> {} (they render as
  childless roots). Never invent a relationship that is not on disk.

Honesty rules: ``active`` is a recency signal (recent disk writes), NOT a
lifecycle status -- the client must label it as activity, not agent state.
Returns {} whenever nothing can be measured; never raises into the poll loop.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

from . import claude_code as claude_code_probe

OPENCODE_DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")

# A subagent counts as active when its session data was written more recently
# than this. Deliberately generous: quiet-but-running agents should flip to
# quiet rather than lie about being alive.
ACTIVE_WINDOW_SECONDS = 120.0

# Depth cap on parent->child traversal so a runaway nested-Task chain can
# neither produce an unbounded tree nor an unbounded recursive CTE.
MAX_DEPTH = 3

# Task titles are clamped before they ever reach the wire.
TITLE_CLAMP_CHARS = 90

# opencode names subagent sessions like "Explore TUI structure (@explore
# subagent)"; the @kind prefix is the only machine-readable kind signal.
_OPENCODE_KIND_RE = re.compile(r"@([\w][\w-]*)\s+subagent", re.IGNORECASE)

_SQLITE_TIMEOUT_SECONDS = 1.0


def _clamp_title(text: Any) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= TITLE_CLAMP_CHARS:
        return cleaned
    return cleaned[: TITLE_CLAMP_CHARS - 1].rstrip() + "\u2026"


def _iso_from_epoch(epoch_seconds: Optional[float]) -> str:
    if not epoch_seconds:
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            float(epoch_seconds), tz=datetime.timezone.utc
        ).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _is_active(updated_epoch_seconds: Optional[float], now_epoch: float) -> bool:
    if not updated_epoch_seconds:
        return False
    return (now_epoch - float(updated_epoch_seconds)) <= ACTIVE_WINDOW_SECONDS


def _node(
    subagent_id: str,
    title: str,
    kind: str,
    model: str,
    tokens: Optional[int],
    updated_epoch_seconds: Optional[float],
    now_epoch: float,
) -> Dict[str, Any]:
    return {
        "id": str(subagent_id),
        "title": _clamp_title(title),
        "kind": str(kind or ""),
        "model": str(model or ""),
        # None where the harness exposes no honest token figure.
        "tokens": int(tokens) if isinstance(tokens, (int, float)) else None,
        "updated_at": _iso_from_epoch(updated_epoch_seconds),
        "active": _is_active(updated_epoch_seconds, now_epoch),
        "children": [],
    }


def _normalize_ms(epoch_ms: Any) -> Optional[float]:
    """opencode stores epoch milliseconds; tolerate second-precision just in case."""
    try:
        value = float(epoch_ms)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value < 10_000_000_000:  # seconds-scale timestamp
        return value
    return value / 1000.0


def _clean_model(raw_model: Any) -> str:
    """opencode's session.model is a JSON blob like
    {"id":"...","providerID":"opencode","variant":"..."}; surface the bare id."""
    text = str(raw_model or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return str(parsed.get("id") or "")
        except json.JSONDecodeError:
            return ""
    return text


def _opencode_subagents(root_session_id: str, now_epoch: float) -> List[Dict[str, Any]]:
    """Children of `root_session_id` via one bounded recursive CTE."""
    if not os.path.isfile(OPENCODE_DB_PATH):
        return []
    uri = f"file:{OPENCODE_DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=_SQLITE_TIMEOUT_SECONDS)
    try:
        rows = con.execute(
            """
            WITH RECURSIVE tree AS (
                SELECT id, parent_id, title, agent, model,
                       tokens_input, tokens_output, time_updated, 0 AS depth
                FROM session WHERE id = :root
                UNION ALL
                SELECT s.id, s.parent_id, s.title, s.agent, s.model,
                       s.tokens_input, s.tokens_output, s.time_updated, t.depth + 1
                FROM session s JOIN tree t ON s.parent_id = t.id
                WHERE t.depth < :max_depth
            )
            SELECT id, parent_id, title, agent, model,
                   COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0),
                   time_updated
            FROM tree
            WHERE id != :root
            """,
            {"root": str(root_session_id), "max_depth": MAX_DEPTH},
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return []

    nodes: Dict[str, Dict[str, Any]] = {}
    parents: Dict[str, str] = {}
    for sid, parent_id, title, _agent, model, tokens, updated_ms in rows:
        kind_match = _OPENCODE_KIND_RE.search(str(title or ""))
        nodes[str(sid)] = _node(
            sid,
            title,
            kind_match.group(1) if kind_match else "",
            _clean_model(model),
            tokens,
            _normalize_ms(updated_ms),
            now_epoch,
        )
        parents[str(sid)] = str(parent_id)

    roots: List[Dict[str, Any]] = []
    for sid, node in nodes.items():
        parent_id = parents[sid]
        parent_node = nodes.get(parent_id)
        if parent_node is not None:
            parent_node["children"].append(node)
        else:
            roots.append(node)

    def sort_branch(branch: List[Dict[str, Any]]) -> None:
        branch.sort(key=lambda n: n.get("updated_at") or "", reverse=True)
        for item in branch:
            sort_branch(item["children"])

    sort_branch(roots)
    return roots


def _candidate_root_dirs(project_dir: str) -> List[str]:
    try:
        entries = os.listdir(project_dir)
    except OSError:
        return []
    candidates = []
    for name in entries:
        subagents_dir = os.path.join(project_dir, name, "subagents")
        if os.path.isdir(subagents_dir):
            candidates.append(os.path.join(project_dir, name))
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates


def _select_root_dir(project_dir: str, pid: Optional[int]) -> Optional[str]:
    """Newest subagent-bearing session dir wins; ties disambiguated against
    the pane process start time (mirrors probes.claude_code's transcript pick)."""
    candidates = _candidate_root_dirs(project_dir)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    start = claude_code_probe._process_start_time(pid)
    if start is None:
        return candidates[0]

    def distance(path: str) -> float:
        try:
            return abs(os.path.getmtime(path) - start)
        except OSError:
            return float("inf")

    return min(candidates, key=distance)


def _claude_title(transcript_path: str) -> str:
    """First user-side text in the sidechain transcript is the task prompt."""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "user":
                    continue
                message = entry.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return str(message.get("content"))
    except OSError:
        pass
    return ""


def _claude_subagents(
    root_dir: str, now_epoch: float
) -> List[Dict[str, Any]]:
    subagents_dir = os.path.join(root_dir, "subagents")
    try:
        files = [
            os.path.join(subagents_dir, f)
            for f in os.listdir(subagents_dir)
            if f.endswith(".jsonl")
        ]
    except OSError:
        return []

    nodes: List[Dict[str, Any]] = []
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        subagent_id = stem[6:] if stem.startswith("agent-") else stem
        try:
            updated = os.path.getmtime(path)
        except OSError:
            updated = None
        nodes.append(
            _node(subagent_id, _claude_title(path), "", "", None, updated, now_epoch)
        )
    nodes.sort(key=lambda n: n.get("updated_at") or "", reverse=True)
    return nodes


def probe(
    cwd: str = "",
    pid: Optional[int] = None,
    harness: str = "",
    agent_session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Partial enrichment dict: session_id + subagents for the pane's root session."""
    now_epoch = time.time()
    harness = (harness or "").strip().lower()

    try:
        if harness == "opencode":
            root_id = ""
            if isinstance(agent_session, dict):
                root_id = str(agent_session.get("value") or "")
            if not root_id:
                return {}
            subagents = _opencode_subagents(root_id, now_epoch)
            return {"session_id": root_id, "subagents": subagents}

        if harness == "claude":
            if not cwd:
                return {}
            project_dir = os.path.join(
                claude_code_probe.CLAUDE_PROJECTS_DIR,
                claude_code_probe._slugify_cwd(cwd),
            )
            if not os.path.isdir(project_dir):
                return {}
            root_dir = _select_root_dir(project_dir, pid)
            if not root_dir:
                return {}
            found: Dict[str, Any] = {
                "session_id": os.path.basename(root_dir),
            }
            subagents = _claude_subagents(root_dir, now_epoch)
            if subagents:
                found["subagents"] = subagents
            return found
    except Exception:
        # Locked/corrupt stores or unreadable transcripts degrade to "unknown"
        # rather than breaking the poll loop (probes/__init__.py re-guards too).
        return {}

    return {}
