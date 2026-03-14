"""Tests for the CostTracker module."""

import pytest

from autopilot_core.cost_tracker import CostTracker
from autopilot_core.event_bus import EventBus


@pytest.fixture
def tracker(tmp_path):
    bus = EventBus(tmp_path / "events.ndjson")
    return CostTracker(bus)


class TestRecordSpend:
    def test_record_returns_event_id(self, tracker):
        eid = tracker.record_spend("task-1", "claude-opus", 1000, 500, 0.05)
        assert eid.startswith("evt-")

    def test_record_multiple_spends(self, tracker):
        tracker.record_spend("task-1", "claude-opus", 1000, 500, 0.05)
        tracker.record_spend("task-1", "claude-opus", 2000, 800, 0.08)
        tracker.record_spend("task-2", "kimi-k2.5", 5000, 1000, 0.004)
        cost = tracker.get_task_cost("task-1")
        assert cost == 0.13


class TestGetTaskCost:
    def test_zero_for_unknown_task(self, tracker):
        assert tracker.get_task_cost("nonexistent") == 0.0

    def test_sums_multiple_entries(self, tracker):
        tracker.record_spend("t1", "claude", 100, 50, 0.01)
        tracker.record_spend("t1", "claude", 200, 100, 0.02)
        tracker.record_spend("t2", "kimi", 300, 150, 0.003)
        assert tracker.get_task_cost("t1") == 0.03
        assert tracker.get_task_cost("t2") == 0.003


class TestGetModelCosts:
    def test_breakdown_by_model(self, tracker):
        tracker.record_spend("t1", "claude-opus", 1000, 500, 0.05)
        tracker.record_spend("t2", "claude-opus", 2000, 800, 0.08)
        tracker.record_spend("t3", "kimi-k2.5", 5000, 1000, 0.004)

        costs = tracker.get_model_costs()
        assert "claude-opus" in costs
        assert "kimi-k2.5" in costs
        assert costs["claude-opus"]["count"] == 2
        assert costs["claude-opus"]["total_usd"] == 0.13
        assert costs["kimi-k2.5"]["total_input_tokens"] == 5000

    def test_empty_tracker(self, tracker):
        assert tracker.get_model_costs() == {}


class TestGetSummary:
    def test_summary(self, tracker):
        tracker.record_spend("t1", "claude", 1000, 500, 0.05)
        tracker.record_spend("t2", "kimi", 5000, 1000, 0.004)
        summary = tracker.get_summary()
        assert summary["total_usd"] == 0.054
        assert summary["total_tasks"] == 2
        assert "claude" in summary["by_model"]
        assert "kimi" in summary["by_model"]
