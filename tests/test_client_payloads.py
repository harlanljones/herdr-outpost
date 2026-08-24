"""Tests for client payloads, event structures, and notification schemas."""

from __future__ import annotations

import json
import os
import sys

# Ensure relay is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from agent_state import (
    agents_snapshot_message,
    normalize_agent_dict,
    normalize_status,
)


class TestEventStructures:
    """Test standard incoming event structures from herdr-push and external hooks."""

    def test_herdr_push_blocked_event_structure(self):
        event = {
            "type": "event",
            "event": "pane_blocked",
            "timestamp": "2026-08-20T19:00:00Z",
            "payload": {
                "host": "macbook-pro",
                "workspace": "backend-api",
                "pane_id": "pane_3",
                "status": "needs_approval",
                "reason": "Agent wants to execute `rm -rf tmp/`",
                "agent_name": "codegen-agent",
                "tool_call": "run_command",
            },
        }

        # Validate event envelope
        assert event["type"] == "event"
        assert event["event"] == "pane_blocked"
        assert "payload" in event

        # Normalize agent
        agent = normalize_agent_dict(event["payload"])
        assert agent["id"] == "macbook-pro:backend-api:pane_3"
        assert agent["status"] == "blocked"
        assert agent["status_reason"] == "Agent wants to execute `rm -rf tmp/`"
        assert agent["agent_name"] == "codegen-agent"

    def test_herdr_push_done_event_structure(self):
        event = {
            "type": "event",
            "event": "pane_done",
            "payload": {
                "host": "local",
                "workspace": "herdr-outpost",
                "pane_id": "pane_1",
                "status": "completed",
                "message": "All unit tests passed successfully.",
            },
        }
        agent = normalize_agent_dict(event["payload"])
        assert agent["status"] == "done"
        assert agent["status_reason"] == "All unit tests passed successfully."

    def test_herdr_output_stream_event(self):
        event = {
            "type": "pane_output",
            "pane_id": "pane_1",
            "host": "local",
            "workspace": "default",
            "format": "ansi",
            "data": "\x1b[32mBuild Successful\x1b[0m\n",
        }
        assert event["type"] == "pane_output"
        assert event["format"] == "ansi"
        assert "\x1b[32m" in event["data"]


class TestWebPushNotificationPayloads:
    """Test Web Push (VAPID) notification formatting and action schemas."""

    def format_web_push_payload(
        self,
        agent_id: str,
        status: str,
        reason: str,
        base_url: str = "https://herdr.example.com",
    ) -> Dict[str, Any]:
        """Build Web Push notification payload conforming to browser Notification API."""
        is_blocked = status == "blocked"
        title = f"Action Required: {agent_id}" if is_blocked else f"Agent Done: {agent_id}"
        
        actions = []
        if is_blocked:
            actions = [
                {"action": "approve", "title": "Approve (y)", "icon": "/icons/approve.png"},
                {"action": "reject", "title": "Reject (n)", "icon": "/icons/reject.png"},
            ]

        return {
            "title": title,
            "body": reason or f"Agent status changed to {status}",
            "icon": "/logo.svg",
            "badge": "/logo.svg",
            "tag": f"herdr-{agent_id}",
            "data": {
                "agent_id": agent_id,
                "status": status,
                "url": f"{base_url}?agent={agent_id}",
            },
            "actions": actions,
        }

    def test_web_push_blocked_has_interactive_actions(self):
        payload = self.format_web_push_payload(
            agent_id="local:main:1",
            status="blocked",
            reason="Requires permission to run migrations",
        )

        assert "Action Required" in payload["title"]
        assert len(payload["actions"]) == 2
        assert payload["actions"][0]["action"] == "approve"
        assert payload["actions"][1]["action"] == "reject"
        assert payload["data"]["agent_id"] == "local:main:1"
        assert "local:main:1" in payload["data"]["url"]

        # Ensure JSON serializable
        serialized = json.dumps(payload)
        assert json.loads(serialized) == payload

    def test_web_push_done_has_no_actions(self):
        payload = self.format_web_push_payload(
            agent_id="gpu:eval:2",
            status="done",
            reason="Model evaluation complete",
        )
        assert "Agent Done" in payload["title"]
        assert len(payload["actions"]) == 0
        assert payload["data"]["status"] == "done"


