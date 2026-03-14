"""Tests for the Router module."""

import pytest

from autopilot_core.router import ExecutorChoice, Router, RoutingTable
from autopilot_core.task import TaskSpec


@pytest.fixture
def router(tmp_path):
    return Router(
        executors=["claude-code", "codex"],
        routing_table_path=tmp_path / "routing.json",
    )


class TestExplicitRouting:
    def test_explicit_executor(self, router):
        task = TaskSpec.new("Test", executor="claude-code")
        choice = router.route(task)
        assert choice.executor == "claude-code"
        assert choice.confidence == 1.0
        assert "explicit" in choice.reason

    def test_explicit_unknown_falls_through(self, router):
        task = TaskSpec.new("Test", executor="gemini")
        choice = router.route(task)
        # Falls through to score-based routing
        assert choice.executor in ["claude-code", "codex"]


class TestScoreBasedRouting:
    def test_neutral_with_no_data(self, router):
        task = TaskSpec.new("Test", task_type="bugfix")
        choice = router.route(task)
        assert choice.confidence >= 0.0

    def test_prefers_better_performer(self, router):
        # Record codex as better at bugfixes
        for _ in range(10):
            router.record_outcome("codex", "bugfix", success=True, duration_s=60)
            router.record_outcome("claude-code", "bugfix", success=False, duration_s=120)

        task = TaskSpec.new("Fix bug", task_type="bugfix")
        choice = router.route(task)
        assert choice.executor == "codex"

    def test_fallback_when_no_executors(self, tmp_path):
        router = Router(executors=[], routing_table_path=tmp_path / "r.json")
        task = TaskSpec.new("Test")
        choice = router.route(task)
        assert choice.confidence == 0.0


class TestRoutingTablePersistence:
    def test_save_and_load(self, tmp_path):
        table = RoutingTable()
        table.record("codex", "feature", success=True, duration_s=120)
        table.record("codex", "feature", success=True, duration_s=90)
        table.record("codex", "feature", success=False)

        path = tmp_path / "table.json"
        table.save(path)

        loaded = RoutingTable.load(path)
        stats = loaded.stats["codex"]["feature"]
        assert stats.runs == 3
        assert stats.done == 2
        assert stats.blocked == 1

    def test_load_missing_file(self, tmp_path):
        table = RoutingTable.load(tmp_path / "nonexistent.json")
        assert table.stats == {}


class TestRecordOutcome:
    def test_record_updates_table(self, router):
        router.record_outcome("claude-code", "refactor", success=True, duration_s=45)
        stats = router.table.stats["claude-code"]["refactor"]
        assert stats.runs == 1
        assert stats.done == 1
        assert stats.avg_duration_s == 45.0
