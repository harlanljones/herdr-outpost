"""Daemon-level session lifecycle tests for herdr-outpost relay.

Covers the polling/reconciliation contract of HerdrRelayDaemon:
- missing agents are pruned only after RECONCILE_GRACE consecutive successful polls,
  broadcasting exactly one agent_removed (reason "closed"),
- failed polls (nonzero exit / bad payload) skip reconciliation entirely,
- stale agents past CONFIG["session_ttl"] expire with reason "expired",
- removed agents purge output_cursors/output_locks,
- /health exposes agents_by_host and last_reconcile_at.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import subprocess
import sys
import time

# Ensure relay module is in sys.path (same pattern as test_relay.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from herdr_relay import HerdrRelayDaemon, CONFIG


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def iso_utc(**offset_kwargs) -> str:
    """ISO-8601 UTC timestamp offset from now by the given timedelta kwargs."""
    return (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**offset_kwargs)
    ).isoformat()


def make_raw_agent(
    host: str = "local",
    workspace: str = "default",
    pane_id: str = "1",
    status: str = "working",
) -> dict:
    """A minimal raw agent payload shaped like a `herdr agent list` entry."""
    return {
        "host": host,
        "workspace": workspace,
        "pane_id": pane_id,
        "status": status,
    }


class BroadcastCapture:
    """Shadow daemon.broadcast_state to record every outbound message."""

    def __init__(self):
        self.messages: list = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)

    def of_type(self, type_name: str) -> list:
        return [m for m in self.messages if m.get("type") == type_name]


class PollHarness:
    """Fake execute_herdr_cmd serving canned `herdr agent list` envelopes.

    Every `agent list` attempt consumes exactly one permit, so tests step the
    daemon's poll loop one cycle at a time with release_cycle() -- fully
    deterministic, independent of CONFIG["poll_interval"].
    """

    def __init__(self, panes_by_host: dict | None = None, fail_hosts: tuple = ()):
        self.panes_by_host = panes_by_host or {}
        self.fail_hosts = set(fail_hosts)
        self.cycles = 0
        self._permits = asyncio.Semaphore(0)

    def release_cycle(self) -> None:
        """Grant permission for one more poll cycle to execute."""
        self._permits.release()

    async def __call__(self, args: list, host: str = "local"):
        if args[:2] == ["agent", "list"]:
            await self._permits.acquire()
            self.cycles += 1
            if host in self.fail_hosts:
                return 1, "", "simulated herdr failure"
            agents = [
                {"host": host, "workspace": "default", "pane_id": str(p), "status": "working"}
                for p in self.panes_by_host.get(host, [])
            ]
            return 0, json.dumps({"result": {"agents": agents}}), ""
        return 0, "[]", ""


class RealisticHerdrHarness(PollHarness):
    """Emits payloads shaped exactly like live `herdr agent list` output:
    composite pane_id ("ws:pane"), workspace_id-only entries (no `workspace`
    key), and `agent_status` as the status field."""

    def __init__(self, workspaces_by_host: dict | None = None):
        super().__init__()
        self.workspaces_by_host = workspaces_by_host or {}

    async def __call__(self, args: list, host: str = "local"):
        if args[:2] == ["agent", "list"]:
            await self._permits.acquire()
            self.cycles += 1
            if host in self.fail_hosts:
                return 1, "", "simulated herdr failure"
            agents = []
            for ws in self.workspaces_by_host.get(host, []):
                agents.append({
                    "agent": "opencode",
                    "agent_status": "working",
                    "cwd": f"/home/dev/{ws.lower()}",
                    "foreground_cwd": f"/home/dev/{ws.lower()}",
                    "pane_id": f"{ws}:p1",
                    "tab_id": f"{ws}:t1",
                    "workspace_id": ws,
                    "revision": 1,
                })
            return 0, json.dumps({"result": {"agents": agents}, "type": "agent_list"}), ""
        return 0, "[]", ""


async def wait_for_cycles(harness: PollHarness, target: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while harness.cycles < target:
        assert time.monotonic() < deadline, f"timed out waiting for poll cycle #{target}"
        await asyncio.sleep(0.01)


async def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        await asyncio.sleep(0.02)


async def http_get_json(port: int, path: str) -> dict:
    """Minimal HTTP/1.1 GET returning the parsed JSON body."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()

    raw = b""
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            break
        raw += chunk
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()

    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"200" in head.split(b"\r\n")[0], f"unexpected status line: {head!r}"
    return json.loads(body.decode("utf-8"))


def stop_poll_task(daemon: HerdrRelayDaemon, poll_task: asyncio.Task) -> None:
    daemon.running = False
    poll_task.cancel()


