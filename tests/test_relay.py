"""Unit and integration tests for herdr-outpost relay component."""

import asyncio
import json
import os
import sys
import pytest
import websockets
from websockets.asyncio.client import connect as ws_connect

# Add relay directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))

import agent_state
from herdr_relay import HerdrRelayDaemon, scrub, scrub_dict, validate_origin, CONFIG


def test_normalize_status():
    assert agent_state.normalize_status("running") == "working"
    assert agent_state.normalize_status("busy") == "working"
    assert agent_state.normalize_status("waiting") == "blocked"
    assert agent_state.normalize_status("prompting") == "blocked"
    assert agent_state.normalize_status("completed") == "done"
    assert agent_state.normalize_status("idle") == "idle"
    assert agent_state.normalize_status("random_unknown_state") == "unknown"


def test_agent_update_message():
    event = {
        "host": "devbox",
        "workspace": "feature-auth",
        "pane_id": "2",
        "status": "waiting",
        "status_reason": "Waiting for user confirmation",
        "agent_name": "backend-agent",
    }
    msg = agent_state.agent_update_message(event)
    assert msg["type"] == "agent_update"
    ag = msg["agent"]
    assert ag["id"] == "devbox:feature-auth:2"
    assert ag["status"] == "blocked"
    assert ag["status_reason"] == "Waiting for user confirmation"
    assert ag["agent_name"] == "backend-agent"


def test_complete_agent_update_message():
    current = {
        "local:default:1": {
            "id": "local:default:1",
            "host": "local",
            "workspace": "default",
            "pane_id": "1",
            "status": "working",
            "agent_name": "agent-alpha",
            "last_message": "Thinking...",
        }
    }
    event = {
        "host": "local",
        "workspace": "default",
        "pane_id": "1",
        "status": "blocked",
        "status_reason": "Needs approval to run command",
    }
    msg = agent_state.complete_agent_update_message(event, current=current)
    assert msg["agent"]["status"] == "blocked"
    assert msg["agent"]["agent_name"] == "agent-alpha"
    assert msg["agent"]["status_reason"] == "Needs approval to run command"


def test_apply_agent_message():
    state = {}
    # Apply update
    update_msg = {
        "type": "agent_update",
        "agent": {
            "host": "local",
            "workspace": "ws1",
            "pane_id": "10",
            "status": "working",
        },
    }
    agent_state.apply_agent_message(state, update_msg)
    assert "local:ws1:10" in state
    assert state["local:ws1:10"]["status"] == "working"

    # Apply snapshot
    snapshot_msg = {
        "type": "agents_snapshot",
        "agents": [
            {"host": "local", "workspace": "ws2", "pane_id": "20", "status": "idle"},
        ],
    }
    agent_state.apply_agent_message(state, snapshot_msg)
    assert "local:ws1:10" not in state
    assert "local:ws2:20" in state
    assert state["local:ws2:20"]["status"] == "idle"

    # Apply remove
    agent_state.apply_agent_message(state, {"type": "agent_removed", "id": "local:ws2:20"})
    assert len(state) == 0


def test_secret_scrubbing():
    CONFIG["token"] = "super-secret-token-12345"
    raw_log = "Connection with token=super-secret-token-12345 and Authorization: Bearer abcdef123456789"
    scrubbed = scrub(raw_log)
    assert "super-secret-token-12345" not in scrubbed
    assert "[REDACTED]" in scrubbed

    payload = {
        "user": "alice",
        "token": "super-secret-token-12345",
        "nested": {"password": "pass123", "safe": "hello"},
    }
    clean_dict = scrub_dict(payload)
    assert clean_dict["token"] == "[REDACTED]"
    assert clean_dict["nested"]["password"] == "[REDACTED]"
    assert clean_dict["nested"]["safe"] == "hello"


def test_origin_verification():
    trusted = ["https://herdr.example.com", "https://*.example.com", "http://localhost:8375"]

    assert validate_origin(None, trusted) is True
    assert validate_origin("http://localhost:8375", trusted) is True
    assert validate_origin("https://herdr.example.com", trusted) is True
    assert validate_origin("https://sub.example.com", trusted) is True
    assert validate_origin("https://malicious-site.com", trusted) is False


