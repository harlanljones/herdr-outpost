"""Agent state management and normalization for herdr-outpost relay."""

from __future__ import annotations

import datetime
import socket
from typing import Any, Dict, List, Optional, Set, Tuple, Union

VALID_STATUSES = {"blocked", "working", "done", "idle", "unknown"}
VALID_BLOCK_KINDS = {"permission", "question"}

# --- Session lifecycle defaults -------------------------------------------------
# A polled agent must be absent from `herdr agent list` for RECONCILE_GRACE
# consecutive successful polls before the relay considers it closed.
RECONCILE_GRACE = 2
# Hook/UDP-only agents (never present in an authoritative poll) expire once
# their last observation is older than this.
DEFAULT_SESSION_TTL_SECONDS = 90.0

STATUS_MAP = {
    "blocked": "blocked",
    "waiting": "blocked",
    "prompt": "blocked",
    "prompting": "blocked",
    "approval": "blocked",
    "needs_approval": "blocked",
    "confirm": "blocked",
    "working": "working",
    "running": "working",
    "busy": "working",
    "executing": "working",
    "thinking": "working",
    "done": "done",
    "finished": "done",
    "completed": "done",
    "success": "done",
    "idle": "idle",
    "ready": "idle",
    "waiting_for_input": "blocked",
    "paused": "idle",
    "stopped": "idle",
    "unknown": "unknown",
}


def normalize_status(raw_status: Optional[str]) -> str:
    """Normalize any raw agent status string to standard herdr-outpost status."""
    if not raw_status:
        return "unknown"
    cleaned = str(raw_status).strip().lower().replace("-", "_").replace(" ", "_")
    return STATUS_MAP.get(cleaned, "unknown")


# Heuristic tokens for classifying blocked episodes. Permission wins on ties
# so phone approve/reject stays the default when signals conflict.
_PERMISSION_HINTS = (
    "approval",
    "approve",
    "permission",
    "allow this",
    "allow the",
    "do you want",
    "y/n",
    "yes/no",
    "tool call",
    "run command",
    "bash",
    "write file",
    "edit file",
    "needs approval",
    "awaiting approval",
    "user confirmation",
)
_QUESTION_HINTS = (
    "askuserquestion",
    "ask user",
    "user question",
    "choose an option",
    "select one",
    "select an option",
    "which approach",
    "which option",
    "pick a",
    "waiting for user input",
    "waiting for input",
    "arrow keys",
    "use arrow",
    "❯",
)


def infer_block_kind(
    status_reason: Optional[str] = None,
    last_message: Optional[str] = None,
    screen: Optional[str] = None,
) -> str:
    """Classify a blocked episode as permission vs TUI question.

    Ambiguous or empty signals default to ``permission`` so existing
    Approve/Reject phone flows remain the safe default. Explicit question
    cues (AskUserQuestion, numbered menus, arrow-key prompts) flip to
    ``question``.
    """
    blob = " ".join(
        str(part or "") for part in (status_reason, last_message, screen)
    ).lower()
    if not blob.strip():
        return "permission"

    permission_hit = any(hint in blob for hint in _PERMISSION_HINTS)
    question_hit = any(hint in blob for hint in _QUESTION_HINTS)

    # Numbered TUI menus: "1. ..." or "❯ 1." / "(1)" style option lists.
    if not question_hit and (
        "❯" in blob
        or "\n1." in blob
        or blob.lstrip().startswith("1.")
        or " 1. " in blob
    ):
        question_hit = True

    if question_hit and not permission_hit:
        return "question"
    return "permission"


def normalize_block_kind(raw_kind: Optional[str]) -> str:
    """Normalize an explicit block_kind override; empty if unset/invalid."""
    if not raw_kind:
        return ""
    cleaned = str(raw_kind).strip().lower()
    return cleaned if cleaned in VALID_BLOCK_KINDS else ""


def get_default_hostname(local_hostname: Optional[str] = None) -> str:
    """Get the canonical local hostname."""
    if local_hostname:
        return str(local_hostname).strip()
    try:
        return socket.gethostname().split(".")[0] or "local"
    except Exception:
        return "local"


def build_agent_id(host: str, workspace: Optional[str], pane_id: Union[str, int]) -> str:
    """Create a deterministic unique key for an agent."""
    h = (host or "local").strip()
    ws = (workspace or "default").strip()
    p = str(pane_id).strip()
    return f"{h}:{ws}:{p}"