async def reap(poll_task: asyncio.Task) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await poll_task


def make_daemon_with(harness: PollHarness, capture: BroadcastCapture) -> HerdrRelayDaemon:
    daemon = HerdrRelayDaemon()
    daemon.broadcast_state = capture  # instance attribute shadows the method
    daemon.execute_herdr_cmd = harness
    return daemon


@pytest.fixture
def isolated_config():
    """Snapshot CONFIG around a test so mutations never leak across tests."""
    original = dict(CONFIG)
    yield
    CONFIG.clear()
    CONFIG.update(original)


@pytest.mark.asyncio
async def test_poll_prunes_missing_agent_after_grace(isolated_config):
    """Two live agents; authoritative list shrinks to one. Grace must hold on the
    first missed poll and prune with a single 'closed' removal on the second."""
    CONFIG["remotes"] = []
    CONFIG["poll_interval"] = 0.05  # fast park between cycles; harness gates pacing
    CONFIG["session_ttl"] = 90.0

    capture = BroadcastCapture()
    harness = PollHarness(panes_by_host={"local": ["1"]})
    daemon = make_daemon_with(harness, capture)

    # Seed two live agents the way the poller itself would report them
    await daemon.update_agent_status(make_raw_agent(pane_id="1"), source="poll:local")
    await daemon.update_agent_status(make_raw_agent(pane_id="2"), source="poll:local")

    assert set(daemon.agents_state) == {"local:default:1", "local:default:2"}
    # Contract: update_agent_status stamps source when the payload omits one
    assert daemon.agents_state["local:default:1"]["source"] == "poll:local"
    assert daemon.agents_state["local:default:2"]["source"] == "poll:local"

    daemon.running = True
    poll_task = asyncio.create_task(daemon.poll_herdr_agents())
    try:
        # --- First successful poll sees only pane 1: grace holds, nothing prunes ---
        harness.release_cycle()
        await wait_for_cycles(harness, 1)
        await asyncio.sleep(0.05)  # let any (contract-forbidden) pruning surface
        assert "local:default:2" in daemon.agents_state
        assert capture.of_type("agent_removed") == []

        # --- Second consecutive miss reaches grace: agent #2 is gone ---
        harness.release_cycle()
        await wait_for_cycles(harness, 2)
        await wait_until(
            lambda: "local:default:2" not in daemon.agents_state
            and len(capture.of_type("agent_removed")) >= 1
        )
        assert "local:default:1" in daemon.agents_state

        removals = capture.of_type("agent_removed")
        assert len(removals) == 1
        assert removals[0]["type"] == "agent_removed"
        assert removals[0]["agent_id"] == "local:default:2"
        assert removals[0]["reason"] == "closed"
        assert removals[0]["host"] == "local"
        assert removals[0]["workspace"] == "default"
        assert removals[0]["pane_id"] == "2"

        # Removed agents purge their output-streaming resources
        assert "local:default:2" not in daemon.output_cursors
        assert "local:default:2" not in daemon.output_locks
    finally:
        stop_poll_task(daemon, poll_task)
        await reap(poll_task)


@pytest.mark.asyncio
async def test_realistic_herdr_payloads_survive_repeated_polls(isolated_config):
    """Regression: live herdr entries use composite pane_id ('ws:p1') and
    workspace_id-only keys. The reconcile diff must derive polled ids through
    the SAME normalization as the state write, or every poll counts a miss and
    live agents thrash prune/re-add forever (observed in live smoke test)."""
    CONFIG["remotes"] = []
    CONFIG["poll_interval"] = 0.05
    CONFIG["session_ttl"] = 90.0

    capture = BroadcastCapture()
    harness = RealisticHerdrHarness(workspaces_by_host={"local": ["wP", "wY"]})
    daemon = make_daemon_with(harness, capture)

    daemon.running = True
    poll_task = asyncio.create_task(daemon.poll_herdr_agents())
    try:
        for _ in range(4):
            harness.release_cycle()
        await wait_for_cycles(harness, 4)
        await asyncio.sleep(0.05)

        assert set(daemon.agents_state) == {"local:default:wP:p1", "local:default:wY:p1"}
        assert all(a["source"] == "poll:local" for a in daemon.agents_state.values())
        assert all(a.get("last_seen_at") for a in daemon.agents_state.values())
        assert capture.of_type("agent_removed") == []
    finally:
        stop_poll_task(daemon, poll_task)
        await reap(poll_task)


