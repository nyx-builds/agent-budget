"""Tests for v0.12.0 — Session Cost Importer."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

from agent_budget.session_importer import (
    SessionImportResult,
    HermesSessionImporter,
    JSONLTranscriptImporter,
    RequestDumpImporter,
    discover_session_sources,
    _safe_int,
    _safe_float,
    _parse_timestamp,
)
from agent_budget.llm_costs import PriceCatalog, ModelProvider, ModelPrice


class TestSafeHelpers:
    def test_safe_int_none(self):
        assert _safe_int(None) == 0

    def test_safe_int_valid(self):
        assert _safe_int("42") == 42

    def test_safe_int_invalid(self):
        assert _safe_int("abc") == 0

    def test_safe_int_negative(self):
        assert _safe_int(-5) == -5

    def test_safe_float_none(self):
        assert _safe_float(None) == 0.0

    def test_safe_float_valid(self):
        assert _safe_float("3.14") == 3.14

    def test_safe_float_invalid(self):
        assert _safe_float("not-a-float") == 0.0


class TestParseTimestamp:
    def test_iso_format_with_z(self):
        ts = _parse_timestamp("2026-07-14T10:30:00Z")
        assert ts is not None
        assert ts.year == 2026
        assert ts.month == 7
        assert ts.day == 14

    def test_iso_format_with_fractional(self):
        ts = _parse_timestamp("2026-07-14T10:30:00.123456Z")
        assert ts is not None
        assert ts.year == 2026

    def test_iso_format_no_z(self):
        ts = _parse_timestamp("2026-07-14T10:30:00")
        assert ts is not None
        assert ts.year == 2026

    def test_unix_timestamp(self):
        ts = _parse_timestamp(1720000000)
        assert ts is not None
        assert ts.year == 2024

    def test_none_returns_none(self):
        assert _parse_timestamp(None) is None

    def test_invalid_string(self):
        assert _parse_timestamp("not-a-date") is None

    def test_space_format(self):
        ts = _parse_timestamp("2026-07-14 10:30:00")
        assert ts is not None
        assert ts.year == 2026


class TestSessionImportResult:
    def test_to_dict(self):
        r = SessionImportResult(
            sessions_found=5,
            sessions_imported=3,
            sessions_skipped=2,
            total_input_tokens=10000,
            total_output_tokens=5000,
            total_cost_usd=0.123456,
            models_seen={"gpt-4o", "claude-sonnet"},
        )
        d = r.to_dict()
        assert d["sessions_found"] == 5
        assert d["sessions_imported"] == 3
        assert d["sessions_skipped"] == 2
        assert d["total_input_tokens"] == 10000
        assert d["total_cost_usd"] == 0.123456
        assert "gpt-4o" in d["models_seen"]
        assert d["record_count"] == 0

    def test_defaults(self):
        r = SessionImportResult()
        assert r.sessions_found == 0
        assert r.sessions_imported == 0
        assert r.total_cost_usd == 0.0
        assert len(r.records) == 0


class TestHermesSessionImporter:
    @pytest.fixture
    def catalog(self):
        prices = {
            "gpt-4o": ModelPrice(
                model_id="gpt-4o",
                provider=ModelProvider.OPENAI,
                input_price_per_mtok=2.5,
                output_price_per_mtok=10.0,
                cache_read_price_per_mtok=1.25,
            ),
            "claude-sonnet-4-20250514": ModelPrice(
                model_id="claude-sonnet-4-20250514",
                provider=ModelProvider.ANTHROPIC,
                input_price_per_mtok=3.0,
                output_price_per_mtok=15.0,
                cache_read_price_per_mtok=0.3,
            ),
        }
        return PriceCatalog(custom_prices=prices)

    @pytest.fixture
    def db_path(self, tmp_path):
        db = tmp_path / "state.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            model TEXT,
            source TEXT,
            user_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            reasoning_tokens INTEGER,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            cost_status TEXT,
            started_at TEXT,
            ended_at TEXT,
            cwd TEXT,
            git_repo_root TEXT,
            billing_provider TEXT,
            message_count INTEGER,
            tool_call_count INTEGER,
            api_call_count INTEGER
        )""")
        conn.execute("""INSERT INTO sessions VALUES (
            'sess-1', 'gpt-4o', 'cli', 'user-1',
            1000, 500, 200, 50, 0,
            0.005, 0.0075, 'actual',
            '2026-07-14T10:00:00Z', '2026-07-14T10:05:00Z',
            '/tmp/project', '/tmp/project', 'openai',
            10, 3, 5
        )""")
        conn.execute("""INSERT INTO sessions VALUES (
            'sess-2', 'claude-sonnet-4-20250514', 'web', 'user-2',
            2000, 1000, 0, 0, 0,
            0.0, 0.0, 'estimated',
            '2026-07-14T11:00:00Z', '2026-07-14T11:10:00Z',
            '/tmp/project2', '/tmp/project2', 'anthropic',
            20, 5, 10
        )""")
        conn.execute("""INSERT INTO sessions VALUES (
            'sess-3', 'gpt-4o', 'cli', 'user-1',
            0, 0, 0, 0, 0,
            0.0, 0.0, 'estimated',
            '2026-07-14T12:00:00Z', '2026-07-14T12:01:00Z',
            NULL, NULL, 'openai', 0, 0, 0
        )""")
        conn.commit()
        conn.close()
        return str(db)

    def test_discover(self, db_path):
        importer = HermesSessionImporter(db_path)
        sessions = importer.discover()
        # sess-3 has 0 tokens, filtered out by WHERE clause
        assert len(sessions) == 2
        assert sessions[0]["id"] in ("sess-1", "sess-2")

    def test_import_sessions(self, db_path, catalog):
        importer = HermesSessionImporter(db_path)
        result = importer.import_sessions(catalog=catalog)
        assert result.sessions_found == 2
        assert result.sessions_imported == 2
        assert result.sessions_skipped == 0
        assert result.total_input_tokens == 3000
        assert result.total_output_tokens == 1500
        # sess-1 has actual_cost_usd = 0.0075
        # sess-2 has no actual, so estimated from catalog
        assert result.total_cost_usd > 0.0075
        assert "gpt-4o" in result.models_seen
        assert "claude-sonnet-4-20250514" in result.models_seen
        assert len(result.records) == 2

    def test_import_dry_run(self, db_path, catalog):
        importer = HermesSessionImporter(db_path)
        result = importer.import_sessions(catalog=catalog, dry_run=True)
        assert result.sessions_imported == 2
        assert len(result.records) == 0  # dry run doesn't produce records

    def test_import_with_agent_id(self, db_path, catalog):
        importer = HermesSessionImporter(db_path)
        result = importer.import_sessions(catalog=catalog, agent_id="my-agent")
        assert all(r.agent_id == "my-agent" for r in result.records)

    def test_import_with_since_filter(self, db_path, catalog):
        importer = HermesSessionImporter(db_path)
        since = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
        result = importer.import_sessions(catalog=catalog, since=since)
        # sess-1 (10:00) should be skipped, sess-2 (11:00) imported
        assert result.sessions_imported == 1
        assert result.sessions_skipped == 1

    def test_invalid_db_path(self, catalog):
        importer = HermesSessionImporter("/nonexistent/path.db")
        result = importer.import_sessions(catalog=catalog)
        assert len(result.errors) > 0

    def test_discover_invalid_db(self):
        importer = HermesSessionImporter("/nonexistent/path.db")
        with pytest.raises(ValueError):
            importer.discover()

    def test_actual_cost_used_over_estimate(self, db_path, catalog):
        importer = HermesSessionImporter(db_path)
        result = importer.import_sessions(catalog=catalog)
        # Find sess-1 record
        sess1 = [r for r in result.records if r.task_id == "sess-1"][0]
        assert sess1.cost_usd == 0.0075  # actual_cost, not estimated


