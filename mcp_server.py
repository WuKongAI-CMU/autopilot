#!/usr/bin/env python3
"""Autopilot MCP Server.

Exposes Autopilot's task queue and dispatch capabilities as MCP tools,
allowing Claude Code (or any MCP client) to create tasks, list them,
check status, and trigger dispatch cycles.

Usage:
    python mcp_server.py                    # stdio transport (default)
    python mcp_server.py --port 8420        # HTTP transport

Requires: pip install fastmcp
"""

from __future__ import annotations

import os
from pathlib import Path

from fastmcp import FastMCP

from autopilot_core.dispatcher import Dispatcher, DispatchConfig, ExecutionResult
from autopilot_core.event_bus import EventBus
from autopilot_core.queue import TaskQueue
from autopilot_core.router import Router
from autopilot_core.task import TaskPriority, TaskSpec

# Configuration from environment
DATA_DIR = Path(os.environ.get("AUTOPILOT_DATA_DIR", os.path.expanduser("~/.autopilot")))
TASKS_DIR = DATA_DIR / "tasks"
EVENTS_FILE = DATA_DIR / "events.ndjson"
ROUTING_TABLE = DATA_DIR / "routing-table.json"
EXECUTORS = os.environ.get("AUTOPILOT_EXECUTORS", "local").split(",")

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Initialize core components
queue = TaskQueue(TASKS_DIR)
bus = EventBus(EVENTS_FILE)
router = Router(EXECUTORS, ROUTING_TABLE)

# Create the MCP server
mcp = FastMCP(
    "Autopilot",
    instructions="Control plane for autonomous AI agent clusters. Create tasks, route to executors, track events.",
)


@mcp.tool()
def autopilot_create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    task_type: str = "general",
    executor: str = "",
    tags: str = "",
) -> dict:
    """Create a new task in the Autopilot queue.

    Args:
        title: Short task title.
        description: Detailed task description.
        priority: Task priority (critical, high, medium, low).
        task_type: Type of task (bugfix, feature, refactor, diagnostic, etc.).
        executor: Preferred executor (claude-code, kimi, codex, local). Empty = auto-route.
        tags: Comma-separated tags.

    Returns:
        Dict with task ID and status.
    """
    priority_map = {
        "critical": TaskPriority.CRITICAL,
        "high": TaskPriority.HIGH,
        "medium": TaskPriority.MEDIUM,
        "low": TaskPriority.LOW,
    }
    p = priority_map.get(priority.lower(), TaskPriority.MEDIUM)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    task = TaskSpec.new(
        title,
        priority=p,
        description=description,
        task_type=task_type,
        executor=executor if executor else None,
        tags=tag_list,
    )
    path = queue.create(task)

    bus.append("task.created", {
        "task_id": task.id,
        "title": title,
        "priority": priority,
        "task_type": task_type,
    }, source="mcp-server")

    return {
        "task_id": task.id,
        "status": "pending",
        "priority": priority,
        "path": str(path),
    }


@mcp.tool()
def autopilot_list_tasks(
    status_filter: str = "",
    limit: int = 20,
) -> list[dict]:
    """List tasks in the Autopilot queue.

    Args:
        status_filter: Filter by status (pending, claimed, in_progress, done, blocked). Empty = all.
        limit: Maximum number of tasks to return.

    Returns:
        List of task summaries.
    """
    tasks = queue.scan()

    if status_filter:
        tasks = [t for t in tasks if t.status.value == status_filter.lower()]

    tasks = tasks[:limit]

    return [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status.value,
            "priority": t.priority.value,
            "task_type": t.task_type or "general",
            "executor": t.executor or "auto",
        }
        for t in tasks
    ]


@mcp.tool()
def autopilot_task_status(task_id: str) -> dict:
    """Get detailed status of a specific task.

    Args:
        task_id: The task ID to look up.

    Returns:
        Full task details or error.
    """
    task = queue.get(task_id)
    if task is None:
        return {"error": f"Task {task_id} not found"}

    # Get routing recommendation
    choice = router.route(task)

    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "priority": task.priority.value,
        "task_type": task.task_type or "general",
        "executor": task.executor or "auto",
        "description": task.description or "",
        "tags": task.tags,
        "created_at": task.created_at,
        "routing_recommendation": {
            "executor": choice.executor,
            "confidence": choice.confidence,
            "reason": choice.reason,
        },
    }


@mcp.tool()
def autopilot_dispatch() -> dict:
    """Trigger a dispatch cycle — route and execute eligible tasks.

    This scans the queue for pending tasks, routes them to the best
    available executor, and dispatches them for execution.

    Returns:
        Summary of dispatch decisions made.
    """
    # Create a simple local adapter for dispatch
    class LocalDispatchAdapter:
        @property
        def name(self):
            return "local"

        def execute(self, task):
            import subprocess
            import time

            if not task.description:
                return ExecutionResult(success=False, exit_code=1, stderr="No command")
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    task.description, shell=True, capture_output=True,
                    text=True, timeout=60,
                )
                duration = time.monotonic() - start
                return ExecutionResult(
                    success=proc.returncode == 0,
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_seconds=round(duration, 2),
                )
            except subprocess.TimeoutExpired:
                return ExecutionResult(
                    success=False, exit_code=124,
                    stderr="Timed out",
                    duration_seconds=round(time.monotonic() - start, 2),
                )

        def health(self):
            return 1.0

    dispatcher = Dispatcher(
        queue=queue,
        router=router,
        event_bus=bus,
        adapters={"local": LocalDispatchAdapter()},
        config=DispatchConfig(max_active=3),
    )

    decisions = dispatcher.tick()

    return {
        "dispatched": len(decisions),
        "decisions": decisions,
    }


if __name__ == "__main__":
    import sys

    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 8420
        mcp.run(transport="http", port=port)
    else:
        mcp.run(transport="stdio")
