"""Tests for relay/agent_state.py state tracking, merging, snapshots, and normalization."""

from __future__ import annotations

import os
import sys

# Ensure relay module is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from agent_state import (
    VALID_STATUSES,
    STATUS_MAP,
    agent_update_message,
    agents_snapshot_message,
    apply_agent_message,
    build_agent_id,
    complete_agent_update_message,
    get_default_hostname,
    normalize_agent_dict,
    normalize_status,
    parse_agent_id,
)


class TestStatusNormalization:
    """Test agent status normalization and mapping."""

    def test_valid_statuses_defined(self):
        assert "blocked" in VALID_STATUSES
        assert "working" in VALID_STATUSES
        assert "done" in VALID_STATUSES
        assert "idle" in VALID_STATUSES
        assert "unknown" in VALID_STATUSES

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("blocked", "blocked"),
            ("waiting", "blocked"),
            ("prompt", "blocked"),
            ("prompting", "blocked"),
            ("approval", "blocked"),
            ("needs_approval", "blocked"),
            ("needs-approval", "blocked"),
            ("confirm", "blocked"),
            ("waiting_for_input", "blocked"),
            ("working", "working"),
            ("running", "working"),
            ("busy", "working"),
            ("executing", "working"),
            ("thinking", "working"),
            ("done", "done"),
            ("finished", "done"),
            ("completed", "done"),
            ("success", "done"),
            ("idle", "idle"),
            ("ready", "idle"),
            ("paused", "idle"),
            ("stopped", "idle"),
            ("unknown", "unknown"),
            ("", "unknown"),
            (None, "unknown"),
            ("nonexistent_state", "unknown"),
            ("  RUNNING  ", "working"),
            ("Needs-Approval", "blocked"),
        ],
    )
    def test_normalize_status(self, raw, expected):
        assert normalize_status(raw) == expected


class TestAgentIdAndHostNormalization:
    """Test agent ID construction, parsing, and host normalization."""

    def test_get_default_hostname_explicit(self):
        assert get_default_hostname("custom-host") == "custom-host"
        assert get_default_hostname("  trimmed-host  ") == "trimmed-host"

    def test_get_default_hostname_implicit(self):
        host = get_default_hostname(None)
        assert isinstance(host, str)
        assert len(host) > 0

    def test_build_agent_id(self):
        assert build_agent_id("local", "project-a", "pane_1") == "local:project-a:pane_1"
        assert build_agent_id("", "", "1") == "local:default:1"
        assert build_agent_id("remote.box", "dev", 42) == "remote.box:dev:42"

    def test_parse_agent_id_three_parts(self):
        host, ws, pane = parse_agent_id("myhost:myws:pane_10")
        assert host == "myhost"
        assert ws == "myws"
        assert pane == "pane_10"

    def test_parse_agent_id_two_parts(self):
        host, ws, pane = parse_agent_id("remote-node:pane_5")
        assert host == "remote-node"
        assert ws == "default"
        assert pane == "pane_5"

    def test_parse_agent_id_single_part(self):
        host, ws, pane = parse_agent_id("pane_single")
        assert host == "local"
        assert ws == "default"
        assert pane == "pane_single"

    def test_parse_agent_id_colon_in_pane(self):
        host, ws, pane = parse_agent_id("host:ws:pane:sub:1")
        assert host == "host"
        assert ws == "ws"
        assert pane == "pane:sub:1"


