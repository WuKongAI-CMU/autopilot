"""Tests for the Task model."""

import json

import pytest

from autopilot_core.task import TaskPriority, TaskSpec, TaskStatus


class TestCreateTask:
    def test_basic_creation(self):
        task = TaskSpec(id="req-001", title="Fix the bug")
        assert task.id == "req-001"
        assert task.title == "Fix the bug"
        assert task.status == TaskStatus.PENDING
        assert task.priority == TaskPriority.MEDIUM
        assert task.executor is None
        assert task.depends_on == []
        assert task.tags == []

    def test_creation_with_all_fields(self):
        task = TaskSpec(
            id="req-002",
            title="Add feature",
            status=TaskStatus.CLAIMED,
            priority=TaskPriority.HIGH,
            executor="claude-code",
            repo="/path/to/repo",
            task_type="feature",
            description="Implement the new feature",
            timeout_minutes=60,
            depends_on=["req-001"],
            acceptance_criteria=["Tests pass", "No regressions"],
            tags=["feature", "urgent"],
        )
        assert task.executor == "claude-code"
        assert task.priority == TaskPriority.HIGH
        assert len(task.depends_on) == 1
        assert len(task.acceptance_criteria) == 2


class TestFactory:
    def test_new_generates_id(self):
        task = TaskSpec.new("Test task")
        assert task.id.startswith("req-")
        assert len(task.id) == 21  # req-YYYYMMDD-xxxxxxxx (4+8+1+8 = 21)

    def test_new_with_kwargs(self):
        task = TaskSpec.new("High priority task", priority=TaskPriority.CRITICAL, executor="codex")
        assert task.priority == TaskPriority.CRITICAL
        assert task.executor == "codex"

    def test_new_unique_ids(self):
        ids = {TaskSpec.new("task").id for _ in range(100)}
        assert len(ids) == 100


class TestMarkdownRoundtrip:
    def test_simple_roundtrip(self):
        original = TaskSpec.new(
            "Fix authentication bug",
            priority=TaskPriority.HIGH,
            executor="claude-code",
            task_type="bugfix",
            description="The login endpoint returns 500 when email contains a plus sign.",
            tags=["bugfix", "auth"],
        )
        md = original.to_markdown()
        restored = TaskSpec.from_markdown(md)

        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.executor == original.executor
        assert restored.task_type == original.task_type
        assert restored.tags == original.tags
        assert "plus sign" in restored.description

    def test_roundtrip_with_lists(self):
        original = TaskSpec.new(
            "Complex task",
            depends_on=["req-001", "req-002"],
            acceptance_criteria=["All tests pass", "No lint errors"],
        )
        md = original.to_markdown()
        restored = TaskSpec.from_markdown(md)
        assert restored.depends_on == original.depends_on
        assert restored.acceptance_criteria == original.acceptance_criteria

    def test_from_markdown_with_body(self):
        text = """---
id: req-20260314-test1234
title: Test task
status: pending
priority: medium
created_at: 2026-03-14T00:00:00+00:00
timeout_minutes: 30
---

This is the task description.

It has multiple paragraphs.
"""
        task = TaskSpec.from_markdown(text)
        assert task.id == "req-20260314-test1234"
        assert "multiple paragraphs" in task.description

    def test_invalid_frontmatter_raises(self):
        with pytest.raises(ValueError, match="missing --- delimiters"):
            TaskSpec.from_markdown("no frontmatter here")


class TestJsonRoundtrip:
    def test_json_roundtrip(self):
        original = TaskSpec.new("JSON test", priority=TaskPriority.LOW, tags=["test"])
        json_str = original.to_json()
        restored = TaskSpec.from_json(json_str)
        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.priority == TaskPriority.LOW
        assert restored.tags == ["test"]

    def test_to_dict(self):
        task = TaskSpec.new("Dict test")
        d = task.to_dict()
        assert isinstance(d, dict)
        assert d["status"] == "pending"
        assert d["priority"] == "medium"


class TestStatusTransition:
    def test_valid_transitions(self):
        task = TaskSpec.new("Transition test")
        assert task.status == TaskStatus.PENDING
        task.transition(TaskStatus.CLAIMED)
        assert task.status == TaskStatus.CLAIMED
        task.transition(TaskStatus.IN_PROGRESS)
        assert task.status == TaskStatus.IN_PROGRESS
        task.transition(TaskStatus.DONE)
        assert task.status == TaskStatus.DONE

    def test_invalid_done_to_pending(self):
        task = TaskSpec.new("Done task")
        task.transition(TaskStatus.CLAIMED)
        task.transition(TaskStatus.IN_PROGRESS)
        task.transition(TaskStatus.DONE)
        with pytest.raises(ValueError, match="Invalid transition"):
            task.transition(TaskStatus.PENDING)

    def test_blocked_can_retry(self):
        task = TaskSpec.new("Blocked task")
        task.transition(TaskStatus.CLAIMED)
        task.transition(TaskStatus.IN_PROGRESS)
        task.transition(TaskStatus.BLOCKED)
        task.transition(TaskStatus.PENDING)  # Can retry
        assert task.status == TaskStatus.PENDING

    def test_cancel_from_any_active(self):
        for start in [TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]:
            task = TaskSpec(id="t", title="t", status=start)
            task.transition(TaskStatus.CANCELLED)
            assert task.status == TaskStatus.CANCELLED


class TestPriorityOrdering:
    def test_critical_is_highest(self):
        assert TaskPriority.CRITICAL < TaskPriority.HIGH
        assert TaskPriority.HIGH < TaskPriority.MEDIUM
        assert TaskPriority.MEDIUM < TaskPriority.LOW

    def test_sorting(self):
        priorities = [TaskPriority.LOW, TaskPriority.CRITICAL, TaskPriority.MEDIUM, TaskPriority.HIGH]
        sorted_p = sorted(priorities)
        assert sorted_p == [TaskPriority.CRITICAL, TaskPriority.HIGH, TaskPriority.MEDIUM, TaskPriority.LOW]
