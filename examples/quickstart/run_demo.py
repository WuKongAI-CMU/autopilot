#!/usr/bin/env python3
"""Autopilot Quickstart Demo.

Demonstrates the core flow: create task -> dispatch -> execute -> done.
Uses the local executor to run a simple shell command.
"""

import sys
import tempfile
from pathlib import Path

# Add project root to path for demo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from autopilot_core.dispatcher import Dispatcher, DispatchConfig, ExecutionResult
from autopilot_core.event_bus import EventBus
from autopilot_core.queue import TaskQueue
from autopilot_core.router import Router
from autopilot_core.task import TaskPriority, TaskSpec


def main():
    # Use a temp directory for demo data
    data_dir = Path(tempfile.mkdtemp(prefix="autopilot-demo-"))
    print(f"Demo data dir: {data_dir}\n")

    # Initialize components
    queue = TaskQueue(data_dir / "tasks")
    bus = EventBus(data_dir / "events.ndjson")
    router = Router(["local"], data_dir / "routing-table.json")

    # Create a simple local adapter
    class DemoAdapter:
        @property
        def name(self):
            return "local"

        def execute(self, task):
            import subprocess
            import time

            start = time.monotonic()
            proc = subprocess.run(
                task.description, shell=True, capture_output=True, text=True, timeout=60
            )
            duration = time.monotonic() - start
            return ExecutionResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_seconds=round(duration, 2),
            )

        def health(self):
            return 1.0

    dispatcher = Dispatcher(
        queue=queue,
        router=router,
        event_bus=bus,
        adapters={"local": DemoAdapter()},
        config=DispatchConfig(max_active=2),
    )

    # Step 1: Create tasks
    print("=== Step 1: Creating tasks ===")
    t1 = TaskSpec.new(
        "List Python version",
        priority=TaskPriority.HIGH,
        description="python3 --version",
        task_type="diagnostic",
    )
    t2 = TaskSpec.new(
        "Show current date",
        priority=TaskPriority.MEDIUM,
        description="date",
        task_type="diagnostic",
    )
    queue.create(t1)
    queue.create(t2)
    print(f"  Created: {t1.id} — {t1.title}")
    print(f"  Created: {t2.id} — {t2.title}")

    # Step 2: Show queue
    print("\n=== Step 2: Queue status ===")
    for task in queue.scan():
        print(f"  [{task.status.value}] {task.priority.value:8s} {task.id}")

    # Step 3: Dispatch
    print("\n=== Step 3: Dispatching ===")
    decisions = dispatcher.tick()
    for d in decisions:
        print(f"  Dispatched: {d['task_id']} -> {d['executor']} (confidence={d['confidence']})")

    # Step 4: Check results
    print("\n=== Step 4: Results ===")
    for task in queue.scan():
        print(f"  [{task.status.value:12s}] {task.id} — {task.title}")

    # Step 5: Event trail
    print("\n=== Step 5: Event trail ===")
    events = bus.read_recent(20)
    for e in events:
        ts = e["timestamp"][:19]
        print(f"  [{ts}] {e['event_type']}")

    print(f"\nDemo complete. {len(decisions)} tasks dispatched, {len(events)} events recorded.")
    print(f"Data: {data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
