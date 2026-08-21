"""Tests for relay/agent_state.py state tracking, merging, snapshots, and normalization."""

from __future__ import annotations

import datetime
import os
import sys

# Ensure relay module is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from agent_state import (
    DEFAULT_SESSION_TTL_SECONDS,
    RECONCILE_GRACE,
    VALID_STATUSES,
    STATUS_MAP,
    agent_removed_message,
    agent_update_message,
    agents_snapshot_message,
    apply_agent_message,
    build_agent_id,
    complete_agent_update_message,
    find_expired_agents,
    get_default_hostname,
    normalize_agent_dict,
    normalize_status,
    parse_agent_id,
    reconcile_agent_state,
)


def iso_utc(**offset_kwargs) -> str:
    """ISO-8601 UTC timestamp offset from now by the given timedelta kwargs."""
    return (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**offset_kwargs)
    ).isoformat()


def make_current_agent(agent_id: str) -> dict:
    host, workspace, pane_id = parse_agent_id(agent_id)
    return {
        "id": agent_id,
        "host": host,
        "workspace": workspace,
        "pane_id": pane_id,
        "status": "working",
    }


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


class TestNormalizeLivenessFields:
    """normalize_agent_dict must stamp relay-local liveness metadata."""

    def test_normalize_defaults_source_and_last_seen_at(self):
        normalized = normalize_agent_dict({"host": "h", "workspace": "w", "pane_id": "1"})

        assert normalized["source"] == ""
        assert isinstance(normalized["last_seen_at"], str) and normalized["last_seen_at"]
        parsed = datetime.datetime.fromisoformat(normalized["last_seen_at"])
        assert parsed.tzinfo is not None  # ISO UTC (aware)
        # Stamped at normalization time, not taken from the payload
        assert abs((datetime.datetime.now(datetime.timezone.utc) - parsed).total_seconds()) < 30

    def test_normalize_source_passthrough_when_supplied(self):
        normalized = normalize_agent_dict(
            {"host": "h", "workspace": "w", "pane_id": "1", "source": "poll:local"}
        )
        assert normalized["source"] == "poll:local"

    def test_last_seen_at_not_sourced_from_payload_timestamps(self):
        """last_seen_at is relay-local observation time; payload updated_at/ts must not become it."""
        raw = {
            "host": "h",
            "workspace": "w",
            "pane_id": "1",
            "updated_at": iso_utc(hours=-5),
            "ts": iso_utc(hours=-6),
        }
        normalized = normalize_agent_dict(raw)
        assert normalized["updated_at"] == raw["updated_at"]
        assert normalized["last_seen_at"] != raw["updated_at"]


