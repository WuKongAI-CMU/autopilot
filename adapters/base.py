"""Base adapter interface for Autopilot executors."""

from __future__ import annotations

from abc import ABC, abstractmethod

from autopilot_core.dispatcher import ExecutionResult
from autopilot_core.task import TaskSpec


class BaseAdapter(ABC):
    """Abstract base class for executor adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this executor."""
        ...

    @abstractmethod
    def execute(self, task: TaskSpec) -> ExecutionResult:
        """Execute a task and return the result."""
        ...

    def health(self) -> float:
        """Return health score 0.0-1.0. Override to implement health checks."""
        return 1.0
