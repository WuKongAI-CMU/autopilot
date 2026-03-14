"""Capability-based routing engine for Autopilot.

Selects the best executor for a task based on explicit constraints,
health scores, and historical performance. Routing table is persisted
as JSON for learning across ticks.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopilot_core.task import TaskSpec


@dataclass
class ExecutorChoice:
    """Result of routing a task to an executor."""

    executor: str
    confidence: float  # 0.0 to 1.0
    reason: str


@dataclass
class ExecutorStats:
    """Performance stats for an executor on a given task_type."""

    runs: int = 0
    done: int = 0
    blocked: int = 0
    total_duration_s: float = 0.0

    @property
    def done_ratio(self) -> float:
        return self.done / self.runs if self.runs > 0 else 0.0

    @property
    def avg_duration_s(self) -> float:
        return self.total_duration_s / self.done if self.done > 0 else 0.0


@dataclass
class RoutingTable:
    """Persistent routing table tracking executor performance per task_type."""

    stats: dict[str, dict[str, ExecutorStats]] = field(default_factory=dict)
    # stats[executor_name][task_type] = ExecutorStats

    def record(
        self,
        executor: str,
        task_type: str,
        *,
        success: bool,
        duration_s: float = 0.0,
    ) -> None:
        """Record an execution outcome."""
        if executor not in self.stats:
            self.stats[executor] = {}
        if task_type not in self.stats[executor]:
            self.stats[executor][task_type] = ExecutorStats()

        s = self.stats[executor][task_type]
        s.runs += 1
        if success:
            s.done += 1
            s.total_duration_s += duration_s
        else:
            s.blocked += 1

    def score(self, executor: str, task_type: str) -> float:
        """Compute a routing score for an executor on a task type."""
        s = self.stats.get(executor, {}).get(task_type)
        if s is None or s.runs == 0:
            return 0.5  # No data — neutral

        sample_weight = min(1.0, s.runs / 12.0)
        return s.done_ratio * sample_weight

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for executor, types in self.stats.items():
            result[executor] = {}
            for task_type, stats in types.items():
                result[executor][task_type] = asdict(stats)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingTable:
        table = cls()
        for executor, types in data.items():
            table.stats[executor] = {}
            for task_type, stats_dict in types.items():
                table.stats[executor][task_type] = ExecutorStats(**stats_dict)
        return table

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> RoutingTable:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return cls()


# Default executor tiers: maps executor name to capability level
DEFAULT_EXECUTOR_TIERS: dict[str, str] = {
    "claude-code": "high",
    "codex": "high",
    "kimi": "low",
    "local": "any",
}

# Task types that indicate high complexity
HIGH_COMPLEXITY_TYPES = frozenset({
    "architecture", "refactor", "security", "design",
    "debugging", "integration", "migration",
})

# Task types that indicate low complexity
LOW_COMPLEXITY_TYPES = frozenset({
    "diagnostic", "cleanup", "docs", "formatting",
    "lint", "test-only", "typo", "chore",
})


def classify_complexity(task: TaskSpec) -> str:
    """Classify a task's complexity as 'high' or 'low'.

    Uses task_type, priority, and description length as signals:
    - High: architecture/refactor/security/debugging + critical/high priority
    - Low: diagnostic/cleanup/docs/formatting + low/medium priority
    - Ambiguous cases default based on priority (high/critical → 'high', else 'low')
    """
    task_type = (task.task_type or "").lower()
    priority = task.priority

    # Explicit type signals
    if task_type in HIGH_COMPLEXITY_TYPES:
        return "high"
    if task_type in LOW_COMPLEXITY_TYPES:
        return "low"

    # Priority-based fallback
    from autopilot_core.task import TaskPriority
    if priority in (TaskPriority.CRITICAL, TaskPriority.HIGH):
        return "high"

    # Description length as a weak signal (long descriptions = complex)
    desc_len = len(task.description or "")
    if desc_len > 500:
        return "high"

    return "low"


class Router:
    """Routes tasks to executors based on constraints, health, and history.

    Args:
        executors: List of available executor names (e.g., ["claude-code", "codex"]).
        routing_table_path: Path to persist the routing table JSON.
        executor_tiers: Maps executor names to capability tiers ('high', 'low', 'any').
            Defaults to claude-code=high, kimi=low, codex=high, local=any.
    """

    def __init__(
        self,
        executors: list[str],
        routing_table_path: str | Path | None = None,
        executor_tiers: dict[str, str] | None = None,
    ):
        self.executors = list(executors)
        self._table_path = Path(
            routing_table_path
            or os.environ.get("AUTOPILOT_ROUTING_TABLE", "routing-table.json")
        )
        self._table = RoutingTable.load(self._table_path)
        self._tiers = dict(DEFAULT_EXECUTOR_TIERS)
        if executor_tiers:
            self._tiers.update(executor_tiers)

    def route(self, task: TaskSpec) -> ExecutorChoice:
        """Select the best executor for a task.

        Routing layers:
        1. Explicit constraint (task.executor is set)
        2. Tier-based filtering (match task complexity to executor capability)
        3. Score-based selection (historical performance)
        """
        # Layer 1: Explicit constraint
        if task.executor and task.executor in self.executors:
            return ExecutorChoice(
                executor=task.executor,
                confidence=1.0,
                reason=f"explicit: task.executor={task.executor}",
            )

        # If explicit executor is set but not available, fall through to routing
        task_type = task.task_type or "general"
        complexity = classify_complexity(task)

        # Layer 2: Tier-based filtering
        # Filter executors to those matching the task complexity
        tier_matched = []
        tier_fallback = []
        for executor in self.executors:
            tier = self._tiers.get(executor, "any")
            if tier == "any" or tier == complexity:
                tier_matched.append(executor)
            elif complexity == "high" and tier == "low":
                # Low-tier executors are fallback for high-complexity tasks
                tier_fallback.append(executor)
            else:
                # High-tier executors can handle low-complexity tasks (fallback)
                tier_fallback.append(executor)

        candidates = tier_matched if tier_matched else tier_fallback
        if not candidates:
            candidates = list(self.executors)  # Last resort: all executors

        # Layer 3: Score-based routing among candidates
        scores: list[tuple[str, float]] = []
        for executor in candidates:
            score = self._table.score(executor, task_type)
            scores.append((executor, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        if not scores:
            return ExecutorChoice(
                executor=self.executors[0] if self.executors else "unknown",
                confidence=0.0,
                reason="no executors available",
            )

        best_name, best_score = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else 0.0
        margin = best_score - second_score

        # Confidence based on score and margin
        confidence = min(1.0, best_score + margin * 0.5)

        return ExecutorChoice(
            executor=best_name,
            confidence=round(confidence, 3),
            reason=f"routed: complexity={complexity} score={best_score:.3f} margin={margin:.3f} task_type={task_type}",
        )

    def record_outcome(
        self,
        executor: str,
        task_type: str,
        *,
        success: bool,
        duration_s: float = 0.0,
    ) -> None:
        """Record an execution outcome for future routing decisions."""
        self._table.record(executor, task_type, success=success, duration_s=duration_s)
        try:
            self._table.save(self._table_path)
        except OSError:
            pass  # Best-effort persistence

    @property
    def table(self) -> RoutingTable:
        return self._table