class TestAgentDictNormalization:
    """Test normalizing raw dictionaries into standard agent schemas."""

    def test_normalize_basic_agent_dict(self):
        raw = {
            "host": "dev-box",
            "workspace": "herdr-outpost",
            "pane_id": "pane_2",
            "status": "running",
            "agent_name": "backend-coder",
            "tool_call": "run_command",
            "last_message": "Working on relay tests",
            "last_output": "test output sample",
            "pid": 12345,
        }
        normalized = normalize_agent_dict(raw, local_hostname="fallback-host")

        assert normalized["id"] == "dev-box:herdr-outpost:pane_2"
        assert normalized["host"] == "dev-box"
        assert normalized["workspace"] == "herdr-outpost"
        assert normalized["pane_id"] == "pane_2"
        assert normalized["status"] == "working"
        assert normalized["agent_name"] == "backend-coder"
        assert normalized["tool_call"] == "run_command"
        assert normalized["pid"] == 12345
        assert "updated_at" in normalized
        assert isinstance(normalized["metadata"], dict)

    def test_normalize_composite_id_unpacking(self):
        raw = {
            "id": "gpu-server:models:pane_7",
            "status": "waiting",
            "reason": "Approval required to write file",
        }
        normalized = normalize_agent_dict(raw)

        assert normalized["id"] == "gpu-server:models:pane_7"
        assert normalized["host"] == "gpu-server"
        assert normalized["workspace"] == "models"
        assert normalized["pane_id"] == "pane_7"
        assert normalized["status"] == "blocked"
        assert normalized["status_reason"] == "Approval required to write file"

    def test_normalize_nested_agent_status(self):
        raw = {
            "agent": {
                "status": "finished",
                "name": "qa-subagent",
            },
            "paneId": "pane_8",
        }
        normalized = normalize_agent_dict(raw, local_hostname="localhost")

        assert normalized["status"] == "done"
        assert normalized["pane_id"] == "pane_8"
        # a dict "agent" field is legacy nested status, not a harness label
        assert normalized["harness"] == ""

    def test_normalize_identity_and_runway_fields(self):
        raw = {
            "host": "dev-box",
            "workspace": "herdr-outpost",
            "pane_id": "pane_2",
            "status": "working",
            "agent": "claude",  # herdr's real `agent list` harness label (a string)
            "foreground_cwd": "/home/harlan/dev/herdr-outpost",
            "terminal_title_stripped": "◐ Claude Code",
            "model": "claude-opus-5",
            "git_repo": "herdr-outpost",
            "git_branch": "main",
            "git_dirty": True,
            "context_used": 56000,
            "context_limit": 200000,
            "cost_usd": 0.42,
        }
        normalized = normalize_agent_dict(raw)

        assert normalized["harness"] == "claude"
        assert normalized["cwd"] == "/home/harlan/dev/herdr-outpost"
        assert normalized["task_title"] == "◐ Claude Code"
        assert normalized["model"] == "claude-opus-5"
        assert normalized["git_repo"] == "herdr-outpost"
        assert normalized["git_branch"] == "main"
        assert normalized["git_dirty"] is True
        assert normalized["context_used"] == 56000
        assert normalized["context_limit"] == 200000
        assert normalized["cost_usd"] == 0.42
        assert normalized["quota"] is None

    def test_normalize_defaults_new_fields_absent(self):
        """A bare status event carries none of the new fields -- they must default to
        empty/None, never raise, so a harness/probe that reports nothing is indistinguishable
        from one whose fields legitimately arrived empty."""
        raw = {"host": "h", "workspace": "w", "pane_id": "1", "status": "working"}
        normalized = normalize_agent_dict(raw)

        assert normalized["harness"] == ""
        assert normalized["cwd"] == ""
        assert normalized["model"] == ""
        assert normalized["task_title"] == ""
        assert normalized["git_repo"] == ""
        assert normalized["git_branch"] == ""
        assert normalized["git_dirty"] is None
        assert normalized["context_used"] is None
        assert normalized["context_limit"] is None
        assert normalized["quota"] is None
        assert normalized["cost_usd"] is None

    def test_normalize_quota_dict_passthrough(self):
        raw = {
            "pane_id": "1",
            "quota": {"label": "5-hour window", "percent": 0.62, "resets_at": "2026-08-21T05:19:02Z"},
        }
        normalized = normalize_agent_dict(raw)
        assert normalized["quota"] == {"label": "5-hour window", "percent": 0.62, "resets_at": "2026-08-21T05:19:02Z"}