class TestJSONLTranscriptImporter:
    @pytest.fixture
    def catalog(self):
        prices = {
            "gpt-4o": ModelPrice(
                model_id="gpt-4o",
                provider=ModelProvider.OPENAI,
                input_price_per_mtok=2.5,
                output_price_per_mtok=10.0,
            ),
        }
        return PriceCatalog(custom_prices=prices)

    @pytest.fixture
    def jsonl_path(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({
                "model": "gpt-4o",
                "usage": {"input_tokens": 500, "output_tokens": 200},
                "timestamp": "2026-07-14T10:00:00Z",
            }),
            json.dumps({
                "model": "gpt-4o",
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
                "created_at": "2026-07-14T11:00:00Z",
            }),
            "",  # empty line should be skipped
            json.dumps({
                "model": "gpt-4o",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }),  # zero tokens, skipped
            "invalid json line",  # bad JSON, skipped
        ]
        path.write_text("\n".join(lines))
        return str(path)

    def test_import_transcript(self, jsonl_path, catalog):
        importer = JSONLTranscriptImporter(jsonl_path)
        result = importer.import_transcript(catalog=catalog)
        assert result.sessions_found == 2
        assert result.sessions_imported == 2
        assert result.total_input_tokens == 1500
        assert result.total_output_tokens == 700
        assert len(result.records) == 2

    def test_import_dry_run(self, jsonl_path, catalog):
        importer = JSONLTranscriptImporter(jsonl_path)
        result = importer.import_transcript(catalog=catalog, dry_run=True)
        assert result.sessions_imported == 2
        assert len(result.records) == 0

    def test_import_with_agent_id(self, jsonl_path, catalog):
        importer = JSONLTranscriptImporter(jsonl_path)
        result = importer.import_transcript(catalog=catalog, agent_id="test-agent")
        assert all(r.agent_id == "test-agent" for r in result.records)

    def test_file_not_found(self, catalog):
        importer = JSONLTranscriptImporter("/nonexistent/file.jsonl")
        result = importer.import_transcript(catalog=catalog)
        assert len(result.errors) > 0
        assert "not found" in result.errors[0].lower()

    def test_prompt_completion_tokens(self, jsonl_path, catalog):
        """Verify that prompt_tokens/completion_tokens field names are supported."""
        importer = JSONLTranscriptImporter(jsonl_path)
        result = importer.import_transcript(catalog=catalog)
        # Second entry uses prompt_tokens/completion_tokens
        records = sorted(result.records, key=lambda r: r.input_tokens)
        assert records[0].input_tokens == 500
        assert records[1].input_tokens == 1000


