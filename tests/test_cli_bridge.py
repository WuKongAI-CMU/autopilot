"""Tests for CLI bridge commands (route, bridge scan, bridge ingest)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot_core.cli import build_parser, cmd_route, cmd_bridge_scan, cmd_bridge_ingest
from autopilot_core.queue import TaskQueue
from autopilot_core.event_bus import EventBus


def _write_request(path: Path, request_id: str, status: str = "pending",
                   priority: str = "high", executor: str = "claude-code",
                   task_type: str = "feature", body: str = "# Test Task\n\nDo something.") -> Path:
    """Write a minimal agent-ops request file."""
    content = f"""---
id: {request_id}
status: {status}
priority: {priority}
executor: {executor}
task_type: {task_type}
repo: /tmp/test-repo
cwd: /tmp/test-repo
---

{body}
"""
    path.write_text(content, encoding="utf-8")
    return path


class TestRouteCommand:
    def test_route_parses_request_file(self, tmp_path, capsys):
        req_file = tmp_path / "req-test-001.md"
        _write_request(req_file, "req-test-001", priority="critical", task_type="architecture")

        args = build_parser().parse_args(["route", str(req_file)])
        result = cmd_route(args)
        assert result == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["task_id"] == "req-test-001"
        assert data["complexity"] == "high"
        assert data["recommended_executor"] in ("claude-code", "kimi", "local")

    def test_route_low_complexity_task(self, tmp_path, capsys):
        req_file = tmp_path / "req-test-002.md"
        _write_request(req_file, "req-test-002", priority="low", task_type="cleanup")

        args = build_parser().parse_args(["route", str(req_file)])
        cmd_route(args)

        data = json.loads(capsys.readouterr().out)
        assert data["complexity"] == "low"

    def test_route_explicit_executor_preserved(self, tmp_path, capsys):
        req_file = tmp_path / "req-test-003.md"
        _write_request(req_file, "req-test-003", executor="kimi")

        args = build_parser().parse_args(["route", str(req_file)])
        cmd_route(args)

        data = json.loads(capsys.readouterr().out)
        assert data["recommended_executor"] == "kimi"


class TestBridgeScan:
    def test_scan_filters_pending_only(self, tmp_path, capsys):
        _write_request(tmp_path / "req-pending.md", "req-pending", status="pending")
        _write_request(tmp_path / "req-done.md", "req-done", status="done")
        _write_request(tmp_path / "req-cancelled.md", "req-cancelled", status="cancelled")

        args = build_parser().parse_args(["bridge", "scan", "--requests-dir", str(tmp_path)])
        cmd_bridge_scan(args)

        output = capsys.readouterr().out
        assert "req-pending" in output
        assert "req-done" not in output
        assert "req-cancelled" not in output
        assert "Pending: 1" in output

    def test_scan_json_output(self, tmp_path, capsys):
        _write_request(tmp_path / "req-a.md", "req-a")
        _write_request(tmp_path / "req-b.md", "req-b")

        args = build_parser().parse_args(["bridge", "scan", "--requests-dir", str(tmp_path), "--json"])
        cmd_bridge_scan(args)

        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        assert len(data) == 2
        assert all("task_id" in item for item in data)
        assert all("complexity" in item for item in data)

    def test_scan_empty_dir(self, tmp_path, capsys):
        args = build_parser().parse_args(["bridge", "scan", "--requests-dir", str(tmp_path)])
        cmd_bridge_scan(args)
        assert "Pending: 0" in capsys.readouterr().out


class TestBridgeIngest:
    def test_ingest_creates_tasks(self, tmp_path, capsys):
        requests_dir = tmp_path / "requests"
        requests_dir.mkdir()
        _write_request(requests_dir / "req-ingest-1.md", "req-ingest-1")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "tasks").mkdir()

        import os
        old_val = os.environ.get("AUTOPILOT_DATA_DIR")
        os.environ["AUTOPILOT_DATA_DIR"] = str(data_dir)
        try:
            args = build_parser().parse_args(["bridge", "ingest", "--requests-dir", str(requests_dir)])
            result = cmd_bridge_ingest(args)
            assert result == 0

            output = capsys.readouterr().out
            assert "Imported 1" in output

            # Verify task was created in queue
            queue = TaskQueue(data_dir / "tasks")
            tasks = queue.scan()
            assert len(tasks) == 1
            assert tasks[0].id == "req-ingest-1"
        finally:
            if old_val is None:
                os.environ.pop("AUTOPILOT_DATA_DIR", None)
            else:
                os.environ["AUTOPILOT_DATA_DIR"] = old_val

    def test_ingest_skips_existing(self, tmp_path, capsys):
        requests_dir = tmp_path / "requests"
        requests_dir.mkdir()
        _write_request(requests_dir / "req-dup.md", "req-dup")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "tasks").mkdir()

        import os
        old_val = os.environ.get("AUTOPILOT_DATA_DIR")
        os.environ["AUTOPILOT_DATA_DIR"] = str(data_dir)
        try:
            # First ingest
            args = build_parser().parse_args(["bridge", "ingest", "--requests-dir", str(requests_dir)])
            cmd_bridge_ingest(args)
            assert "Imported 1" in capsys.readouterr().out

            # Second ingest — should skip
            args = build_parser().parse_args(["bridge", "ingest", "--requests-dir", str(requests_dir)])
            cmd_bridge_ingest(args)
            output = capsys.readouterr().out
            assert "Imported 0" in output
            assert "1 already in queue" in output
        finally:
            if old_val is None:
                os.environ.pop("AUTOPILOT_DATA_DIR", None)
            else:
                os.environ["AUTOPILOT_DATA_DIR"] = old_val

    def test_ingest_creates_events(self, tmp_path, capsys):
        requests_dir = tmp_path / "requests"
        requests_dir.mkdir()
        _write_request(requests_dir / "req-evt.md", "req-evt")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "tasks").mkdir()

        import os
        old_val = os.environ.get("AUTOPILOT_DATA_DIR")
        os.environ["AUTOPILOT_DATA_DIR"] = str(data_dir)
        try:
            args = build_parser().parse_args(["bridge", "ingest", "--requests-dir", str(requests_dir)])
            cmd_bridge_ingest(args)

            # Check event was recorded
            bus = EventBus(data_dir / "events.ndjson")
            events = bus.recent(10, event_type="task.imported")
            assert len(events) == 1
            assert events[0]["payload"]["task_id"] == "req-evt"
            assert events[0]["payload"]["source"] == "agent-ops"
        finally:
            if old_val is None:
                os.environ.pop("AUTOPILOT_DATA_DIR", None)
            else:
                os.environ["AUTOPILOT_DATA_DIR"] = old_val