def parse_agent_id(agent_id: str) -> tuple[str, str, str]:
    """Parse an agent_id into (host, workspace, pane_id)."""
    parts = str(agent_id).split(":")
    if len(parts) == 1:
        return "local", "default", parts[0]
    if len(parts) == 2:
        return parts[0], "default", parts[1]
    return parts[0], parts[1], ":".join(parts[2:])


def normalize_agent_dict(raw: Dict[str, Any], local_hostname: Optional[str] = None) -> Dict[str, Any]:
    """Normalize arbitrary event or agent dictionary into a canonical agent representation."""
    host = raw.get("host") or raw.get("hostname") or get_default_hostname(local_hostname)
    workspace = raw.get("workspace") or raw.get("workspace_name") or "default"
    pane_id = raw.get("pane_id") or raw.get("paneId") or raw.get("pane") or raw.get("id") or "0"

    # If raw id was composite (host:workspace:pane_id), unpack it
    if ":" in str(pane_id) and not raw.get("host") and not raw.get("workspace"):
        host, workspace, pane_id = parse_agent_id(str(pane_id))

    agent_id = build_agent_id(host, workspace, pane_id)

    agent_obj = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
    # herdr's own `agent list`/`agent get` envelope uses the bare key "agent" for the
    # harness label (e.g. "claude", "cline") on a *string*, not a nested dict -- only
    # treat it as metadata when it actually is one.
    raw_agent_field = raw.get("agent")
    raw_status = (
        raw.get("status")
        or raw.get("agent_status")
        or raw.get("state")
        or agent_obj.get("status")
    )
    status = normalize_status(raw_status)

    # Detection health: whether herdr has a lifecycle session registered for
    # this pane (`agent_session` in polled payloads). Absent on hook/UDP
    # events -> None (unknown); never clobbers a previously observed value.
    # Falls back to an already-derived field so apply_agent_message's
    # re-normalization of merged agents preserves prior evidence.
    if "agent_session" in raw:
        session_registered: Optional[bool] = bool(raw.get("agent_session"))
    else:
        derived = raw.get("agent_session_registered")
        session_registered = None if derived is None else bool(derived)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    updated_at = raw.get("updated_at") or raw.get("timestamp") or raw.get("ts") or now_iso

    quota = raw.get("quota") if isinstance(raw.get("quota"), dict) else None

    status_reason = str(raw.get("status_reason") or raw.get("reason") or raw.get("message") or "")
    last_message = str(raw.get("last_message") or raw.get("prompt") or "")
    last_output = str(raw.get("last_output") or raw.get("output") or "")

    explicit_kind = normalize_block_kind(raw.get("block_kind"))
    if status == "blocked":
        block_kind = explicit_kind or infer_block_kind(status_reason, last_message, last_output)
    else:
        block_kind = ""

    return {
        "id": agent_id,
        "host": str(host),
        "workspace": str(workspace),
        "tab": raw.get("tab") or raw.get("tab_name") or raw.get("tab_id") or "",
        "pane_id": str(pane_id),
        "status": status,
        # Alarm dampening: set by the relay once a poll-sourced "blocked"
        # report has persisted long enough to be trusted (see
        # HerdrRelayDaemon.update_agent_status). False until confirmed.
        "blocked_confirmed": bool(raw.get("blocked_confirmed")),
        # permission = Approve/Reject flow; question = TUI menu / AskUserQuestion.
        "block_kind": block_kind,
        "agent_session_registered": session_registered,
        "status_reason": status_reason,
        # --- Liveness: how the relay last heard about this session, and when ---
        "source": str(raw.get("source") or ""),
        "last_seen_at": now_iso,
        "agent_name": str(raw.get("agent_name") or raw.get("name") or "herdr-agent"),
        "tool_call": str(raw.get("tool_call") or raw.get("action") or ""),
        "last_message": last_message,
        "last_output": last_output,
        "pid": raw.get("pid"),
        "updated_at": updated_at,
        "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        # --- Identity: who is running this agent and on what ---
        "cwd": str(raw.get("cwd") or raw.get("foreground_cwd") or ""),
        "harness": str(
            raw.get("harness")
            or (raw_agent_field if isinstance(raw_agent_field, str) else "")
            or ""
        ),
        "harness_version": str(raw.get("harness_version") or ""),
        "model": str(raw.get("model") or ""),
        "task_title": str(raw.get("task_title") or raw.get("terminal_title_stripped") or raw.get("terminal_title") or ""),
        "git_repo": str(raw.get("git_repo") or ""),
        "git_branch": str(raw.get("git_branch") or ""),
        "git_dirty": bool(raw.get("git_dirty")) if raw.get("git_dirty") is not None else None,
        # --- Runway: how much rope is left, from whichever honest source the harness offers ---
        "context_used": raw.get("context_used") if isinstance(raw.get("context_used"), (int, float)) else None,
        "context_limit": raw.get("context_limit") if isinstance(raw.get("context_limit"), (int, float)) else None,
        "quota": quota,
        "cost_usd": raw.get("cost_usd") if isinstance(raw.get("cost_usd"), (int, float)) else None,
    }


