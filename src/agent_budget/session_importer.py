"""Session cost importer for agent-budget v0.12.0.

Bridges the observability→enforcement gap: reads agent session data from
Hermes SQLite state.db, JSONL transcript files, and request dump JSON files,
then imports token usage + costs as LLMUsageRecords that can be synced to
budgets as expenses.

This is what CodeBurn (8589★) does for observability, but we close the loop
by feeding that data directly into budget enforcement, guardrails, and
loop detection.

Supported formats:
  - Hermes state.db (SQLite): reads sessions table with token/cost columns
  - JSONL transcripts (OpenAI/Anthropic format): one JSON object per line
  - Request dump JSON: Hermes API request dump files
  - Generic JSON: any JSON with model + usage fields
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from .llm_costs import LLMUsageRecord, PriceCatalog, ModelProvider


@dataclass
class SessionImportResult:
    """Result of importing session cost data."""

    sessions_found: int = 0
    sessions_imported: int = 0
    sessions_skipped: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float = 0.0
    models_seen: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    records: list[LLMUsageRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sessions_found": self.sessions_found,
            "sessions_imported": self.sessions_imported,
            "sessions_skipped": self.sessions_skipped,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "models_seen": sorted(self.models_seen),
            "errors": self.errors,
            "record_count": len(self.records),
        }


def _safe_int(val) -> int:
    """Safely convert to int, returning 0 for None/invalid."""
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    """Safely convert to float, returning 0.0 for None/invalid."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _parse_timestamp(val) -> Optional[datetime]:
    """Parse various timestamp formats into UTC datetime."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(val, str):
        # Try ISO format
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(val, fmt)
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
            except ValueError:
                continue
    return None


class HermesSessionImporter:
    """Import session data from a Hermes state.db SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def discover(self) -> list[dict]:
        """List available sessions without importing. Returns summary dicts."""
        sessions = []
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, model, source, user_id,
                          input_tokens, output_tokens,
                          cache_read_tokens, cache_write_tokens,
                          reasoning_tokens,
                          estimated_cost_usd, actual_cost_usd,
                          cost_status,
                          started_at, ended_at,
                          cwd, git_repo_root, billing_provider,
                          message_count, tool_call_count, api_call_count
                   FROM sessions
                   WHERE input_tokens > 0 OR output_tokens > 0
                   ORDER BY started_at DESC"""
            )
            for row in cursor.fetchall():
                sessions.append(dict(row))
        except sqlite3.Error as e:
            raise ValueError(f"Failed to read Hermes state.db: {e}")
        finally:
            if conn:
                conn.close()
        return sessions

    def import_sessions(
        self,
        catalog: PriceCatalog,
        agent_id: Optional[str] = None,
        since: Optional[datetime] = None,
        dry_run: bool = False,
    ) -> SessionImportResult:
        """Import Hermes sessions as LLMUsageRecords.

        Args:
            catalog: PriceCatalog for cost calculation
            agent_id: Override agent_id for all records
            since: Only import sessions started after this datetime
            dry_run: If True, don't return records (just count)

        Returns:
            SessionImportResult with discovered data
        """
        result = SessionImportResult()
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, model, source, user_id,
                          input_tokens, output_tokens,
                          cache_read_tokens, cache_write_tokens,
                          reasoning_tokens,
                          estimated_cost_usd, actual_cost_usd,
                          cost_status,
                          started_at, ended_at,
                          cwd, git_repo_root, billing_provider,
                          message_count, tool_call_count, api_call_count
                   FROM sessions
                   WHERE input_tokens > 0 OR output_tokens > 0
                   ORDER BY started_at DESC"""
            )
            for row in cursor.fetchall():
                row_dict = dict(row)
                result.sessions_found += 1

                # Parse timestamp
                started_at = _parse_timestamp(row_dict.get("started_at"))
                if started_at is None:
                    result.sessions_skipped += 1
                    result.errors.append(
                        f"Session {row_dict.get('id', '?')}: unparseable timestamp"
                    )
                    continue

                # Filter by date
                if since and started_at < since:
                    result.sessions_skipped += 1
                    continue

                model_id = row_dict.get("model") or "unknown"
                in_tok = _safe_int(row_dict.get("input_tokens"))
                out_tok = _safe_int(row_dict.get("output_tokens"))
                cache_r = _safe_int(row_dict.get("cache_read_tokens"))
                cache_w = _safe_int(row_dict.get("cache_write_tokens"))

                # Use actual cost if available, otherwise estimate
                actual = _safe_float(row_dict.get("actual_cost_usd"))
                if actual > 0:
                    cost = actual
                else:
                    cost = catalog.calculate_cost(
                        model_id, in_tok, out_tok, cache_r, cache_w
                    )

                result.total_input_tokens += in_tok
                result.total_output_tokens += out_tok
                result.total_cache_read_tokens += cache_r
                result.total_cost_usd += cost
                result.models_seen.add(model_id)
                result.sessions_imported += 1

                if not dry_run:
                    record = LLMUsageRecord(
                        model_id=model_id,
                        agent_id=agent_id or row_dict.get("source") or "hermes",
                        task_id=str(row_dict.get("id", "")),
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cache_read_tokens=cache_r,
                        cache_write_tokens=cache_w,
                        cost_usd=cost,
                        recorded_at=started_at,
                        metadata={
                            "source": "hermes_state_db",
                            "billing_provider": row_dict.get("billing_provider"),
                            "cwd": row_dict.get("cwd"),
                            "git_repo_root": row_dict.get("git_repo_root"),
                            "message_count": row_dict.get("message_count"),
                            "tool_call_count": row_dict.get("tool_call_count"),
                            "cost_status": row_dict.get("cost_status"),
                        },
                    )
                    result.records.append(record)

        except sqlite3.Error as e:
            result.errors.append(f"SQLite error: {e}")
        finally:
            if conn:
                conn.close()
        return result


