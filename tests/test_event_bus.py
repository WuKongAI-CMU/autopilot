"""Tests for the EventBus module."""

import json
import threading
import time

import pytest

from autopilot_core.event_bus import EventBus


@pytest.fixture
def bus(tmp_path):
    return EventBus(tmp_path / "events.ndjson")


class TestAppendAndRead:
    def test_append_returns_event_id(self, bus):
        eid = bus.append("task.created", {"task_id": "t1"})
        assert eid.startswith("evt-")

    def test_append_and_read_back(self, bus):
        bus.append("task.created", {"task_id": "t1"}, source="test")
        bus.append("task.done", {"task_id": "t1"}, source="test")
        events = bus.read_recent(10)
        assert len(events) == 2
        assert events[0]["event_type"] == "task.created"
        assert events[1]["event_type"] == "task.done"

    def test_event_structure(self, bus):
        bus.append("dispatch.decision", {"executor": "claude"}, source="router", trace_id="tr-1")
        events = bus.read_recent(1)
        e = events[0]
        assert "id" in e
        assert e["event_type"] == "dispatch.decision"
        assert e["source"] == "router"
        assert e["trace_id"] == "tr-1"
        assert e["payload"]["executor"] == "claude"
        assert "timestamp" in e


class TestRecentFiltering:
    def test_filter_by_event_type(self, bus):
        bus.append("task.created", {"n": 1})
        bus.append("task.done", {"n": 2})
        bus.append("task.created", {"n": 3})

        created = bus.recent(10, event_type="task.created")
        assert len(created) == 2
        assert all(e["event_type"] == "task.created" for e in created)

    def test_limit_respected(self, bus):
        for i in range(20):
            bus.append("test", {"i": i})
        events = bus.recent(5)
        assert len(events) == 5

    def test_max_age_filter(self, bus):
        # All events are fresh (just created), so max_age=1 should include them
        bus.append("test", {"v": 1})
        events = bus.recent(10, max_age_hours=1.0)
        assert len(events) == 1


class TestPubSub:
    def test_publish_and_subscribe(self, bus):
        bus.publish("alerts", "System healthy", source="monitor")
        bus.publish("alerts", {"level": "warn", "msg": "High load"}, source="monitor")
        bus.publish("logs", "Unrelated log")

        alerts = bus.subscribe("alerts", limit=10)
        assert len(alerts) == 2
        assert alerts[0]["payload"]["channel"] == "alerts"

    def test_subscribe_empty_channel(self, bus):
        bus.publish("alerts", "something")
        empty = bus.subscribe("nonexistent", limit=10)
        assert len(empty) == 0


class TestTTL:
    def test_gc_removes_expired(self, bus):
        # Manually write an old event
        old_event = {
            "id": "old-1",
            "event_type": "test",
            "timestamp": "2020-01-01T00:00:00Z",
            "source": "test",
            "trace_id": None,
            "payload": {},
        }
        bus.path.parent.mkdir(parents=True, exist_ok=True)
        with bus.path.open("w") as f:
            f.write(json.dumps(old_event) + "\n")

        # Append a fresh one
        bus.append("fresh", {"v": 1})

        result = bus.gc(max_age_hours=1.0)
        assert result["removed"] == 1
        assert result["kept"] == 1

        remaining = bus.read_recent(10)
        assert len(remaining) == 1
        assert remaining[0]["event_type"] == "fresh"


class TestConcurrency:
    def test_concurrent_appends(self, bus):
        errors = []

        def writer(n):
            try:
                for i in range(10):
                    bus.append("concurrent", {"writer": n, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent writes had errors: {errors}"
        events = bus.read_recent(100)
        assert len(events) == 40  # 4 writers * 10 events each


class TestEmptyBus:
    def test_read_empty(self, bus):
        assert bus.read_recent(10) == []
        assert bus.recent(10) == []
        assert bus.subscribe("any") == []

    def test_gc_empty(self, bus):
        result = bus.gc()
        assert result == {"removed": 0, "kept": 0}


class TestPrune:
    def test_prune_keeps_recent(self, bus):
        for i in range(20):
            bus.append("test", {"i": i})
        result = bus.prune(max_entries=5)
        assert result["pruned"] == 15
        assert result["kept"] == 5
        remaining = bus.read_recent(100)
        assert len(remaining) == 5
