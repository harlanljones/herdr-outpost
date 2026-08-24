"""OpenCode root-session identity probe.

Reads the pane's own main session row from OpenCode's SQLite store
(~/.local/share/opencode/opencode.db, table ``session``) to fill in what
`herdr agent list` does not report for opencode panes: model and harness
version. The root session id comes from herdr's ``agent_session.value``
(exact identity, no guessing).

OpenCode stores ``session.model`` as a JSON blob like
``{"id":"...","providerID":"opencode","variant":"..."}``; ``_clean_model``
surfaces the bare model id. Returns {} whenever nothing can be measured;
never raises into the poll loop.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, Optional

OPENCODE_DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")

_SQLITE_TIMEOUT_SECONDS = 1.0


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


def probe(
    cwd: str = "",
    pid: Optional[int] = None,
    harness: str = "",
    agent_session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Partial enrichment dict: model (+harness_version) for the pane's root session."""
    if (harness or "").strip().lower() != "opencode":
        return {}
    root_id = ""
    if isinstance(agent_session, dict):
        root_id = str(agent_session.get("value") or "")
    if not root_id or not os.path.isfile(OPENCODE_DB_PATH):
        return {}

    try:
        uri = f"file:{OPENCODE_DB_PATH}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=_SQLITE_TIMEOUT_SECONDS)
        try:
            row = con.execute(
                "SELECT model, version FROM session WHERE id = :root",
                {"root": root_id},
            ).fetchone()
        finally:
            con.close()
    except Exception:
        # Locked/corrupt store degrades to "unknown" rather than breaking
        # the poll loop (probes/__init__.py re-guards too).
        return {}

    if not row:
        return {}

    found: Dict[str, Any] = {}
    model = _clean_model(row[0])
    if model:
        found["model"] = model
    version = str(row[1] or "").strip()
    if version:
        found["harness_version"] = version
    return found