class TestReconcileAgentState:
    """Test host-scoped reconciliation with consecutive-miss grace."""

    def test_constants(self):
        assert RECONCILE_GRACE == 2
        assert DEFAULT_SESSION_TTL_SECONDS == 90.0

    def test_prune_after_grace_consecutive_misses(self):
        current = {
            "local:ws:1": make_current_agent("local:ws:1"),
            "local:ws:2": make_current_agent("local:ws:2"),
        }
        counts: dict = {}

        # First miss: below grace, survives with counter bumped
        # (the seen agent's counter resets to an explicit 0)
        pruned, counts = reconcile_agent_state(current, {"local:ws:1"}, "local", counts)
        assert pruned == []
        assert counts["local:ws:2"] == 1
        assert counts["local:ws:1"] == 0

        # Second consecutive miss: grace reached -> pruned and dropped from counts
        pruned, counts = reconcile_agent_state(current, {"local:ws:1"}, "local", counts)
        assert pruned == ["local:ws:2"]
        assert "local:ws:2" not in counts
        assert counts == {"local:ws:1": 0}

    def test_no_prune_below_grace_single_miss(self):
        current = {"local:ws:1": make_current_agent("local:ws:1")}
        pruned, counts = reconcile_agent_state(current, set(), "local", {})
        assert pruned == []
        assert counts == {"local:ws:1": 1}

    def test_counter_resets_when_seen_again_mid_streak(self):
        """miss once, seen again, miss once -> agent still alive (grace holds)."""
        current = {"local:ws:1": make_current_agent("local:ws:1")}

        pruned, counts = reconcile_agent_state(current, set(), "local", {})
        assert pruned == [] and counts == {"local:ws:1": 1}

        pruned, counts = reconcile_agent_state(current, {"local:ws:1"}, "local", counts)
        assert pruned == [] and counts == {"local:ws:1": 0}

        pruned, counts = reconcile_agent_state(current, set(), "local", counts)
        assert pruned == [] and counts == {"local:ws:1": 1}
        assert "local:ws:1" in current

    def test_host_scoping_ignores_other_hosts(self):
        """An agent on another host is never reset nor pruned by this host's poll,
        even if its composite id somehow appears in polled_ids."""
        # Reconciling host local must not count/reset/prune remote agents
        current = {
            "local:ws:2": make_current_agent("local:ws:2"),
            "remote:ws:9": make_current_agent("remote:ws:9"),
        }
        counts: dict = {}
        all_pruned: list = []
        for _ in range(RECONCILE_GRACE + 1):
            pruned, counts = reconcile_agent_state(current, {"remote:ws:9"}, "local", counts)
            all_pruned.extend(pruned)

        assert "local:ws:2" in all_pruned        # local agent hit grace within the cycles
        assert "remote:ws:9" not in counts       # remote agent never tracked by this poll
        assert "remote:ws:9" not in all_pruned   # ...and never pruned/reset
        assert "remote:ws:9" in current

    def test_garbage_collects_stale_counters(self):
        """Counters for ids no longer present in `current` are dropped."""
        current = {"local:ws:1": make_current_agent("local:ws:1")}
        stale_counts = {"local:gone:5": 3, "remote:x:2": 1}

        pruned, counts = reconcile_agent_state(current, {"local:ws:1"}, "local", stale_counts)
        assert pruned == []
        assert counts == {"local:ws:1": 0}

    def test_purity_does_not_mutate_inputs(self):
        current = {
            "local:ws:1": make_current_agent("local:ws:1"),
            "local:ws:2": make_current_agent("local:ws:2"),
        }
        snapshot = {k: dict(v) for k, v in current.items()}
        counts = {"local:ws:2": 1}

        pruned, next_counts = reconcile_agent_state(current, set(), "local", counts)

        assert pruned == ["local:ws:2"]
        # current untouched
        assert current == snapshot
        assert len(current) == 2
        # input counts untouched; results are new objects
        assert counts == {"local:ws:2": 1}
        assert next_counts is not counts

    def test_seen_agents_reset_to_zero(self):
        current = {"local:ws:1": make_current_agent("local:ws:1")}
        pruned, counts = reconcile_agent_state(current, {"local:ws:1"}, "local", {"local:ws:1": 1})
        assert pruned == []
        assert counts == {"local:ws:1": 0}

    def test_custom_grace_honored(self):
        current = {"local:ws:1": make_current_agent("local:ws:1")}
        counts = {}
        for i in range(3):
            pruned, counts = reconcile_agent_state(current, set(), "local", counts, grace=4)
            assert pruned == []
            assert counts == {"local:ws:1": i + 1}
        pruned, counts = reconcile_agent_state(current, set(), "local", counts, grace=4)
        assert pruned == ["local:ws:1"]
        assert counts == {}