class TestTelegramAlertPayloads:
    """Test Telegram message structure formatting."""

    def format_telegram_alert(
        self,
        agent: Dict[str, Any],
        dashboard_url: str = "https://herdr.example.com",
    ) -> str:
        """Format a clean markdown alert for Telegram bot messages."""
        status = agent.get("status", "unknown")
        emoji = "BLOCKED" if status == "blocked" else "DONE" if status == "done" else "PENDING"
        agent_id = agent.get("id", "unknown")
        reason = agent.get("status_reason") or agent.get("last_message") or "No details provided"
        tool = agent.get("tool_call") or "None"

        lines = [
            f"{emoji} *Herdr Outpost Alert*",
            f"*Agent:* `{agent_id}`",
            f"*Status:* `{status.upper()}`",
            f"*Tool:* `{tool}`",
            f"*Details:* {reason}",
            f"[Open Dashboard]({dashboard_url}?agent={agent_id})",
        ]
        return "\n".join(lines)

    def test_telegram_alert_markdown_formatting(self):
        agent = {
            "id": "local:dev:pane_5",
            "status": "blocked",
            "tool_call": "edit_file",
            "status_reason": "File conflict detected in config.py",
        }
        alert = self.format_telegram_alert(agent)
        assert "*Herdr Outpost Alert*" in alert
        assert "`local:dev:pane_5`" in alert
        assert "`BLOCKED`" in alert
        assert "`edit_file`" in alert
        assert "File conflict detected in config.py" in alert
        assert "https://herdr.example.com?agent=local:dev:pane_5" in alert


class TestClientCommandSchemas:
    """Test client command validation for prompt, approve, reject, interrupt."""

    @pytest.mark.parametrize(
        "cmd_type,extra_fields",
        [
            ("prompt", {"text": "Please fix the failing tests."}),
            ("respond", {"text": "y\n"}),
            ("approve", {"text": "y"}),
            ("reject", {"text": "n"}),
            ("interrupt", {}),
            ("read_pane", {"format": "ansi", "lines": 100}),
            ("subscribe", {"filter": "all"}),
        ],
    )
    def test_client_command_schema(self, cmd_type, extra_fields):
        payload = {
            "type": cmd_type,
            "agent_id": "prod-server:webapp:pane_2",
            **extra_fields,
        }

        assert payload["type"] == cmd_type
        assert payload["agent_id"] == "prod-server:webapp:pane_2"
        # Validate serialization roundtrip
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        assert decoded == payload


class TestSubagentTreePayload:
    """Subagent tree fields ride the existing snapshot/update payloads."""

    def test_snapshot_with_tree_is_json_serializable(self):
        agent = normalize_agent_dict({
            "host": "local", "workspace": "w", "pane_id": "1",
            "status": "working",
            "session_id": "ses_root",
            "subagents": [
                {
                    "id": "ses_child_1",
                    "title": "Explore layout (@explore subagent)",
                    "kind": "explore",
                    "model": "sonnet-4",
                    "tokens": 1200,
                    "updated_at": "2026-08-24T12:00:00+00:00",
                    "active": True,
                    "children": [],
                }
            ],
        })
        message = agents_snapshot_message({"local:w:1": agent})

        decoded = json.loads(json.dumps(message))
        shipped = decoded["agents"][0]
        assert shipped["session_id"] == "ses_root"
        assert shipped["subagents"][0]["id"] == "ses_child_1"
        assert shipped["subagents"][0]["tokens"] == 1200

    def test_agents_without_trees_ship_empty_list_not_null(self):
        agent = normalize_agent_dict({
            "host": "local", "workspace": "w", "pane_id": "2",
        })
        message = agents_snapshot_message({"local:w:2": agent})
        decoded = json.loads(json.dumps(message))
        assert decoded["agents"][0]["subagents"] == []