class TestUpdateMergingAndSnapshots:
    """Test diff updates, state merging, and snapshot generations."""

    def test_agent_update_message_wrapper(self):
        event = {
            "type": "event",
            "payload": {
                "host": "local",
                "workspace": "core",
                "pane_id": "1",
                "status": "thinking",
                "name": "planner",
            },
        }
        msg = agent_update_message(event)

        assert msg["type"] == "agent_update"
        assert msg["agent"]["id"] == "local:core:1"
        assert msg["agent"]["status"] == "working"

    def test_complete_agent_update_message_merging(self):
        current_state = {
            "local:core:1": {
                "id": "local:core:1",
                "host": "local",
                "workspace": "core",
                "pane_id": "1",
                "status": "working",
                "agent_name": "planner",
                "last_message": "Step 1 complete",
                "tool_call": "write_to_file",
                "metadata": {"task": "test"},
            }
        }

        # Incoming update with new status and reason
        event = {
            "host": "local",
            "workspace": "core",
            "pane_id": "1",
            "status": "prompt",
            "status_reason": "Needs user confirmation",
        }

        result = complete_agent_update_message(event, current=current_state)
        agent = result["agent"]

        assert result["type"] == "agent_update"
        assert agent["id"] == "local:core:1"
        assert agent["status"] == "blocked"
        assert agent["status_reason"] == "Needs user confirmation"
        # Preserved previous attributes
        assert agent["agent_name"] == "planner"
        assert agent["tool_call"] == "write_to_file"

    def test_merge_preserves_enriched_fields_on_bare_status_event(self):
        """A bare status-change event (e.g. a UDP hook firing on state transition) must
        not wipe out model/context/git fields the poller enriched earlier -- those only
        arrive via alias keys the terse event never sends."""
        current_state = {
            "local:core:1": {
                "id": "local:core:1",
                "host": "local",
                "workspace": "core",
                "pane_id": "1",
                "status": "working",
                "harness": "claude",
                "model": "claude-opus-5",
                "cwd": "/home/harlan/dev/herdr-outpost",
                "task_title": "Shaping the frontend revamp",
                "git_repo": "herdr-outpost",
                "git_branch": "main",
                "context_used": 56000,
                "context_limit": 200000,
            }
        }
        event = {"host": "local", "workspace": "core", "pane_id": "1", "status": "blocked", "reason": "awaiting approval"}

        result = complete_agent_update_message(event, current=current_state)
        agent = result["agent"]

        assert agent["status"] == "blocked"
        assert agent["status_reason"] == "awaiting approval"
        assert agent["harness"] == "claude"
        assert agent["model"] == "claude-opus-5"
        assert agent["cwd"] == "/home/harlan/dev/herdr-outpost"
        assert agent["task_title"] == "Shaping the frontend revamp"
        assert agent["git_repo"] == "herdr-outpost"
        assert agent["git_branch"] == "main"
        assert agent["context_used"] == 56000
        assert agent["context_limit"] == 200000

    def test_merge_updates_via_alias_keys(self):
        """A poll event that only carries herdr's native `agent`/`foreground_cwd`/
        `terminal_title_stripped` keys must still be recognized as supplying harness/cwd/
        task_title, and overwrite the previous values."""
        current_state = {
            "local:core:1": {
                "id": "local:core:1",
                "host": "local",
                "workspace": "core",
                "pane_id": "1",
                "status": "working",
                "harness": "claude",
                "cwd": "/old/path",
                "task_title": "old task",
            }
        }
        event = {
            "host": "local",
            "workspace": "core",
            "pane_id": "1",
            "status": "working",
            "agent": "cline",
            "foreground_cwd": "/new/path",
            "terminal_title_stripped": "new task",
        }

        result = complete_agent_update_message(event, current=current_state)
        agent = result["agent"]

        assert agent["harness"] == "cline"
        assert agent["cwd"] == "/new/path"
        assert agent["task_title"] == "new task"

    def test_apply_agent_message_snapshot_list(self):
        state = {}
        snapshot_msg = {
            "type": "agents_snapshot",
            "agents": [
                {"host": "h1", "workspace": "w1", "pane_id": "p1", "status": "running"},
                {"host": "h2", "workspace": "w2", "pane_id": "p2", "status": "idle"},
            ],
        }

        apply_agent_message(state, snapshot_msg)
        assert len(state) == 2
        assert "h1:w1:p1" in state
        assert "h2:w2:p2" in state
        assert state["h1:w1:p1"]["status"] == "working"
        assert state["h2:w2:p2"]["status"] == "idle"

    def test_apply_agent_message_snapshot_dict(self):
        state = {"old:old:0": {"id": "old:old:0", "status": "done"}}
        snapshot_msg = {
            "type": "agents_snapshot",
            "agents": {
                "new:ws:1": {"host": "new", "workspace": "ws", "pane_id": "1", "status": "busy"}
            },
        }

        apply_agent_message(state, snapshot_msg)
        assert len(state) == 1
        assert "new:ws:1" in state
        assert "old:old:0" not in state

    def test_apply_agent_message_diff_update(self):
        state = {
            "local:dev:1": {
                "id": "local:dev:1",
                "host": "local",
                "workspace": "dev",
                "pane_id": "1",
                "status": "working",
            }
        }
        update_msg = {
            "type": "agent_update",
            "agent": {
                "host": "local",
                "workspace": "dev",
                "pane_id": "1",
                "status": "done",
                "last_message": "Task finished",
            },
        }

        apply_agent_message(state, update_msg)
        assert state["local:dev:1"]["status"] == "done"
        assert state["local:dev:1"]["last_message"] == "Task finished"

    def test_apply_agent_message_remove(self):
        state = {
            "local:dev:1": {"id": "local:dev:1", "pane_id": "1"},
            "local:dev:2": {"id": "local:dev:2", "pane_id": "2"},
        }

        # Remove by agent_id
        apply_agent_message(state, {"type": "agent_removed", "agent_id": "local:dev:1"})
        assert "local:dev:1" not in state
        assert "local:dev:2" in state

        # Remove by pane_id
        apply_agent_message(state, {"type": "pane_closed", "pane_id": "2"})
        assert "local:dev:2" not in state
        assert len(state) == 0

    def test_agents_snapshot_message(self):
        state = {
            "h:w:1": {"id": "h:w:1", "status": "working"},
            "h:w:2": {"id": "h:w:2", "status": "blocked"},
        }
        snapshot = agents_snapshot_message(state)

        assert snapshot["type"] == "agents_snapshot"
        assert len(snapshot["agents"]) == 2
        assert "timestamp" in snapshot
