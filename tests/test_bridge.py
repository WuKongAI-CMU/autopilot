"""Tests for the agent-ops bridge."""

import pytest

from bridge.agent_ops_bridge import (
    request_to_task,
    task_to_request,
    scan_requests,
    _parse_frontmatter,
)
from autopilot_core.task import TaskPriority, TaskSpec, TaskStatus


SAMPLE_REQUEST = """---
id: req-20260303-2330-miniflux-setup
status: pending
priority: high
repo: /Users/peter/agent-intelligence-bus
cwd: /Users/peter/agent-intelligence-bus
executor: either
created: 2026-03-03
---

# Deploy Miniflux + Build Feed Pipeline

## Context
Research concluded that Miniflux should replace the custom poller.
"""

SAMPLE_CLAUDE_REQUEST = """---
id: ci-fix-test-repo-abc123de
status: pending
priority: critical
repo: /Users/peter/test-repo
executor: claude
task_type: bugfix
tags: [ci, fix, urgent]
---

# Fix CI Pipeline

The CI build is failing due to missing dependency.
"""


class TestParseFrontmatter:
    def test_parse_yaml_request(self):
        meta, body = _parse_frontmatter(SAMPLE_REQUEST)
        assert meta["id"] == "req-20260303-2330-miniflux-setup"
        assert meta["status"] == "pending"
        assert meta["priority"] == "high"
        assert meta["executor"] == "either"
        assert "Deploy Miniflux" in body

    def test_parse_with_tags(self):
        meta, body = _parse_frontmatter(SAMPLE_CLAUDE_REQUEST)
        assert meta["tags"] == ["ci", "fix", "urgent"]
        assert meta["task_type"] == "bugfix"

    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("Just plain text")
        assert meta == {}
        assert body == "Just plain text"


class TestRequestToTask:
    def test_converts_yaml_request(self, tmp_path):
        path = tmp_path / "req-test.yaml"
        path.write_text(SAMPLE_REQUEST)
        task = request_to_task(path)
        assert task.id == "req-20260303-2330-miniflux-setup"
        assert task.title == "Deploy Miniflux + Build Feed Pipeline"
        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.PENDING
        assert task.executor is None  # "either" maps to empty = let router decide
        assert task.repo == "/Users/peter/agent-intelligence-bus"
        assert "Miniflux" in task.description

    def test_converts_claude_request(self, tmp_path):
        path = tmp_path / "ci-fix-test.yaml"
        path.write_text(SAMPLE_CLAUDE_REQUEST)
        task = request_to_task(path)
        assert task.id == "ci-fix-test-repo-abc123de"
        assert task.priority == TaskPriority.CRITICAL
        assert task.executor == "claude-code"  # "claude" maps to "claude-code"
        assert task.task_type == "bugfix"
        assert task.tags == ["ci", "fix", "urgent"]

    def test_infers_task_type_from_filename(self, tmp_path):
        path = tmp_path / "ci-fix-repo-123.yaml"
        path.write_text("---\nid: ci-fix-123\nstatus: pending\npriority: medium\n---\n\n# Fix CI\n")
        task = request_to_task(path)
        assert task.task_type == "bugfix"


class TestTaskToRequest:
    def test_roundtrip(self, tmp_path):
        task = TaskSpec.new(
            "Test roundtrip",
            priority=TaskPriority.HIGH,
            description="Some work to do",
            task_type="feature",
            tags=["test", "roundtrip"],
        )
        path = task_to_request(task, tmp_path / "output")
        assert path.exists()

        # Read back
        loaded = request_to_task(path)
        assert loaded.id == task.id
        assert loaded.title == "Test roundtrip"
        assert loaded.priority == TaskPriority.HIGH
        assert "Some work to do" in loaded.description


class TestScanRequests:
    def test_scan_directory(self, tmp_path):
        # Create request files
        (tmp_path / "req-001.yaml").write_text(SAMPLE_REQUEST)
        (tmp_path / "ci-fix-002.yaml").write_text(SAMPLE_CLAUDE_REQUEST)
        (tmp_path / "README.md").write_text("# Requests\nNot a request file.")
        (tmp_path / "_archive").mkdir()

        tasks = scan_requests(tmp_path)
        assert len(tasks) == 2
        ids = {t.id for t in tasks}
        assert "req-20260303-2330-miniflux-setup" in ids
        assert "ci-fix-test-repo-abc123de" in ids

    def test_scan_empty_dir(self, tmp_path):
        assert scan_requests(tmp_path) == []

    def test_scan_nonexistent_dir(self, tmp_path):
        assert scan_requests(tmp_path / "nope") == []

    def test_scan_real_agent_ops(self):
        """Smoke test against real agent-ops requests if available."""
        requests_dir = "/Users/peter/agent-ops/requests"
        tasks = scan_requests(requests_dir)
        # Should find some requests (we know they exist)
        assert len(tasks) >= 1
        # All should have valid IDs
        for t in tasks:
            assert t.id
            assert t.title
