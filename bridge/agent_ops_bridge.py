"""Bridge between agent-ops request format and Autopilot TaskSpec.

Agent-ops uses YAML frontmatter + Markdown body for requests.
This bridge converts them to/from Autopilot TaskSpec format
so Autopilot can dispatch agent-ops tasks through its routing engine.

Zero external dependencies — uses autopilot_core's built-in YAML parser.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopilot_core.task import TaskPriority, TaskSpec, TaskStatus


# Maps agent-ops executor names to Autopilot executor names
EXECUTOR_MAP = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codex": "codex",
    "kimi": "kimi",
    "either": "",  # Let the router decide
    "any": "",
}

# Maps agent-ops priority strings to TaskPriority
PRIORITY_MAP = {
    "critical": TaskPriority.CRITICAL,
    "high": TaskPriority.HIGH,
    "medium": TaskPriority.MEDIUM,
    "low": TaskPriority.LOW,
}


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a request file.

    Returns (metadata_dict, body_text).
    """
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    yaml_block = text[4:end]
    body = text[end + 4:].strip()

    # Simple YAML parser (matching autopilot_core.task pattern)
    meta: dict[str, Any] = {}
    for line in yaml_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Handle lists
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            meta[key] = items
        # Handle booleans
        elif value.lower() in ("true", "false"):
            meta[key] = value.lower() == "true"
        # Handle numbers
        elif value.isdigit():
            meta[key] = int(value)
        else:
            meta[key] = value.strip("'\"")

    return meta, body


def request_to_task(request_path: str | Path) -> TaskSpec:
    """Convert an agent-ops request file to an Autopilot TaskSpec.

    Args:
        request_path: Path to a .yaml or .md request file.

    Returns:
        TaskSpec ready for dispatch.
    """
    path = Path(request_path)
    content = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(content)

    # Map fields
    request_id = meta.get("id", path.stem)
    priority_str = str(meta.get("priority", "medium")).lower()
    priority = PRIORITY_MAP.get(priority_str, TaskPriority.MEDIUM)

    executor_raw = str(meta.get("executor", "")).lower()
    executor = EXECUTOR_MAP.get(executor_raw, executor_raw)

    # Extract title from first markdown heading or filename
    title = path.stem
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Map status
    status_str = str(meta.get("status", "pending")).lower()
    status_map = {
        "pending": TaskStatus.PENDING,
        "claimed": TaskStatus.CLAIMED,
        "in_progress": TaskStatus.IN_PROGRESS,
        "in-progress": TaskStatus.IN_PROGRESS,
        "done": TaskStatus.DONE,
        "blocked": TaskStatus.BLOCKED,
        "cancelled": TaskStatus.CANCELLED,
    }
    status = status_map.get(status_str, TaskStatus.PENDING)

    # Build task type from filename pattern
    task_type = meta.get("task_type", "general")
    if task_type == "general":
        # Try to infer from filename: ci-fix-*, pr-review-*, req-*
        stem = path.stem.lower()
        if "ci-fix" in stem:
            task_type = "bugfix"
        elif "pr-review" in stem:
            task_type = "review"
        elif "setup" in stem or "deploy" in stem:
            task_type = "integration"

    repo = meta.get("repo", meta.get("cwd", ""))

    return TaskSpec(
        id=request_id,
        title=title,
        status=status,
        priority=priority,
        executor=executor if executor else None,
        repo=str(repo) if repo else None,
        task_type=task_type,
        description=body,
        created_at=meta.get("created", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        timeout_minutes=int(meta.get("timeout_minutes", 30)),
        tags=meta.get("tags", []),
    )


def task_to_request(task: TaskSpec, output_dir: str | Path) -> Path:
    """Convert an Autopilot TaskSpec back to agent-ops request format.

    Args:
        task: The TaskSpec to convert.
        output_dir: Directory to write the request file.

    Returns:
        Path to the written request file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Reverse executor mapping
    reverse_executor = {v: k for k, v in EXECUTOR_MAP.items() if v}
    executor_str = reverse_executor.get(task.executor or "", task.executor or "either")

    lines = [
        "---",
        f"id: {task.id}",
        f"status: {task.status.value}",
        f"priority: {task.priority.value}",
    ]
    if task.repo:
        lines.append(f"repo: {task.repo}")
        lines.append(f"cwd: {task.repo}")
    lines.append(f"executor: {executor_str}")
    lines.append(f"created: {task.created_at}")
    if task.tags:
        lines.append(f"tags: [{', '.join(task.tags)}]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {task.title}")
    lines.append("")
    if task.description:
        lines.append(task.description)

    filename = f"{task.id}.yaml"
    path = output_dir / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def scan_requests(requests_dir: str | Path) -> list[TaskSpec]:
    """Scan an agent-ops requests directory and convert all to TaskSpecs.

    Skips archived requests, README, and non-request files.
    """
    requests_dir = Path(requests_dir)
    if not requests_dir.exists():
        return []

    tasks = []
    for path in sorted(requests_dir.iterdir()):
        # Skip directories, READMEs, and non-request files
        if path.is_dir():
            continue
        if path.name.upper().startswith("README"):
            continue
        if path.suffix not in (".yaml", ".yml", ".md"):
            continue
        if path.name.startswith("_") or path.name.startswith("."):
            continue
        # Skip non-request .md files (like opus.md which is messages)
        if path.suffix == ".md" and not path.stem.startswith("req-"):
            # Check if it has frontmatter with id/status
            try:
                content = path.read_text(encoding="utf-8")
                if not content.startswith("---"):
                    continue
                meta, _ = _parse_frontmatter(content)
                if "status" not in meta:
                    continue
            except (OSError, UnicodeDecodeError):
                continue

        try:
            task = request_to_task(path)
            tasks.append(task)
        except (OSError, UnicodeDecodeError, KeyError):
            continue

    return tasks
