"""CLI entry point for Autopilot (autopilotctl).

Provides commands for task management, dispatch, event monitoring,
and agent-ops bridge integration (route, scan, ingest).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from autopilot_core.event_bus import EventBus
from autopilot_core.queue import TaskQueue
from autopilot_core.router import Router, classify_complexity
from autopilot_core.task import TaskPriority, TaskSpec, TaskStatus


def _default_data_dir() -> Path:
    import os
    return Path(os.environ.get("AUTOPILOT_DATA_DIR", ".")).resolve()


def cmd_task_create(args: argparse.Namespace) -> int:
    data_dir = _default_data_dir()
    queue = TaskQueue(data_dir / "tasks")
    task = TaskSpec.new(
        args.title,
        priority=TaskPriority(args.priority),
        executor=args.executor or None,
        task_type=args.type or None,
        description=args.description or "",
        timeout_minutes=args.timeout,
    )
    path = queue.create(task)
    print(f"Created: {task.id}")
    print(f"  File: {path}")
    return 0


def cmd_task_list(args: argparse.Namespace) -> int:
    data_dir = _default_data_dir()
    queue = TaskQueue(data_dir / "tasks")
    tasks = queue.scan()

    if args.status:
        tasks = [t for t in tasks if t.status.value == args.status]

    if not tasks:
        print("No tasks found.")
        return 0

    # Sort by priority
    tasks.sort(key=lambda t: t.priority.rank)

    for t in tasks:
        executor = t.executor or "auto"
        print(f"  [{t.status.value:12s}] {t.priority.value:8s} {t.id}  {t.title}  (executor={executor})")
    print(f"\nTotal: {len(tasks)}")
    return 0


def cmd_task_status(args: argparse.Namespace) -> int:
    data_dir = _default_data_dir()
    queue = TaskQueue(data_dir / "tasks")
    task = queue.get(args.task_id)
    if task is None:
        print(f"Task not found: {args.task_id}")
        return 1
    print(json.dumps(task.to_dict(), indent=2))
    return 0


def cmd_dispatch_tick(args: argparse.Namespace) -> int:
    from autopilot_core.dispatcher import Dispatcher, DispatchConfig
    from adapters.local import LocalAdapter

    data_dir = _default_data_dir()
    queue = TaskQueue(data_dir / "tasks")
    bus = EventBus(data_dir / "events.ndjson")
    router = Router(["local"], data_dir / "routing-table.json")
    adapter = LocalAdapter(cwd=data_dir)

    dispatcher = Dispatcher(
        queue=queue,
        router=router,
        event_bus=bus,
        adapters={"local": adapter},
        config=DispatchConfig(max_active=args.max_active),
    )

    decisions = dispatcher.tick()
    if not decisions:
        print("No tasks to dispatch.")
    else:
        for d in decisions:
            status = "OK" if d.get("success", True) else "FAIL"
            print(f"  Dispatched {d['task_id']} -> {d['executor']}")
    print(f"\nDispatched: {len(decisions)}")
    return 0


def cmd_events_tail(args: argparse.Namespace) -> int:
    data_dir = _default_data_dir()
    bus = EventBus(data_dir / "events.ndjson")
    events = bus.recent(args.limit, event_type=args.type or None)

    if not events:
        print("No events.")
        return 0

    for e in events:
        ts = e.get("timestamp", "?")[:19]
        etype = e.get("event_type", "?")
        source = e.get("source", "?")
        payload_str = json.dumps(e.get("payload", {}), ensure_ascii=False)
        if len(payload_str) > 120:
            payload_str = payload_str[:117] + "..."
        print(f"  [{ts}] {etype:25s} src={source:12s} {payload_str}")

    print(f"\nShowing {len(events)} events.")
    return 0


def _get_executors() -> list[str]:
    return os.environ.get("AUTOPILOT_EXECUTORS", "claude-code,kimi,local").split(",")


def cmd_route(args: argparse.Namespace) -> int:
    """Route an agent-ops request through Autopilot's complexity classifier."""
    # Add project root to path so bridge module is importable
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from bridge.agent_ops_bridge import request_to_task

    data_dir = _default_data_dir()
    router = Router(_get_executors(), data_dir / "routing-table.json")
    task = request_to_task(args.request_file)
    choice = router.route(task)

    result = {
        "task_id": task.id,
        "title": task.title,
        "priority": task.priority.value,
        "task_type": task.task_type or "general",
        "complexity": classify_complexity(task),
        "recommended_executor": choice.executor,
        "confidence": choice.confidence,
        "reason": choice.reason,
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_bridge_scan(args: argparse.Namespace) -> int:
    """Scan agent-ops requests and show routing decisions for pending tasks."""
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from bridge.agent_ops_bridge import scan_requests

    requests_dir = Path(args.requests_dir)
    tasks = scan_requests(requests_dir)
    pending = [t for t in tasks if t.status == TaskStatus.PENDING]

    data_dir = _default_data_dir()
    router = Router(_get_executors(), data_dir / "routing-table.json")

    results = []
    for task in pending:
        choice = router.route(task)
        results.append({
            "task_id": task.id,
            "title": task.title[:60],
            "priority": task.priority.value,
            "complexity": classify_complexity(task),
            "executor": choice.executor,
            "confidence": choice.confidence,
        })

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"  {r['task_id'][:50]:50s} {r['complexity']:5s} -> {r['executor']:12s} ({r['confidence']:.0%})")
        print(f"\nPending: {len(results)}")
    return 0


