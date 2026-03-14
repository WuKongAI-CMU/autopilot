"""Tests for the Router module."""

import pytest

from autopilot_core.router import ExecutorChoice, Router, RoutingTable, classify_complexity
from autopilot_core.task import TaskPriority, TaskSpec


@pytest.fixture
def router(tmp_path):
    return Router(
        executors=["claude-code", "codex"],
        routing_table_path=tmp_path / "routing.json",
    )


@pytest.fixture
def multi_model_router(tmp_path):
    """Router with both high-tier (claude-code) and low-tier (kimi) executors."""
    return Router(
        executors=["claude-code", "kimi"],
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


class TestComplexityClassification:
    def test_architecture_is_high(self):
        task = TaskSpec.new("Redesign auth", task_type="architecture")
        assert classify_complexity(task) == "high"

    def test_refactor_is_high(self):
        task = TaskSpec.new("Refactor DB layer", task_type="refactor")
        assert classify_complexity(task) == "high"

    def test_security_is_high(self):
        task = TaskSpec.new("Fix XSS", task_type="security")
        assert classify_complexity(task) == "high"

    def test_diagnostic_is_low(self):
        task = TaskSpec.new("Check version", task_type="diagnostic")
        assert classify_complexity(task) == "low"

    def test_cleanup_is_low(self):
        task = TaskSpec.new("Remove dead code", task_type="cleanup")
        assert classify_complexity(task) == "low"

    def test_docs_is_low(self):
        task = TaskSpec.new("Update README", task_type="docs")
        assert classify_complexity(task) == "low"

    def test_critical_priority_defaults_high(self):
        task = TaskSpec.new("Urgent fix", priority=TaskPriority.CRITICAL, task_type="bugfix")
        assert classify_complexity(task) == "high"

    def test_low_priority_defaults_low(self):
        task = TaskSpec.new("Minor tweak", priority=TaskPriority.LOW, task_type="bugfix")
        assert classify_complexity(task) == "low"

    def test_long_description_is_high(self):
        task = TaskSpec.new("Complex task", description="x" * 600, priority=TaskPriority.MEDIUM)
        assert classify_complexity(task) == "high"


class TestTierBasedRouting:
    def test_high_complexity_routes_to_claude(self, multi_model_router):
        task = TaskSpec.new("Redesign auth module", task_type="architecture")
        choice = multi_model_router.route(task)
        assert choice.executor == "claude-code"
        assert "complexity=high" in choice.reason

    def test_low_complexity_routes_to_kimi(self, multi_model_router):
        task = TaskSpec.new("Fix typo in docs", task_type="docs", priority=TaskPriority.LOW)
        choice = multi_model_router.route(task)
        assert choice.executor == "kimi"
        assert "complexity=low" in choice.reason

    def test_low_complexity_diagnostic_routes_to_kimi(self, multi_model_router):
        task = TaskSpec.new("Check disk space", task_type="diagnostic")
        choice = multi_model_router.route(task)
        assert choice.executor == "kimi"

    def test_explicit_overrides_tier(self, multi_model_router):
        """Explicit executor constraint always wins, even if tier doesn't match."""
        task = TaskSpec.new("Simple task", task_type="docs", executor="claude-code")
        choice = multi_model_router.route(task)
        assert choice.executor == "claude-code"
        assert "explicit" in choice.reason

    def test_fallback_when_no_tier_match(self, tmp_path):
        """If only low-tier executors exist, they handle high-complexity tasks."""
        router = Router(
            executors=["kimi", "local"],
            routing_table_path=tmp_path / "r.json",
        )
        task = TaskSpec.new("Critical refactor", task_type="refactor", priority=TaskPriority.CRITICAL)
        choice = router.route(task)
        # Should still route somewhere (fallback)
        assert choice.executor in ["kimi", "local"]

    def test_custom_tiers(self, tmp_path):
        """Custom tier overrides change routing behavior."""
        router = Router(
            executors=["claude-code", "kimi"],
            routing_table_path=tmp_path / "r.json",
            executor_tiers={"kimi": "high"},  # Override kimi to high tier
        )
        task = TaskSpec.new("Architecture work", task_type="architecture")
        choice = router.route(task)
        # Both are now high-tier, so either is valid
        assert choice.executor in ["claude-code", "kimi"]