class JSONLTranscriptImporter:
    """Import session data from JSONL transcript files.

    Supports OpenAI/Anthropic-style JSONL where each line is a JSON object
    with model, usage, and optionally timestamp fields.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path

    def import_transcript(
        self,
        catalog: PriceCatalog,
        agent_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> SessionImportResult:
        """Parse a JSONL transcript file into LLMUsageRecords."""
        result = SessionImportResult()
        path = Path(self.file_path)
        if not path.exists():
            result.errors.append(f"File not found: {self.file_path}")
            return result

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    model_id = entry.get("model") or entry.get("model_id") or "unknown"
                    usage = entry.get("usage") or {}
                    in_tok = _safe_int(
                        usage.get("input_tokens")
                        or usage.get("prompt_tokens")
                        or usage.get("inputTokens")
                    )
                    out_tok = _safe_int(
                        usage.get("output_tokens")
                        or usage.get("completion_tokens")
                        or usage.get("outputTokens")
                    )
                    cache_r = _safe_int(
                        usage.get("cache_read_tokens")
                        or usage.get("cache_read_input_tokens")
                        or usage.get("prompt_tokens_details", {}).get("cached_tokens")
                    )
                    cache_w = _safe_int(
                        usage.get("cache_write_tokens")
                        or usage.get("cache_creation_input_tokens")
                    )

                    if in_tok == 0 and out_tok == 0:
                        continue

                    result.sessions_found += 1
                    ts = _parse_timestamp(
                        entry.get("timestamp")
                        or entry.get("created_at")
                        or entry.get("created")
                    )
                    if ts is None:
                        ts = datetime.now(timezone.utc)

                    cost = catalog.calculate_cost(
                        model_id, in_tok, out_tok, cache_r, cache_w
                    )

                    result.total_input_tokens += in_tok
                    result.total_output_tokens += out_tok
                    result.total_cache_read_tokens += cache_r
                    result.total_cost_usd += cost
                    result.models_seen.add(model_id)
                    result.sessions_imported += 1

                    if not dry_run:
                        record = LLMUsageRecord(
                            model_id=model_id,
                            agent_id=agent_id or entry.get("agent_id") or "jsonl",
                            task_id=entry.get("session_id") or entry.get("id") or f"line-{line_num}",
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                            cache_read_tokens=cache_r,
                            cache_write_tokens=cache_w,
                            cost_usd=cost,
                            recorded_at=ts,
                            metadata={
                                "source": "jsonl_transcript",
                                "file": str(path.name),
                                "line": line_num,
                            },
                        )
                        result.records.append(record)

        except OSError as e:
            result.errors.append(f"File error: {e}")

        return result


class RequestDumpImporter:
    """Import from Hermes request dump JSON files.

    These are API request/response dumps with model, messages, and sometimes
    usage data.
    """

    def __init__(self, dir_path: str):
        self.dir_path = dir_path

    def import_dumps(
        self,
        catalog: PriceCatalog,
        agent_id: Optional[str] = None,
        since: Optional[datetime] = None,
        dry_run: bool = False,
    ) -> SessionImportResult:
        """Import all request dump JSON files from a directory."""
        result = SessionImportResult()
        dump_dir = Path(self.dir_path)
        if not dump_dir.exists() or not dump_dir.is_dir():
            result.errors.append(f"Directory not found: {self.dir_path}")
            return result

        for json_file in sorted(dump_dir.glob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            result.sessions_found += 1

            # Parse timestamp from filename or content
            ts = _parse_timestamp(data.get("timestamp"))
            if ts is None:
                # Try extracting from filename pattern: ..._YYYYMMDD_HHMMSS_...
                fname = json_file.stem
                # Look for date pattern in filename
                import re

                date_match = re.search(r"(\d{8})_(\d{6})", fname)
                if date_match:
                    try:
                        ts = datetime.strptime(
                            f"{date_match.group(1)}_{date_match.group(2)}",
                            "%Y%m%d_%H%M%S",
                        ).replace(tzinfo=timezone.utc)
                    except ValueError:
                        ts = datetime.now(timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)

            if since and ts < since:
                result.sessions_skipped += 1
                continue

            # Extract from request body
            request = data.get("request", {})
            body = request.get("body", {})
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    body = {}

            model_id = body.get("model") or "unknown"

            # Check for usage in response if present
            usage = data.get("response", {}).get("usage", {}) if isinstance(data.get("response"), dict) else {}
            in_tok = _safe_int(
                usage.get("input_tokens")
                or usage.get("prompt_tokens")
            )
            out_tok = _safe_int(
                usage.get("output_tokens")
                or usage.get("completion_tokens")
            )

            # If no usage data, try estimating from message sizes
            if in_tok == 0 and out_tok == 0:
                messages = body.get("messages", [])
                # Rough estimate: ~4 chars per token
                total_chars = sum(
                    len(str(m.get("content", ""))) for m in messages if isinstance(m, dict)
                )
                in_tok = total_chars // 4

            if in_tok == 0 and out_tok == 0:
                result.sessions_skipped += 1
                continue

            # Check if this was an error dump
            error = data.get("error", {})
            session_id = data.get("session_id", json_file.stem)

            cost = catalog.calculate_cost(model_id, in_tok, out_tok)
            result.total_input_tokens += in_tok
            result.total_output_tokens += out_tok
            result.total_cost_usd += cost
            result.models_seen.add(model_id)
            result.sessions_imported += 1

            if not dry_run:
                record = LLMUsageRecord(
                    model_id=model_id,
                    agent_id=agent_id or (session_id.split("_")[0] if "_" in session_id else "dump"),
                    task_id=session_id,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost,
                    recorded_at=ts,
                    metadata={
                        "source": "request_dump",
                        "file": str(json_file.name),
                        "reason": data.get("reason"),
                        "error_type": error.get("type") if isinstance(error, dict) else None,
                        "error_status": error.get("status_code") if isinstance(error, dict) else None,
                    },
                )
                result.records.append(record)

        return result


def discover_session_sources() -> list[dict]:
    """Auto-discover likely session data sources on the current machine.

    Returns list of dicts with type, path, and description.
    """
    sources = []
    home = Path.home()

    # Hermes state.db (common locations)
    candidates = [
        home / "state.db",
        Path("/opt/data/state.db"),
        home / ".hermes" / "state.db",
        home / ".local" / "share" / "hermes" / "state.db",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            sources.append({
                "type": "hermes_sqlite",
                "path": str(c),
                "description": "Hermes agent state database (SQLite)",
            })

    # Hermes sessions directory (request dumps)
    sessions_dirs = [
        home / "sessions",
        Path("/opt/data/sessions"),
        home / ".hermes" / "sessions",
    ]
    for d in sessions_dirs:
        if d.exists() and d.is_dir():
            json_count = len(list(d.glob("*.json")))
            if json_count > 0:
                sources.append({
                    "type": "request_dumps",
                    "path": str(d),
                    "description": f"Hermes API request dumps ({json_count} files)",
                })

    # Claude Code session files
    claude_dirs = [
        home / ".claude" / "sessions",
        home / ".config" / "claude" / "sessions",
    ]
    for d in claude_dirs:
        if d.exists() and d.is_dir():
            jsonl_count = len(list(d.glob("*.jsonl")))
            if jsonl_count > 0:
                sources.append({
                    "type": "jsonl_transcript",
                    "path": str(d),
                    "description": f"Claude Code session transcripts ({jsonl_count} files)",
                })

    return sources
