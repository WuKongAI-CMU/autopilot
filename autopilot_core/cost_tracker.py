"""Cost tracking for Autopilot task execution.

Records per-task and per-model cost events via the EventBus.
Provides cost breakdowns for auditing and optimization.

Zero external dependencies — uses EventBus for storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autopilot_core.event_bus import EventBus


@dataclass
class CostEntry:
    """A single cost record."""

    task_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostTracker:
    """Tracks execution costs via the EventBus.

    Args:
        event_bus: The EventBus instance to store cost events.
    """

    EVENT_TYPE = "cost.recorded"

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus

    def record_spend(
        self,
        task_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> str:
        """Record a cost event for a task execution.

        Returns the event ID.
        """
        return self._bus.append(
            self.EVENT_TYPE,
            {
                "task_id": task_id,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost_usd, 6),
            },
            source="cost-tracker",
        )

    def get_task_cost(self, task_id: str) -> float:
        """Get total cost for a specific task."""
        events = self._bus.recent(500, event_type=self.EVENT_TYPE)
        total = 0.0
        for e in events:
            payload = e.get("payload", {})
            if payload.get("task_id") == task_id:
                total += payload.get("cost_usd", 0.0)
        return round(total, 6)

    def get_model_costs(self) -> dict[str, dict[str, Any]]:
        """Get cost breakdown by model.

        Returns dict like:
        {
            "claude-opus": {"total_usd": 1.23, "total_input_tokens": 5000, "total_output_tokens": 2000, "count": 5},
            "kimi-k2.5": {"total_usd": 0.12, ...},
        }
        """
        events = self._bus.recent(1000, event_type=self.EVENT_TYPE)
        models: dict[str, dict[str, Any]] = {}
        for e in events:
            payload = e.get("payload", {})
            model = payload.get("model", "unknown")
            if model not in models:
                models[model] = {
                    "total_usd": 0.0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "count": 0,
                }
            m = models[model]
            m["total_usd"] += payload.get("cost_usd", 0.0)
            m["total_input_tokens"] += payload.get("input_tokens", 0)
            m["total_output_tokens"] += payload.get("output_tokens", 0)
            m["count"] += 1

        # Round totals
        for m in models.values():
            m["total_usd"] = round(m["total_usd"], 6)

        return models

    def get_summary(self) -> dict[str, Any]:
        """Get overall cost summary."""
        model_costs = self.get_model_costs()
        total_usd = sum(m["total_usd"] for m in model_costs.values())
        total_tasks = len({
            e.get("payload", {}).get("task_id")
            for e in self._bus.recent(1000, event_type=self.EVENT_TYPE)
            if e.get("payload", {}).get("task_id")
        })
        return {
            "total_usd": round(total_usd, 6),
            "total_tasks": total_tasks,
            "by_model": model_costs,
        }