@pytest.mark.asyncio
async def test_poll_marks_unregistered_sessions_and_dampens_blocked(isolated_config):
    """Live herdr omits `agent_session` entirely for panes whose lifecycle hook
    registration was lost (the false-blocked signature). The poll loop must
    inject an explicit agent_session=None so polled state records detection
    health, and a single poll-sourced blocked must stay alarm-unconfirmed."""
    CONFIG["remotes"] = []
    CONFIG["poll_interval"] = 0.05
    CONFIG["session_ttl"] = 90.0

    class MixedHarness(PollHarness):
        async def __call__(self, args: list, host: str = "local"):
            if args[:2] == ["agent", "list"]:
                await self._permits.acquire()
                self.cycles += 1
                agents = [
                    {
                        "agent": "opencode",
                        "agent_status": "blocked",
                        "cwd": "/home/dev/clify",
                        "foreground_cwd": "/home/dev/clify",
                        "pane_id": "wA:p1",
                        "tab_id": "wA:t1",
                        "workspace_id": "wA",
                        # note: NO agent_session key -- herdr's lost-registration shape
                    },
                    {
                        "agent": "opencode",
                        "agent_status": "working",
                        "cwd": "/home/dev/other",
                        "foreground_cwd": "/home/dev/other",
                        "pane_id": "wB:p1",
                        "tab_id": "wB:t1",
                        "workspace_id": "wB",
                        "agent_session": {"agent": "opencode", "value": "ses_1"},
                    },
                ]
                return 0, json.dumps({"result": {"agents": agents}, "type": "agent_list"}), ""
            return 0, "[]", ""

    capture = BroadcastCapture()
    daemon = make_daemon_with(MixedHarness(), capture)

    daemon.running = True
    poll_task = asyncio.create_task(daemon.poll_herdr_agents())
    try:
        daemon.execute_herdr_cmd.release_cycle()
        await wait_for_cycles(daemon.execute_herdr_cmd, 1)
        await asyncio.sleep(0.05)

        unregistered = daemon.agents_state["local:default:wA:p1"]
        healthy = daemon.agents_state["local:default:wB:p1"]
        assert unregistered["status"] == "blocked"
        assert unregistered["blocked_confirmed"] is False  # dampened on first poll
        assert unregistered["agent_session_registered"] is False
        assert healthy["status"] == "working"
        assert healthy["agent_session_registered"] is True
    finally:
        stop_poll_task(daemon, poll_task)
        await reap(poll_task)


@pytest.mark.asyncio
async def test_failed_polls_skip_reconciliation(isolated_config):
    """With `herdr agent list` failing (nonzero exit), reconciliation is skipped
    entirely: agents survive repeated failed cycles and nothing is removed."""
    CONFIG["remotes"] = []
    CONFIG["poll_interval"] = 0.05
    CONFIG["session_ttl"] = 90.0

    capture = BroadcastCapture()
    harness = PollHarness(panes_by_host={"local": ["5"]}, fail_hosts=("local",))
    daemon = make_daemon_with(harness, capture)

    await daemon.update_agent_status(make_raw_agent(pane_id="5"), source="poll:local")
    assert "local:default:5" in daemon.agents_state

    daemon.running = True
    poll_task = asyncio.create_task(daemon.poll_herdr_agents())
    try:
        for _ in range(3):  # three consecutive FAILED cycles
            harness.release_cycle()
        await wait_for_cycles(harness, 3)
        await asyncio.sleep(0.05)

        assert "local:default:5" in daemon.agents_state
        assert capture.of_type("agent_removed") == []
    finally:
        stop_poll_task(daemon, poll_task)
        await reap(poll_task)


@pytest.mark.asyncio
async def test_failed_poll_bad_json_skips_reconciliation(isolated_config):
    """A successful exit code carrying unparseable JSON must also skip reconciliation."""
    CONFIG["remotes"] = []
    CONFIG["poll_interval"] = 0.05
    CONFIG["session_ttl"] = 90.0

    capture = BroadcastCapture()

    class GarbagePollHarness(PollHarness):
        async def __call__(self, args, host="local"):
            if args[:2] == ["agent", "list"]:
                self.cycles += 1
                return 0, "<<not json>>", ""
            return 0, "[]", ""

    harness = GarbagePollHarness()
    daemon = make_daemon_with(harness, capture)

    await daemon.update_agent_status(make_raw_agent(pane_id="8"), source="poll:local")

    daemon.running = True
    poll_task = asyncio.create_task(daemon.poll_herdr_agents())
    try:
        for _ in range(3):
            harness.release_cycle()
        await wait_for_cycles(harness, 3)
        await asyncio.sleep(0.05)

        assert "local:default:8" in daemon.agents_state
        assert capture.of_type("agent_removed") == []
    finally:
        stop_poll_task(daemon, poll_task)
        await reap(poll_task)


