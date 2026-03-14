"""File-based task queue for Autopilot.

Tasks are stored as Markdown files with YAML frontmatter in a configurable
directory. The queue provides scanning, creation, updating, and filtering.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from autopilot_core.task import TaskPriority, TaskSpec, TaskStatus


class TaskQueue:
    """File-based task queue backed by a directory of Markdown task files.

    Args:
        tasks_dir: Directory where task files are stored.
            Created automatically on first write.
    """

    def __init__(self, tasks_dir: str | Path | None = None):
        if tasks_dir is None:
            tasks_dir = os.environ.get("AUTOPILOT_TASKS_DIR", "tasks")
        self.tasks_dir = Path(tasks_dir).expanduser().resolve()

    def _ensure_dir(self) -> None:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def scan(self) -> list[TaskSpec]:
        """Read all task files and return list of TaskSpec objects."""
        if not self.tasks_dir.exists():
            return []

        tasks: list[TaskSpec] = []
        for f in sorted(self.tasks_dir.iterdir()):
            if f.suffix != ".md":
                continue
            try:
                text = f.read_text(encoding="utf-8")
                task = TaskSpec.from_markdown(text)
                tasks.append(task)
            except (ValueError, OSError, KeyError):
                continue  # Skip malformed files
        return tasks

    def get(self, task_id: str) -> TaskSpec | None:
        """Get a specific task by ID."""
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            return TaskSpec.from_markdown(text)
        except (ValueError, OSError):
            return None

    def create(self, spec: TaskSpec) -> Path:
        """Write a new task file. Returns the file path."""
        self._ensure_dir()
        path = self._task_path(spec.id)
        path.write_text(spec.to_markdown(), encoding="utf-8")
        return path

    def update(self, task_id: str, **fields: Any) -> TaskSpec | None:
        """Update task fields and write back. Returns updated task or None."""
        task = self.get(task_id)
        if task is None:
            return None

        for key, value in fields.items():
            if key == "status":
                new_status = TaskStatus(value) if isinstance(value, str) else value
                task.transition(new_status)
            elif hasattr(task, key):
                setattr(task, key, value)

        path = self._task_path(task_id)
        path.write_text(task.to_markdown(), encoding="utf-8")
        return task

    def eligible(self) -> list[TaskSpec]:
        """Return tasks eligible for dispatch, sorted by priority."""
        eligible_statuses = {TaskStatus.PENDING, TaskStatus.CLAIMED}
        tasks = [t for t in self.scan() if t.status in eligible_statuses]
        return sorted(tasks, key=lambda t: t.priority.rank)

    def by_status(self, status: TaskStatus) -> list[TaskSpec]:
        """Filter tasks by status."""
        return [t for t in self.scan() if t.status == status]

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.md"
