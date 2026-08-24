"""Tests for relay/probes/opencode.py — root-session identity probe."""

from __future__ import annotations

import os
import sqlite3
import sys

# Ensure relay is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from probes import opencode


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
                model text,
                version text
            )
            """
        )
        con.executemany(
            "INSERT INTO session (id, model, version) VALUES (:id, :model, :version)",
            rows,
        )
        con.commit()
        con.close()
        monkeypatch.setattr(opencode, "OPENCODE_DB_PATH", str(db_path))
        return db_path

    return build


def _probe(root="ses_root"):
    return opencode.probe(
        harness="opencode",
        agent_session={"agent": "opencode", "kind": "id", "value": root},
    )


class TestProbe:
    def test_model_from_json_blob(self, opencode_db):
        opencode_db(
            [
                {
                    "id": "ses_root",
                    "model": '{"id":"x-preview-f-free","providerID":"opencode","variant":"default"}',
                    "version": "1.2.3",
                }
            ]
        )
        out = _probe()
        assert out == {"model": "x-preview-f-free", "harness_version": "1.2.3"}

    def test_bare_model_string_passthrough(self, opencode_db):
        opencode_db([{"id": "ses_root", "model": "big-pickle", "version": ""}])
        assert _probe() == {"model": "big-pickle"}

    def test_missing_root_session(self, opencode_db):
        opencode_db([{"id": "ses_other", "model": "big-pickle", "version": ""}])
        assert _probe() == {}

    def test_empty_and_null_model_degrade_to_empty(self, opencode_db):
        opencode_db(
            [
                {"id": "ses_a", "model": None, "version": None},
                {"id": "ses_b", "model": "", "version": ""},
            ]
        )
        assert _probe("ses_a") == {}
        assert _probe("ses_b") == {}

    def test_malformed_json_blob_yields_nothing(self, opencode_db):
        opencode_db([{"id": "ses_root", "model": "{not json", "version": "9.9.9"}])
        assert _probe() == {"harness_version": "9.9.9"}

    def test_non_opencode_harness_skipped(self, opencode_db):
        opencode_db([{"id": "ses_root", "model": "big-pickle", "version": ""}])
        assert opencode.probe(harness="claude") == {}
        assert opencode.probe(harness="", agent_session={"value": "ses_root"}) == {}

    def test_no_agent_session_means_no_claim(self, opencode_db):
        opencode_db([{"id": "ses_root", "model": "big-pickle", "version": ""}])
        assert opencode.probe(harness="opencode") == {}
        assert opencode.probe(harness="opencode", agent_session=None) == {}


class TestCleanModel:
    def test_variants_surface_bare_id(self):
        blob = '{"id":"gpt-5.6-luna","providerID":"opencode-go","variant":"high"}'
        assert opencode._clean_model(blob) == "gpt-5.6-luna"

    def test_plain_ids_unchanged(self):
        assert opencode._clean_model("deepseek-v4-flash") == "deepseek-v4-flash"
        assert opencode._clean_model("") == ""
        assert opencode._clean_model(None) == ""