def agent_update_message(event: Dict[str, Any], local_hostname: Optional[str] = None) -> Dict[str, Any]:
    """Convert an incoming raw event into a standard WebSocket agent_update message."""
    # Handle wrapped event payload
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    if "agent" in payload and isinstance(payload["agent"], dict):
        base_dict = {**payload, **payload["agent"]}
    else:
        base_dict = payload

    normalized = normalize_agent_dict(base_dict, local_hostname=local_hostname)
    return {
        "type": "agent_update",
        "agent": normalized,
    }


def complete_agent_update_message(
    event: Dict[str, Any],
    current: Optional[Dict[str, Dict[str, Any]]] = None,
    local_hostname: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a complete agent update message merging new event data with current state."""
    raw_payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    partial = normalize_agent_dict(
        raw_payload,
        local_hostname=local_hostname,
    )
    agent_id = partial["id"]

    if current and agent_id in current:
        merged = dict(current[agent_id])
        # Fields with more than one accepted raw key: merge when ANY alias was
        # explicitly supplied, mirroring the alias fan-in in normalize_agent_dict.
        aliases = {
            "agent_name": ("name", "agent_name"),
            "cwd": ("cwd", "foreground_cwd"),
            "harness": ("harness", "agent"),
            "task_title": ("task_title", "terminal_title_stripped", "terminal_title"),
            "status_reason": ("status_reason", "reason", "message"),
            "agent_session_registered": ("agent_session",),
        }
        mergeable_extra = (
            "status_reason", "tool_call", "last_message", "last_output", "tab", "pid", "metadata",
            "cwd", "harness", "harness_version", "model", "task_title",
            "git_repo", "git_branch", "git_dirty",
            "context_used", "context_limit", "quota", "cost_usd",
            "block_kind", "blocked_confirmed",
        )
        # Merge only explicitly supplied fields or valid non-default updates
        for k, v in partial.items():
            if k in ("id", "host", "workspace", "pane_id"):
                continue
            supplied = any(a in raw_payload for a in aliases[k]) if k in aliases else (k in raw_payload)
            if supplied:
                merged[k] = v
            elif k in mergeable_extra and k in raw_payload:
                merged[k] = v
        # Status update
        if partial.get("status") and partial["status"] != "unknown":
            merged["status"] = partial["status"]
        merged["updated_at"] = partial.get("updated_at") or merged.get("updated_at")
        # last_seen_at is relay-local observation time: always refreshed on touch,
        # never sourced from the payload.
        merged["last_seen_at"] = partial.get("last_seen_at") or merged.get("last_seen_at")
        merged["id"] = agent_id
        # Re-derive block_kind whenever the episode is blocked unless the
        # payload explicitly supplied an override.
        if merged.get("status") == "blocked":
            if "block_kind" in raw_payload:
                merged["block_kind"] = normalize_block_kind(raw_payload.get("block_kind")) or partial.get("block_kind") or "permission"
            else:
                merged["block_kind"] = infer_block_kind(
                    merged.get("status_reason"),
                    merged.get("last_message"),
                    merged.get("last_output"),
                )
        else:
            merged["block_kind"] = ""
    else:
        merged = partial

    return {
        "type": "agent_update",
        "agent": merged,
    }


def apply_agent_message(
    current: Dict[str, Dict[str, Any]],
    message: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Apply an agent message (agent_update, agents_snapshot, agent_removed) to state dict in-place."""
    msg_type = message.get("type")

    if msg_type == "agents_snapshot":
        agents = message.get("agents")
        if isinstance(agents, list):
            current.clear()
            for ag in agents:
                if isinstance(ag, dict):
                    norm = normalize_agent_dict(ag)
                    current[norm["id"]] = norm
        elif isinstance(agents, dict):
            current.clear()
            for key, ag in agents.items():
                if isinstance(ag, dict):
                    norm = normalize_agent_dict(ag)
                    current[norm["id"]] = norm

    elif msg_type == "agent_update":
        agent_data = message.get("agent")
        if isinstance(agent_data, dict):
            norm = normalize_agent_dict(agent_data)
            agent_id = norm["id"]
            if agent_id in current:
                current[agent_id].update(norm)
            else:
                current[agent_id] = norm

    elif msg_type in ("agent_removed", "agent_deleted", "pane_closed"):
        agent_id = message.get("agent_id") or message.get("id")
        if agent_id and agent_id in current:
            del current[agent_id]
        elif message.get("pane_id"):
            # Find and delete matching pane_id
            p_id = str(message.get("pane_id"))
            to_del = [k for k, v in current.items() if str(v.get("pane_id")) == p_id]
            for k in to_del:
                del current[k]

    return current


def agents_snapshot_message(current: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Build an agents_snapshot message payload from current state dictionary."""
    return {
        "type": "agents_snapshot",
        "agents": list(current.values()),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


# -----------------------------------------------------------------------------
# Session Lifecycle: Reconciliation & Expiry
# -----------------------------------------------------------------------------

def _parse_iso_ts(value: Any) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 timestamp string, returning None on any failure."""
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _agent_ts(agent: Dict[str, Any]) -> Optional[datetime.datetime]:
    """Best-effort observation timestamp for an agent: last_seen_at, then updated_at."""
    ts = _parse_iso_ts(agent.get("last_seen_at")) or _parse_iso_ts(agent.get("updated_at"))
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return ts


def reconcile_agent_state(
    current: Dict[str, Dict[str, Any]],
    polled_ids: Set[str],
    host: str,
    miss_counts: Dict[str, int],
    grace: int = RECONCILE_GRACE,
) -> Tuple[List[str], Dict[str, int]]:
    """Diff current in-memory agents against one authoritative poll for `host`.

    Returns (pruned_agent_ids, next_miss_counts). Pure: the caller is
    responsible for deleting pruned ids from `current` and cleaning up any
    per-agent resources. Agents are pruned after `grace` consecutive missed
    polls so a single failed/slow poll never flushes a live fleet.

    `polled_ids` must contain composite ids exactly as produced by
    build_agent_id(host, workspace, pane_id).
    """
    host = str(host)
    polled = {str(a) for a in polled_ids}
    host_agent_ids = {
        aid for aid, ag in current.items()
        if str(ag.get("host") or parse_agent_id(aid)[0]) == host
    }

    # Forget counters for sessions that no longer exist at all.
    next_counts = {aid: n for aid, n in miss_counts.items() if aid in current}

    pruned: List[str] = []
    for aid in sorted(host_agent_ids):
        if aid in polled:
            next_counts[aid] = 0
            continue
        misses = next_counts.get(aid, 0) + 1
        if misses >= grace:
            pruned.append(aid)
            next_counts.pop(aid, None)
        else:
            next_counts[aid] = misses

    return pruned, next_counts


def find_expired_agents(
    current: Dict[str, Dict[str, Any]],
    ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
    now: Optional[datetime.datetime] = None,
) -> List[str]:
    """Return agent_ids whose last observation is older than `ttl_seconds`.

    Covers hook/UDP-only reporters that never appear in an authoritative
    `herdr agent list`. Entries without any parsable timestamp are never
    expired (absence of evidence is not evidence of closure).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    expired: List[str] = []
    for aid, ag in current.items():
        ts = _agent_ts(ag)
        if ts is None:
            continue
        if (now - ts).total_seconds() > float(ttl_seconds):
            expired.append(aid)
    return sorted(expired)


def agent_removed_message(agent_id: str, reason: str = "closed") -> Dict[str, Any]:
    """Build the standard agent_removed broadcast for a pruned session."""
    reason = "expired" if str(reason) == "expired" else "closed"
    host, workspace, pane_id = parse_agent_id(str(agent_id))
    return {
        "type": "agent_removed",
        "agent_id": str(agent_id),
        "host": str(host),
        "workspace": str(workspace),
        "pane_id": str(pane_id),
        "reason": reason,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