@pytest.mark.asyncio
async def test_ttl_expiry_removes_stale_agent(isolated_config):
    """An agent whose last_seen_at predates session_ttl is swept within one poll
    cycle and reported with reason 'expired'."""
    CONFIG["remotes"] = []
    CONFIG["poll_interval"] = 0.05
    CONFIG["session_ttl"] = 5.0  # ghost is hours stale; fresh agents stay far inside TTL

    capture = BroadcastCapture()
    harness = PollHarness(panes_by_host={"local": []})  # authoritative poll: nobody home
    daemon = make_daemon_with(harness, capture)

    # Inject a long-gone reporter directly into state (proper normalized shape)
    stale_seen = iso_utc(hours=-2)
    daemon.agents_state["local:ghost:7"] = {
        "id": "local:ghost:7",
        "host": "local",
        "workspace": "ghost",
        "tab": "",
        "pane_id": "7",
        "status": "working",
        "status_reason": "",
        "source": "udp:192.168.1.50",
        "last_seen_at": stale_seen,
        "updated_at": stale_seen,
        "metadata": {},
    }
    daemon.output_cursors["local:ghost:7"] = "deadbeefcursor"
    daemon.output_locks["local:ghost:7"] = asyncio.Lock()

    daemon.running = True
    poll_task = asyncio.create_task(daemon.poll_herdr_agents())
    try:
        harness.release_cycle()
        await wait_for_cycles(harness, 1)
        await wait_until(lambda: "local:ghost:7" not in daemon.agents_state)

        removals = capture.of_type("agent_removed")
        assert len(removals) == 1
        assert removals[0]["agent_id"] == "local:ghost:7"
        assert removals[0]["reason"] == "expired"
        assert removals[0]["pane_id"] == "7"

        # Resource purge applies to expiry-driven removals too
        assert "local:ghost:7" not in daemon.output_cursors
        assert "local:ghost:7" not in daemon.output_locks

        # A fresh agent must NOT be swept by the same TTL
        await daemon.update_agent_status(
            make_raw_agent(workspace="fresh", pane_id="9"), source="udp:fresh-host"
        )
        harness.release_cycle()
        await wait_for_cycles(harness, 2)
        await asyncio.sleep(0.05)
        assert "local:fresh:9" in daemon.agents_state
    finally:
        stop_poll_task(daemon, poll_task)
        await reap(poll_task)


@pytest.mark.asyncio
async def test_health_reports_agents_by_host(isolated_config):
    """/health includes agents_by_host (host -> count) and last_reconcile_at."""
    CONFIG["host"] = "127.0.0.1"
    CONFIG["port"] = 8396
    CONFIG["token"] = ""
    CONFIG["trusted_origins"] = []
    CONFIG["remotes"] = []
    CONFIG["poll_interval"] = 30.0
    CONFIG["output_interval"] = 30.0

    capture = BroadcastCapture()
    harness = PollHarness(panes_by_host={"local": []})  # keep background poll inert
    daemon = make_daemon_with(harness, capture)
    daemon.internal_ws_port = 8496

    await daemon.start()
    try:
        await daemon.update_agent_status(make_raw_agent(host="local", pane_id="11"), source="test")
        await daemon.update_agent_status(
            make_raw_agent(host="gpu-box", workspace="models", pane_id="22"), source="test"
        )

        health = await http_get_json(CONFIG["port"], "/health")

        assert health["service"] == "herdr-outpost-relay"
        assert health["agents_count"] == 2
        assert health["agents_by_host"] == {"local": 1, "gpu-box": 1}
        assert "last_reconcile_at" in health
    finally:
        await daemon.stop()
        # Yield so the UDP transport's connection_lost callback runs while the
        # loop is still alive; otherwise it fires at GC time and warns.
        await asyncio.sleep(0)
        await asyncio.sleep(0)


def test_session_ttl_config_default(isolated_config):
    """CONFIG carries session_ttl defaulting to the 90s contract value."""
    assert "session_ttl" in CONFIG, "CONFIG['session_ttl'] missing - SESSION_TTL wiring absent"
    assert float(CONFIG["session_ttl"]) == 90.0


def test_session_ttl_env_override():
    """SESSION_TTL env var overrides the default at module load time."""
    code = (
        "import json, os, sys;"
        f"sys.path.insert(0, {os.path.join(REPO_ROOT, 'relay')!r});"
        "from herdr_relay import CONFIG;"
        "print(json.dumps(float(CONFIG['session_ttl'])))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "SESSION_TTL": "12.5"},
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stderr}"
    assert proc.stdout.strip().endswith("12.5")
