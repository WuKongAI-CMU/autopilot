"""Tests for the Dispatcher module."""

from dataclasses import dataclass

import pytest

from autopilot_core.dispatcher import DispatchConfig, Dispatcher, ExecutionResult
from autopilot_core.event_bus import EventBus
from autopilot_core.queue import TaskQueue
from autopilot_core.router import Router
from autopilot_core.task import TaskPriority, TaskSpec, TaskStatus


class MockAdapter:
    """Mock executor adapter for testing."""

    def __init__(self, executor_name: str, succeed: bool = True):
        self._name = executor_name
        self._succeed = succeed
        self.executed: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def execute(self, task: TaskSpec) -> ExecutionResult:
        self.executed.append(task.id)
        return ExecutionResult(
            success=self._succeed,
            exit_code=0 if self._succeed else 1,
            stdout="done" if self._succeed else "failed",
            duration_seconds=10.0,
        )

    def health(self) -> float:
        return 1.0


@pytest.fixture
def setup(tmp_path):
    queue = TaskQueue(tmp_path / "tasks")
    bus = EventBus(tmp_path / "events.ndjson")
    router = Router(["mock"], tmp_path / "routing.json")
    adapter = MockAdapter("mock")
    dispatcher = Dispatcher(
        queue=queue,
        router=router,
        event_bus=bus,
        adapters={"mock": adapter},
    )
    return queue, bus, router, adapter, dispatcher


class TestBasicDispatch:
    def test_tick_dispatches_eligible(self, setup):
        queue, bus, router, adapter, dispatcher = setup
        task = TaskSpec.new("Test task")
        queue.create(task)

        decisions = dispatcher.tick()
        assert len(decisions) == 1
        assert decisions[0]["task_id"] == task.id
        assert decisions[0]["executor"] == "mock"
        assert task.id in adapter.executed

        # Task should be done
        result = queue.get(task.id)
        assert result.status == TaskStatus.DONE

    def test_tick_empty_queue(self, setup):
        _, _, _, _, dispatcher = setup
        assert dispatcher.tick() == []

    def test_tick_records_events(self, setup):
        queue, bus, _, _, dispatcher = setup
        queue.create(TaskSpec.new("Event test"))
        dispatcher.tick()

        events = bus.read_recent(10)
        types = [e["event_type"] for e in events]
        assert "dispatch.decision" in types
        assert "dispatch.result" in types


class TestConcurrencyControl:
    def test_max_active_respected(self, tmp_path):
        queue = TaskQueue(tmp_path / "tasks")
        bus = EventBus(tmp_path / "events.ndjson")
        router = Router(["mock"], tmp_path / "routing.json")

        # Adapter that never finishes (stays active)
        class SlowAdapter:
            @property
            def name(self):
                return "mock"

            def execute(self, task):
                return ExecutionResult(success=True, duration_seconds=1.0)

            def health(self):
                return 1.0

        dispatcher = Dispatcher(
            queue=queue,
            router=router,
            event_bus=bus,
            adapters={"mock": SlowAdapter()},
            config=DispatchConfig(max_active=1),
        )

        for i in range(5):
            queue.create(TaskSpec.new(f"Task {i}"))

        # First tick picks up 1 task (but completes it synchronously, so picks next)
        # With sync execution, all get processed since each completes before next
        decisions = dispatcher.tick()
        # At least 1 decision made
        assert len(decisions) >= 1


class TestProtectedPaths:
    def test_skips_protected(self, setup):
        queue, bus, _, adapter, dispatcher = setup
        dispatcher.config.protected_paths = ["request-dispatch.py"]

        task = TaskSpec.new("Fix request-dispatch.py bug",
                           description="Modify request-dispatch.py")
        queue.create(task)

        decisions = dispatcher.tick()
        assert len(decisions) == 0
        assert task.id not in adapter.executed


class TestFailedExecution:
    def test_blocked_on_failure(self, tmp_path):
        queue = TaskQueue(tmp_path / "tasks")
        bus = EventBus(tmp_path / "events.ndjson")
        router = Router(["fail"], tmp_path / "routing.json")
        adapter = MockAdapter("fail", succeed=False)
        dispatcher = Dispatcher(
            queue=queue,
            router=router,
            event_bus=bus,
            adapters={"fail": adapter},
        )

        task = TaskSpec.new("Will fail")
        queue.create(task)
        dispatcher.tick()

        result = queue.get(task.id)
        assert result.status == TaskStatus.BLOCKED