class TestFindExpiredAgents:
    """Test TTL-based expiry sweep over last observation timestamps."""

    def test_fresh_agent_survives(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        current = {
            "local:w:1": {**make_current_agent("local:w:1"), "last_seen_at": iso_utc(seconds=-10)}
        }
        assert find_expired_agents(current, ttl_seconds=90.0, now=now) == []

    def test_stale_last_seen_at_expires(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        current = {
            "local:w:1": {**make_current_agent("local:w:1"), "last_seen_at": iso_utc(seconds=-301)},
            "local:w:2": {**make_current_agent("local:w:2"), "last_seen_at": iso_utc(seconds=-89)},
        }
        expired = find_expired_agents(current, ttl_seconds=90.0, now=now)
        assert expired == ["local:w:1"]

    def test_falls_back_to_updated_at(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        current = {
            "local:w:1": {**make_current_agent("local:w:1"), "updated_at": iso_utc(minutes=-5)},
            "local:w:2": {**make_current_agent("local:w:2"), "updated_at": iso_utc(seconds=-1)},
        }
        assert find_expired_agents(current, ttl_seconds=90.0, now=now) == ["local:w:1"]

    def test_last_seen_at_wins_over_updated_at(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        current = {
            "local:w:1": {
                **make_current_agent("local:w:1"),
                "updated_at": iso_utc(hours=-2),      # stale...
                "last_seen_at": iso_utc(seconds=-5),  # ...but recently observed
            },
        }
        assert find_expired_agents(current, ttl_seconds=90.0, now=now) == []

    @pytest.mark.parametrize("bad_ts", [None, "", "not-a-timestamp", 12345, [], {}])
    def test_missing_or_garbage_timestamps_never_expire(self, bad_ts):
        now = datetime.datetime.now(datetime.timezone.utc)
        entry = make_current_agent("local:w:1")
        if bad_ts is not None:
            entry["last_seen_at"] = bad_ts
        current = {"local:w:1": entry}

        assert find_expired_agents(current, ttl_seconds=0.001, now=now) == []

    def test_naive_timestamp_treated_as_utc(self):
        """A timestamp without tzinfo must be interpreted as UTC, not local time."""
        now = datetime.datetime.now(datetime.timezone.utc)
        naive_utc_str = (
            (now - datetime.timedelta(seconds=600))
            .replace(tzinfo=None)  # strip tz -> naive UTC wall clock
            .isoformat()
        )
        current = {"local:w:1": {**make_current_agent("local:w:1"), "last_seen_at": naive_utc_str}}

        # 600s old > 90s ttl when read as UTC -> expires regardless of machine TZ
        assert find_expired_agents(current, ttl_seconds=90.0, now=now) == ["local:w:1"]

    def test_custom_ttl_honored(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        current = {
            "local:w:1": {**make_current_agent("local:w:1"), "last_seen_at": iso_utc(seconds=-15)},
            "local:w:2": {**make_current_agent("local:w:2"), "last_seen_at": iso_utc(seconds=-25)},
        }

        assert find_expired_agents(current, ttl_seconds=20.0, now=now) == ["local:w:2"]
        assert find_expired_agents(current, ttl_seconds=10.0, now=now) == ["local:w:1", "local:w:2"]
        assert find_expired_agents(current, ttl_seconds=100.0, now=now) == []

    def test_deterministic_sorted_output(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        current = {
            "local:w:c": {**make_current_agent("local:w:c"), "last_seen_at": iso_utc(hours=-3)},
            "remote:h:a": {**make_current_agent("remote:h:a"), "last_seen_at": iso_utc(days=-1)},
            "local:w:b": {**make_current_agent("local:w:b"), "last_seen_at": iso_utc(hours=-1)},
        }
        expired = find_expired_agents(current, ttl_seconds=90.0, now=now)

        assert expired == sorted(expired)
        assert expired == ["local:w:b", "local:w:c", "remote:h:a"]

    def test_default_ttl_value(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        boundary = now - datetime.timedelta(seconds=DEFAULT_SESSION_TTL_SECONDS + 1)
        current = {"local:w:1": {**make_current_agent("local:w:1"), "last_seen_at": boundary.isoformat()}}

        # No explicit ttl/now beyond the timestamp: uses DEFAULT_SESSION_TTL_SECONDS
        assert find_expired_agents(current, now=now) == ["local:w:1"]


class TestAgentRemovedMessage:
    """Test the canonical agent_removed broadcast shape."""

    def test_shape_and_fields_default_reason(self):
        before = datetime.datetime.now(datetime.timezone.utc)
        msg = agent_removed_message("local:core:pane_3")

        assert msg["type"] == "agent_removed"
        assert msg["agent_id"] == "local:core:pane_3"
        assert msg["host"] == "local"
        assert msg["workspace"] == "core"
        assert msg["pane_id"] == "pane_3"
        assert msg["reason"] == "closed"
        ts = datetime.datetime.fromisoformat(msg["timestamp"])
        assert ts.tzinfo is not None
        assert before <= ts <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=5)

    def test_composite_id_parsing_variants(self):
        two_part = agent_removed_message("gpu-box:sessions")
        assert (two_part["host"], two_part["workspace"], two_part["pane_id"]) == ("gpu-box", "default", "sessions")

        single_part = agent_removed_message("pane_only")
        assert (single_part["host"], single_part["workspace"], single_part["pane_id"]) == ("local", "default", "pane_only")

        colon_pane = agent_removed_message("host:ws:pane:sub:1")
        assert colon_pane["host"] == "host"
        assert colon_pane["workspace"] == "ws"
        assert colon_pane["pane_id"] == "pane:sub:1"

    def test_reason_coercion_expired(self):
        msg = agent_removed_message("local:ws:1", reason="expired")
        assert msg["reason"] == "expired"

    def test_reason_coercion_anything_else_becomes_closed(self):
        for reason in ("closed", "", "crashed", None, "EXPIRED", 42):
            msg = agent_removed_message("local:ws:1", reason=reason)
            assert msg["reason"] == "closed"


class TestMergeRefreshesLastSeenAt:
    """complete_agent_update_message refreshes last_seen_at on every touch."""

    def test_second_update_refreshes_last_seen_at_without_payload_timestamps(self):
        first_seen = iso_utc(minutes=-10)
        current_state = {
            "local:core:1": {
                "id": "local:core:1",
                "host": "local",
                "workspace": "core",
                "pane_id": "1",
                "status": "working",
                "last_seen_at": first_seen,
            }
        }

        event_one = {"host": "local", "workspace": "core", "pane_id": "1", "status": "working"}
        result_one = complete_agent_update_message(event_one, current=current_state)
        refreshed_one = result_one["agent"]["last_seen_at"]

        event_two = {"host": "local", "workspace": "core", "pane_id": "1", "status": "blocked"}
        result_two = complete_agent_update_message(event_two, current=current_state)
        refreshed_two = result_two["agent"]["last_seen_at"]

        # Payload omitted timestamps entirely: last_seen_at still moves forward
        assert refreshed_one != first_seen
        assert datetime.datetime.fromisoformat(refreshed_one).tzinfo is not None
        assert refreshed_two >= refreshed_one

    def test_touch_refreshes_both_timestamps(self):
        """Both observation clocks refresh on every touch: updated_at falls back to
        relay time when the payload omits timestamps, and last_seen_at is always ours."""
        original_updated = iso_utc(hours=-1)
        original_seen = iso_utc(minutes=-1)
        current_state = {
            "local:core:1": {
                **make_current_agent("local:core:1"),
                "updated_at": original_updated,
                "last_seen_at": original_seen,
            }
        }
        event = {"host": "local", "workspace": "core", "pane_id": "1", "status": "working"}

        merged = complete_agent_update_message(event, current=current_state)["agent"]

        assert merged["updated_at"] != original_updated
        assert merged["last_seen_at"] != original_seen
        for key in ("updated_at", "last_seen_at"):
            parsed = datetime.datetime.fromisoformat(merged[key])
            assert parsed.tzinfo is not None
            assert abs((datetime.datetime.now(datetime.timezone.utc) - parsed).total_seconds()) < 30

    def test_merge_preserves_source_from_current_when_payload_omits_it(self):
        current_state = {
            "local:core:1": {
                **make_current_agent("local:core:1"),
                "source": "poll:local",
                "last_seen_at": iso_utc(minutes=-1),
            }
        }
        event = {"host": "local", "workspace": "core", "pane_id": "1", "status": "done"}

        merged = complete_agent_update_message(event, current=current_state)["agent"]

        assert merged["source"] == "poll:local"

    def test_new_agent_gets_source_and_last_seen_at(self):
        event = {
            "host": "local",
            "workspace": "new",
            "pane_id": "7",
            "status": "working",
            "source": "udp:192.168.1.5",
        }
        merged = complete_agent_update_message(event)["agent"]

        assert merged["source"] == "udp:192.168.1.5"
        assert datetime.datetime.fromisoformat(merged["last_seen_at"]).tzinfo is not None
