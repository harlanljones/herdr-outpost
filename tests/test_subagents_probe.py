"""Tests for relay/probes/subagents.py — per-harness subagent tree extraction."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

# Ensure relay is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from probes import claude_code as claude_code_probe
from probes import subagents


NOW = time.time()


def _ms(epoch_seconds: float) -> int:
    return int(epoch_seconds * 1000)


@pytest.fixture
def opencode_db(tmp_path, monkeypatch):
    """Build a fixture opencode SQLite store and point the probe at it."""

    def build(rows):
        db_path = tmp_path / "opencode.db"
        con = sqlite3.connect(db_path)
        con.execute(
            """
            CREATE TABLE session (
                id text PRIMARY KEY,
                parent_id text,
                title text,
                agent text,
                model text,
                tokens_input integer DEFAULT 0,
                tokens_output integer DEFAULT 0,
                time_updated integer
            )
            """
        )
        con.executemany(
            "INSERT INTO session (id, parent_id, title, agent, model,"
            " tokens_input, tokens_output, time_updated)"
            " VALUES (:id, :parent_id, :title, :agent, :model,"
            " :tokens_input, :tokens_output, :time_updated)",
            rows,
        )
        con.commit()
        con.close()
        monkeypatch.setattr(subagents, "OPENCODE_DB_PATH", str(db_path))
        return db_path

    return build


def sample_rows():
    return [
        # Root session itself (must never appear in the tree)
        {
            "id": "ses_root", "parent_id": None,
            "title": "Main session", "agent": "build",
            "model": '{"id":"sonnet-4","providerID":"opencode","variant":"default"}',
            "tokens_input": 1_000_000, "tokens_output": 5, "time_updated": _ms(NOW),
        },
        # Two children of the root; the @kind is embedded in the title.
        {
            "id": "ses_child_a", "parent_id": "ses_root",
            "title": "Explore TUI structure (@explore subagent)", "agent": "explore",
            "model": '{"id":"sonnet-4","providerID":"opencode","variant":"default"}',
            "tokens_input": 1000, "tokens_output": 200, "time_updated": _ms(NOW - 30),
        },
        {
            "id": "ses_child_b", "parent_id": "ses_root",
            "title": "Plain child without kind marker", "agent": "build",
            "model": "grok-3",
            "tokens_input": 500, "tokens_output": 0, "time_updated": _ms(NOW - 9999),
        },
        # Grandchild: nested subagents must nest recursively.
        {
            "id": "ses_grandchild", "parent_id": "ses_child_a",
            "title": "Deep dive (@general subagent)", "agent": "general",
            "model": "", "tokens_input": 10, "tokens_output": 15,
            "time_updated": _ms(NOW - 60),
        },
        # Unrelated session elsewhere in the store.
        {
            "id": "ses_other_tree", "parent_id": "ses_unrelated",
            "title": "Other (@general subagent)", "agent": "general", "model": "",
            "tokens_input": 0, "tokens_output": 0, "time_updated": _ms(NOW),
        },
    ]


class TestOpencodeTree:
    def test_children_nested_under_root(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(
            harness="opencode", agent_session={"value": "ses_root"}
        )
        assert out["session_id"] == "ses_root"
        ids = {n["id"] for n in out["subagents"]}
        assert ids == {"ses_child_a", "ses_child_b"}
        assert "ses_root" not in ids
        assert "ses_other_tree" not in ids

    def test_nested_grandchildren(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_root"})
        child_a = next(n for n in out["subagents"] if n["id"] == "ses_child_a")
        assert [c["id"] for c in child_a["children"]] == ["ses_grandchild"]

    def test_kind_extracted_from_title_marker(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_root"})
        kinds = {n["id"]: n["kind"] for n in out["subagents"]}
        assert kinds["ses_child_a"] == "explore"
        assert kinds["ses_child_b"] == ""

    def test_model_json_blob_reduced_to_bare_id(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_root"})
        models = {n["id"]: n["model"] for n in out["subagents"]}
        assert models["ses_child_a"] == "sonnet-4"
        # Plain strings pass through untouched
        assert models["ses_child_b"] == "grok-3"

    def test_tokens_are_honest_input_plus_output_sums(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_root"})
        tokens = {n["id"]: n["tokens"] for n in out["subagents"]}
        assert tokens["ses_child_a"] == 1200
        assert tokens["ses_child_b"] == 500

    def test_active_reflects_recent_writes_only(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_root"})
        activity = {n["id"]: n["active"] for n in out["subagents"]}
        # Written 30s ago -> inside ACTIVE_WINDOW_SECONDS; ~2.7h ago -> quiet.
        assert activity["ses_child_a"] is True
        assert activity["ses_child_b"] is False

    def test_most_recently_written_sibling_first(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_root"})
        assert [n["id"] for n in out["subagents"]] == ["ses_child_a", "ses_child_b"]

    def test_unknown_root_yields_empty_tree_not_error(self, opencode_db):
        opencode_db(sample_rows())
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_nope"})
        assert out == {"session_id": "ses_nope", "subagents": []}

    def test_missing_agent_session_value_returns_empty(self, opencode_db):
        opencode_db(sample_rows())
        assert subagents.probe(harness="opencode") == {}
        assert subagents.probe(harness="opencode", agent_session=None) == {}

    def test_missing_db_file_degrades_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            subagents, "OPENCODE_DB_PATH", str(tmp_path / "absent.db")
        )
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_x"})
        assert out == {"session_id": "ses_x", "subagents": []}

    def test_corrupt_db_never_raises(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.db"
        bad.write_bytes(b"this is not sqlite" * 64)
        monkeypatch.setattr(subagents, "OPENCODE_DB_PATH", str(bad))
        # Degrades to {} ("nothing measurable"), never raises into the poll loop.
        assert subagents.probe(harness="opencode", agent_session={"value": "ses_x"}) == {}


class TestClaudeTree:
    @pytest.fixture
    def claude_projects(self, tmp_path, monkeypatch):
        """Fixture ~/.claude/projects layout; returns the project dir path."""
        projects = tmp_path / "projects"
        projects.mkdir()
        monkeypatch.setattr(claude_code_probe, "CLAUDE_PROJECTS_DIR", str(projects))
        return projects

    def _write_sidechain(self, projects, cwd_slug, session_id, agent_id, prompt, mtime):
        subagents_dir = projects / cwd_slug / session_id / "subagents"
        subagents_dir.mkdir(parents=True, exist_ok=True)
        path = subagents_dir / f"agent-{agent_id}.jsonl"
        entries = [
            {"type": "user", "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
        os.utime(path, (mtime, mtime))
        # Keep the parent dir's mtime fresh enough to be picked as root.
        os.utime(projects / cwd_slug / session_id, (mtime + 5, mtime + 5))
        return path

    def test_sidechain_layout_produces_tree(self, claude_projects):
        slug = "-home-harlan-dev-demo"
        self._write_sidechain(
            claude_projects, slug, "sess-parent-1", "abc123", "Implement feature X", NOW - 30
        )
        out = subagents.probe(cwd="/home/harlan/dev/demo", harness="claude")
        assert out["session_id"] == "sess-parent-1"
        assert [n["id"] for n in out["subagents"]] == ["abc123"]
        node = out["subagents"][0]
        assert node["title"].startswith("Implement feature X")
        assert node["active"] is True
        assert node["children"] == []

    def test_newest_session_dir_wins_without_pid(self, claude_projects):
        slug = "-home-harlan-dev-demo"
        self._write_sidechain(
            claude_projects, slug, "sess-old", "old1", "old task", NOW - 86_400
        )
        self._write_sidechain(
            claude_projects, slug, "sess-new", "new1", "fresh task", NOW - 10
        )
        out = subagents.probe(cwd="/home/harlan/dev/demo", harness="claude")
        assert out["session_id"] == "sess-new"
        assert [n["id"] for n in out["subagents"]] == ["new1"]

    def test_no_subagents_dir_means_no_claim(self, claude_projects):
        # Project dir exists but no <sessionId>/subagents/ anywhere.
        (claude_projects / "-home-harlan-dev-solo").mkdir()
        assert subagents.probe(cwd="/home/harlan/dev/solo", harness="claude") == {}

    def test_stale_sidechain_title_falls_back_to_id(self, claude_projects):
        slug = "-home-harlan-dev-demo"
        subagents_dir = claude_projects / slug / "sess-x" / "subagents"
        subagents_dir.mkdir(parents=True)
        path = subagents_dir / "agent-zz9.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        out = subagents.probe(cwd="/home/harlan/dev/demo", harness="claude")
        assert out["subagents"][0]["id"] == "zz9"


class TestProbeContract:
    def test_unsupported_harnesses_return_empty(self, opencode_db):
        opencode_db(sample_rows())  # data exists; cline simply has no parent links
        assert subagents.probe(harness="cline") == {}
        assert subagents.probe(harness="antigravity") == {}
        assert subagents.probe(harness="") == {}

    def test_long_titles_are_clamped(self, opencode_db):
        long_title = "x" * 500 + " (@general subagent)"
        opencode_db([{
            "id": "ses_r", "parent_id": None, "title": "root", "agent": "",
            "model": "", "tokens_input": 0, "tokens_output": 0, "time_updated": _ms(NOW),
        }, {
            "id": "ses_c", "parent_id": "ses_r", "title": long_title, "agent": "",
            "model": "", "tokens_input": 0, "tokens_output": 0, "time_updated": _ms(NOW),
        }])
        out = subagents.probe(harness="opencode", agent_session={"value": "ses_r"})
        assert len(out["subagents"][0]["title"]) <= subagents.TITLE_CLAMP_CHARS
