"""Dispatcher — connects the task queue to executors via routing.

Implements the core tick loop: scan queue -> route -> dispatch -> record events.
Handles concurrency slots, lease-based claiming, and protected paths.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from autopilot_core.event_bus import EventBus
from autopilot_core.queue import TaskQueue
from autopilot_core.router import ExecutorChoice, Router
from autopilot_core.task import TaskSpec, TaskStatus


class ExecutorAdapter(Protocol):
    """Protocol for executor adapters."""

    @property
    def name(self) -> str: ...

    def execute(self, task: TaskSpec) -> ExecutionResult: ...

    def health(self) -> float:
        """Return health score 0.0-1.0. Default healthy."""
        ...


@dataclass
class ExecutionResult:
    """Result of executing a task."""

    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    files_changed: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class DispatchConfig:
    """Configuration for the dispatcher."""

    max_active: int = 3
    protected_paths: list[str] = field(default_factory=list)


class Dispatcher:
    """Dispatches eligible tasks from the queue to executors.

    Args:
        queue: TaskQueue to scan for eligible tasks.
        router: Router to select executors.
        event_bus: EventBus for audit trail.
        adapters: Dict of executor name -> adapter instance.
        config: Dispatch configuration.
    """

    def __init__(
        self,
        queue: TaskQueue,
        router: Router,
        event_bus: EventBus,
        adapters: dict[str, ExecutorAdapter] | None = None,
        config: DispatchConfig | None = None,
    ):
        self.queue = queue
        self.router = router
        self.event_bus = event_bus
        self.adapters = adapters or {}
        self.config = config or DispatchConfig()
        self._active: dict[str, str] = {}  # task_id -> executor_name

    @property
    def active_count(self) -> int:
        return len(self._active)

    def tick(self) -> list[dict[str, Any]]:
        """Run one dispatch cycle. Returns list of dispatch decisions."""
        decisions: list[dict[str, Any]] = []

        eligible = self.queue.eligible()
        if not eligible:
            return decisions

        for task in eligible:
            if self.active_count >= self.config.max_active:
                self.event_bus.append(
                    "dispatch.skipped",
                    {"task_id": task.id, "reason": "max_active reached"},
                )
                break

            # Check protected paths
            if self._touches_protected(task):
                self.event_bus.append(
                    "dispatch.skipped",
                    {"task_id": task.id, "reason": "touches protected paths"},
                )
                continue

            # Check dependencies
            if not self._deps_satisfied(task):
                continue

            # Route
            choice = self.router.route(task)

            if choice.executor not in self.adapters:
                self.event_bus.append(
                    "dispatch.skipped",
                    {
                        "task_id": task.id,
                        "reason": f"executor '{choice.executor}' not available",
                        "routing": choice.reason,
                    },
                )
                continue

            # Claim
            self.queue.update(task.id, status=TaskStatus.CLAIMED)
            self._active[task.id] = choice.executor

            decision = {
                "task_id": task.id,
                "executor": choice.executor,
                "confidence": choice.confidence,
                "reason": choice.reason,
            }
            self.event_bus.append("dispatch.decision", decision)
            decisions.append(decision)

            # Execute
            self.queue.update(task.id, status=TaskStatus.IN_PROGRESS)
            adapter = self.adapters[choice.executor]
            result = adapter.execute(task)

            # Record outcome
            new_status = TaskStatus.DONE if result.success else TaskStatus.BLOCKED
            self.queue.update(task.id, status=new_status)
            self._active.pop(task.id, None)

            self.router.record_outcome(
                choice.executor,
                task.task_type or "general",
                success=result.success,
                duration_s=result.duration_seconds,
            )

            self.event_bus.append(
                "dispatch.result",
                {
                    "task_id": task.id,
                    "executor": choice.executor,
                    "success": result.success,
                    "exit_code": result.exit_code,
                    "duration_s": result.duration_seconds,
                    "files_changed": result.files_changed,
                },
            )

        return decisions

    def _touches_protected(self, task: TaskSpec) -> bool:
        if not self.config.protected_paths:
            return False
        desc = (task.description + " " + task.title).lower()
        for protected in self.config.protected_paths:
            if protected.lower() in desc:
                return True
        return False

    def _deps_satisfied(self, task: TaskSpec) -> bool:
        if not task.depends_on:
            return True
        for dep_id in task.depends_on:
            dep = self.queue.get(dep_id)
            if dep is None or dep.status != TaskStatus.DONE:
                return False
        return True