class TestRequestDumpImporter:
    @pytest.fixture
    def catalog(self):
        prices = {
            "gpt-4o": ModelPrice(
                model_id="gpt-4o",
                provider=ModelProvider.OPENAI,
                input_price_per_mtok=2.5,
                output_price_per_mtok=10.0,
            ),
        }
        return PriceCatalog(custom_prices=prices)

    @pytest.fixture
    def dump_dir(self, tmp_path):
        # Create a few dump files
        for i in range(3):
            data = {
                "session_id": f"dump-{i}",
                "timestamp": "2026-07-14T10:00:00Z",
                "request": {
                    "body": {
                        "model": "gpt-4o",
                        "messages": [
                            {"role": "user", "content": "Hello " * 100},
                        ],
                    }
                },
                "response": {
                    "usage": {
                        "input_tokens": 500 + i * 100,
                        "output_tokens": 200 + i * 50,
                    }
                },
            }
            (tmp_path / f"dump_20260714_10000{i}.json").write_text(json.dumps(data))

        # A file with no usage data (will estimate from messages)
        data_no_usage = {
            "session_id": "dump-no-usage",
            "timestamp": "2026-07-14T11:00:00Z",
            "request": {
                "body": {
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "user", "content": "x" * 400},
                    ],
                }
            },
        }
        (tmp_path / "dump_no_usage.json").write_text(json.dumps(data_no_usage))

        # An invalid JSON file (should be skipped)
        (tmp_path / "bad.json").write_text("not valid json")

        return str(tmp_path)

    def test_import_dumps(self, dump_dir, catalog):
        importer = RequestDumpImporter(dump_dir)
        result = importer.import_dumps(catalog=catalog)
        # 3 with usage + 1 estimated from messages + 1 bad JSON skipped
        assert result.sessions_imported >= 3
        assert result.total_cost_usd > 0

    def test_import_dumps_dry_run(self, dump_dir, catalog):
        importer = RequestDumpImporter(dump_dir)
        result = importer.import_dumps(catalog=catalog, dry_run=True)
        assert len(result.records) == 0

    def test_import_with_agent_id(self, dump_dir, catalog):
        importer = RequestDumpImporter(dump_dir)
        result = importer.import_dumps(catalog=catalog, agent_id="dump-agent")
        assert all(r.agent_id == "dump-agent" for r in result.records)

    def test_directory_not_found(self, catalog):
        importer = RequestDumpImporter("/nonexistent/dir")
        result = importer.import_dumps(catalog=catalog)
        assert len(result.errors) > 0
        assert "not found" in result.errors[0].lower()

    def test_since_filter(self, dump_dir, catalog):
        importer = RequestDumpImporter(dump_dir)
        since = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
        result = importer.import_dumps(catalog=catalog, since=since)
        # Only dump-no-usage (11:00) should pass the filter
        assert all(r.recorded_at >= since for r in result.records)


class TestDiscoverSessionSources:
    def test_returns_list(self):
        """discover_session_sources should return a list (possibly empty)."""
        sources = discover_session_sources()
        assert isinstance(sources, list)

    def test_finds_known_db(self, tmp_path, monkeypatch):
        """When a state.db exists at a known path, it should be found."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        (fake_home / "state.db").write_text("dummy")

        monkeypatch.setattr(Path, "home", lambda: fake_home)

        sources = discover_session_sources()
        db_sources = [s for s in sources if s["type"] == "hermes_sqlite"]
        assert len(db_sources) >= 1