def cmd_bridge_ingest(args: argparse.Namespace) -> int:
    """Import pending agent-ops requests into Autopilot's task queue."""
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from bridge.agent_ops_bridge import scan_requests

    requests_dir = Path(args.requests_dir)
    tasks = scan_requests(requests_dir)
    pending = [t for t in tasks if t.status == TaskStatus.PENDING]

    data_dir = _default_data_dir()
    queue = TaskQueue(data_dir / "tasks")
    bus = EventBus(data_dir / "events.ndjson")

    imported = 0
    for task in pending:
        existing = queue.get(task.id)
        if existing:
            continue
        queue.create(task)
        bus.append("task.imported", {
            "task_id": task.id,
            "source": "agent-ops",
            "title": task.title,
        }, source="bridge")
        imported += 1

    skipped = len(pending) - imported
    print(f"Imported {imported} tasks ({len(pending)} pending, {skipped} already in queue)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autopilotctl",
        description="Autopilot — control plane for autonomous AI agent clusters",
    )
    parser.add_argument("--version", action="version", version="autopilot-core 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    # --- task ---
    task_parser = subparsers.add_parser("task", help="Task management")
    task_sub = task_parser.add_subparsers(dest="task_command")

    # task create
    create = task_sub.add_parser("create", help="Create a new task")
    create.add_argument("--title", required=True, help="Task title")
    create.add_argument("--priority", default="medium", choices=["critical", "high", "medium", "low"])
    create.add_argument("--executor", default="", help="Executor (claude-code, codex, local, or empty for auto)")
    create.add_argument("--type", default="", help="Task type (bugfix, feature, refactor, etc.)")
    create.add_argument("--description", default="", help="Task description / command")
    create.add_argument("--timeout", type=int, default=30, help="Timeout in minutes")
    create.set_defaults(func=cmd_task_create)

    # task list
    ls = task_sub.add_parser("list", help="List tasks")
    ls.add_argument("--status", default="", help="Filter by status")
    ls.set_defaults(func=cmd_task_list)

    # task status
    status = task_sub.add_parser("status", help="Show task details")
    status.add_argument("task_id", help="Task ID")
    status.set_defaults(func=cmd_task_status)

    # --- dispatch ---
    dispatch_parser = subparsers.add_parser("dispatch", help="Dispatch operations")
    dispatch_sub = dispatch_parser.add_subparsers(dest="dispatch_command")

    tick = dispatch_sub.add_parser("tick", help="Run one dispatch cycle")
    tick.add_argument("--max-active", type=int, default=3, help="Max concurrent tasks")
    tick.set_defaults(func=cmd_dispatch_tick)

    # --- route ---
    route_parser = subparsers.add_parser("route", help="Route an agent-ops request file")
    route_parser.add_argument("request_file", help="Path to agent-ops request file (.md/.yaml)")
    route_parser.set_defaults(func=cmd_route)

    # --- bridge ---
    bridge_parser = subparsers.add_parser("bridge", help="Agent-ops bridge operations")
    bridge_sub = bridge_parser.add_subparsers(dest="bridge_command")

    default_requests = os.path.expanduser("~/agent-ops/requests")

    scan = bridge_sub.add_parser("scan", help="Scan agent-ops requests and show routing decisions")
    scan.add_argument("--requests-dir", default=default_requests, help="Agent-ops requests directory")
    scan.add_argument("--json", action="store_true", help="Output as JSON")
    scan.set_defaults(func=cmd_bridge_scan)

    ingest = bridge_sub.add_parser("ingest", help="Import pending requests into Autopilot queue")
    ingest.add_argument("--requests-dir", default=default_requests, help="Agent-ops requests directory")
    ingest.set_defaults(func=cmd_bridge_ingest)

    # --- events ---
    events_parser = subparsers.add_parser("events", help="Event bus operations")
    events_sub = events_parser.add_subparsers(dest="events_command")

    tail = events_sub.add_parser("tail", help="Show recent events")
    tail.add_argument("--limit", type=int, default=20, help="Number of events")
    tail.add_argument("--type", default="", help="Filter by event type")
    tail.set_defaults(func=cmd_events_tail)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    func = getattr(args, "func", None)
    if func is None:
        parser.parse_args([args.command, "--help"])
        return 0

    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