@pytest.mark.asyncio
async def test_daemon_endpoints_and_websocket():
    CONFIG["host"] = "127.0.0.1"
    CONFIG["port"] = 8399
    CONFIG["token"] = ""
    CONFIG["trusted_origins"] = []

    daemon = HerdrRelayDaemon()
    daemon.internal_ws_port = 8499
    await daemon.start()

    try:
        # Test HTTP GET /health
        reader, writer = await asyncio.open_connection("127.0.0.1", 8399)
        writer.write(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        resp_bytes = await reader.read(2048)
        assert b"200 OK" in resp_bytes
        assert b"herdr-outpost-relay" in resp_bytes
        writer.close()
        await writer.wait_closed()

        # Test HTTP POST /event
        event_payload = json.dumps({
            "host": "local",
            "workspace": "default",
            "pane_id": "42",
            "status": "blocked",
            "status_reason": "Waiting for git push confirmation",
        }).encode("utf-8")

        reader, writer = await asyncio.open_connection("127.0.0.1", 8399)
        req = (
            f"POST /event HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(event_payload)}\r\n\r\n"
        ).encode("utf-8") + event_payload

        writer.write(req)
        await writer.drain()
        resp_bytes = await reader.read(2048)
        assert b"200 OK" in resp_bytes
        assert b"accepted" in resp_bytes
        writer.close()
        await writer.wait_closed()

        # Test WebSocket connection & snapshot
        async with ws_connect("ws://127.0.0.1:8399") as ws:
            # First message received is snapshot
            init_msg = json.loads(await ws.recv())
            assert init_msg["type"] == "agents_snapshot"
            assert any(a["pane_id"] == "42" for a in init_msg["agents"])

            # Test ping / pong
            await ws.send(json.dumps({"type": "ping"}))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "pong"

    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_output_streaming_subscription():
    """subscribe_output should ack, force-push a capture, then push only on change."""
    CONFIG["host"] = "127.0.0.1"
    CONFIG["port"] = 8397
    CONFIG["token"] = ""
    CONFIG["trusted_origins"] = []
    CONFIG["poll_interval"] = 9999.0
    CONFIG["output_interval"] = 0.1

    daemon = HerdrRelayDaemon()
    daemon.internal_ws_port = 8497

    # Fake herdr pane capture
    capture = {"text": "$ run tests\nall good"}

    async def fake_exec(args, host="local"):
        if args[:2] == ["pane", "read"]:
            return 0, capture["text"], ""
        return 0, "[]", ""

    daemon.execute_herdr_cmd = fake_exec

    await daemon.start()
    try:
        await daemon.update_agent_status(
            {"host": "local", "workspace": "default", "pane_id": "9", "status": "working"},
            source="test",
        )

        async with ws_connect("ws://127.0.0.1:8397") as ws:
            init_msg = json.loads(await ws.recv())  # snapshot
            assert init_msg["type"] == "agents_snapshot"

            # Subscribe with a bare pane_id -> resolved to the composite id
            await ws.send(json.dumps({"type": "subscribe_output", "pane_id": "9"}))
            ack = json.loads(await ws.recv())
            assert ack["type"] == "output_subscribed"
            assert ack["agent_id"] == "local:default:9"
            assert ack["interval"] == 0.1

            # An immediate forced capture follows the ack
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            assert frame["type"] == "pane_output"
            assert frame["agent_id"] == "local:default:9"
            assert frame["pane_id"] == "9"
            assert frame["full"] is True
            assert "all good" in frame["data"]

            # Changed content triggers a new push from the stream loop
            capture["text"] = "$ run tests\n1 failed"
            frame2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            assert frame2["type"] == "pane_output"
            assert "1 failed" in frame2["data"]

            # Unchanged content is not re-pushed (content-hash dedupe)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), timeout=0.5)

            # Unsubscribe stops the stream for this client
            await ws.send(json.dumps({"type": "unsubscribe_output", "agent_id": "local:default:9"}))
            un = json.loads(await ws.recv())
            assert un["type"] == "output_unsubscribed"
            capture["text"] = "$ run tests\n2 failed"
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), timeout=0.5)
    finally:
        await daemon.stop()
        CONFIG["output_interval"] = 3.0
