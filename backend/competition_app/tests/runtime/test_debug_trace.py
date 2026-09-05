from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from competition_app.runtime.debug_trace import (
    DebugTraceConfig,
    DebugTraceManager,
    DebugTraceWriter,
    bind_debug_call_context,
    record_debug_trace,
)


def _records(writer: DebugTraceWriter) -> list[dict]:
    records: list[dict] = []
    for path in writer.paths:
        if path.exists():
            records.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
    return records


def test_disabled_manager_does_not_create_files(tmp_path: Path) -> None:
    manager = DebugTraceManager(DebugTraceConfig(root=tmp_path), mode="stub")
    assert manager.open("run-1") is None
    assert list(tmp_path.rglob("*")) == []


def test_live_requires_explicit_authorization(tmp_path: Path) -> None:
    config = DebugTraceConfig(enabled=True, root=tmp_path, allow_live=False)
    assert DebugTraceManager(config, mode="live").open("run-1") is None


def test_writer_sanitizes_records_and_keeps_monotonic_sequence(tmp_path: Path) -> None:
    writer = DebugTraceWriter(
        tmp_path,
        "run/with secrets",
        metadata={"execution_id": "EXE-1", "api_key": "do-not-write"},
    )
    with bind_debug_call_context(agent="planner_agent", step_id="planner"):
        assert record_debug_trace(
            "structured_attempt",
            request_payload={"authorization": "Bearer secret", "prompt": "hello"},
            response_text="13800138000",
        ) is False
        assert writer.record(
            "structured_attempt",
            request_payload={"authorization": "Bearer secret", "prompt": "hello"},
            response_text="13800138000",
        )
    writer.record("terminal", status="failed")
    writer.close()

    records = _records(writer)
    assert [record["seq"] for record in records] == [1, 2]
    assert all(record["schema_version"] == "1.0" for record in records)
    assert records[0]["request_payload"]["authorization"] == "[REDACTED]"
    assert records[0]["response_text"] == "[REDACTED_PHONE]"
    assert records[0]["metadata"]["api_key"] == "[REDACTED]"
    assert writer.path is not None
    assert writer.path.stat().st_mode & 0o777 == 0o600


def test_writer_rotates_and_bounds_total_bytes(tmp_path: Path) -> None:
    writer = DebugTraceWriter(
        tmp_path,
        "run-1",
        max_bytes=1_000,
        rotate_bytes=300,
        max_event_bytes=500,
    )
    for index in range(20):
        writer.record("delta", index=index, content="x" * 100)
    writer.close()
    existing = [path for path in writer.paths if path.exists()]
    assert len(existing) >= 2
    assert sum(path.stat().st_size for path in existing) <= 1_000
    for path in existing:
        assert path.stat().st_mode & 0o777 == 0o600


def test_writer_failures_are_best_effort(tmp_path: Path) -> None:
    writer = DebugTraceWriter(tmp_path, "run-1")
    writer._directory = tmp_path / "not-a-directory"
    writer._directory.write_text("occupied", encoding="utf-8")
    assert writer.record("event", value="x") is False
    assert writer.closed is False
    writer.close()


def test_config_rejects_live_debug_without_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    from competition_app.config import Settings, SettingsError

    values = {
        "COMPETITION_APP_MODE": "live",
        "LLM_API_KEY": "chat-key",
        "EMBEDDING_API_KEY": "embedding-key",
        "COMPETITION_DEBUG_TRACE_ENABLED": "true",
        "COMPETITION_DEBUG_TRACE_ALLOW_LIVE": "false",
    }
    with pytest.raises(SettingsError, match="ALLOW_LIVE"):
        Settings.from_env(values)
