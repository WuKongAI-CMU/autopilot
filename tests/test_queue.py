"""Tests for the TaskQueue module."""

import pytest

from autopilot_core.queue import TaskQueue
from autopilot_core.task import TaskPriority, TaskSpec, TaskStatus


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "tasks")


class TestScan:
    def test_scan_empty(self, queue):
        assert queue.scan() == []

    def test_scan_finds_tasks(self, queue):
        t1 = TaskSpec.new("Task 1")
        t2 = TaskSpec.new("Task 2")
        queue.create(t1)
        queue.create(t2)
        tasks = queue.scan()
        assert len(tasks) == 2

    def test_scan_skips_non_md(self, queue):
        queue._ensure_dir()
        (queue.tasks_dir / "notes.txt").write_text("not a task")
        t = TaskSpec.new("Real task")
        queue.create(t)
        assert len(queue.scan()) == 1


class TestCreateAndGet:
    def test_create_writes_file(self, queue):
        t = TaskSpec.new("Test task", priority=TaskPriority.HIGH)
        path = queue.create(t)
        assert path.exists()
        assert path.suffix == ".md"

    def test_get_returns_task(self, queue):
        t = TaskSpec.new("Findable task", executor="claude-code")
        queue.create(t)
        found = queue.get(t.id)
        assert found is not None
        assert found.title == "Findable task"
        assert found.executor == "claude-code"

    def test_get_missing_returns_none(self, queue):
        assert queue.get("nonexistent-id") is None


class TestUpdate:
    def test_update_status(self, queue):
        t = TaskSpec.new("Update me")
        queue.create(t)
        updated = queue.update(t.id, status=TaskStatus.CLAIMED)
        assert updated is not None
        assert updated.status == TaskStatus.CLAIMED
        # Verify persistence
        reloaded = queue.get(t.id)
        assert reloaded.status == TaskStatus.CLAIMED

    def test_update_nonexistent(self, queue):
        assert queue.update("nope", status="done") is None


class TestEligible:
    def test_eligible_filters_and_sorts(self, queue):
        low = TaskSpec.new("Low task", priority=TaskPriority.LOW)
        high = TaskSpec.new("High task", priority=TaskPriority.HIGH)
        done = TaskSpec.new("Done task")
        queue.create(low)
        queue.create(high)
        queue.create(done)
        queue.update(done.id, status=TaskStatus.CLAIMED)
        queue.update(done.id, status=TaskStatus.IN_PROGRESS)
        queue.update(done.id, status=TaskStatus.DONE)

        eligible = queue.eligible()
        assert len(eligible) == 2
        assert eligible[0].priority == TaskPriority.HIGH
        assert eligible[1].priority == TaskPriority.LOW

    def test_eligible_empty_queue(self, queue):
        assert queue.eligible() == []
