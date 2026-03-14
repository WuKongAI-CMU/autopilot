"""Task model and state machine for Autopilot.

Tasks are the fundamental unit of work. They can be serialized to/from
YAML-frontmatter Markdown files or JSON, matching the proven agent-ops pattern.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {
            TaskPriority.CRITICAL: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 3,
        }[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TaskPriority):
            return NotImplemented
        return self.rank < other.rank


# Valid status transitions
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.CLAIMED, TaskStatus.CANCELLED},
    TaskStatus.CLAIMED: {TaskStatus.IN_PROGRESS, TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}


@dataclass
class TaskSpec:
    """Specification for a single task in the Autopilot queue."""

    id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    executor: str | None = None
    repo: str | None = None
    task_type: str | None = None
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timeout_minutes: int = 30
    depends_on: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @staticmethod
    def new(title: str, **kwargs: Any) -> TaskSpec:
        """Factory that auto-generates an ID in the format req-YYYYMMDD-xxxxxxxx."""
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        short_id = uuid.uuid4().hex[:8]
        task_id = f"req-{date_str}-{short_id}"
        return TaskSpec(id=task_id, title=title, **kwargs)

    def transition(self, new_status: TaskStatus) -> None:
        """Validate and apply a status transition."""
        if isinstance(new_status, str):
            new_status = TaskStatus(new_status)
        allowed = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {self.status.value} -> {new_status.value}. "
                f"Allowed: {sorted(s.value for s in allowed)}"
            )
        self.status = new_status

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict."""
        d = asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskSpec:
        """Create from a dict."""
        d = dict(data)
        if "status" in d:
            d["status"] = TaskStatus(d["status"])
        if "priority" in d:
            d["priority"] = TaskPriority(d["priority"])
        # Handle list fields that might be None
        for list_field in ("depends_on", "acceptance_criteria", "tags"):
            if d.get(list_field) is None:
                d[list_field] = []
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> TaskSpec:
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(text))

    def to_markdown(self) -> str:
        """Serialize to Markdown with YAML frontmatter."""
        lines = ["---"]
        lines.append(f"id: {self.id}")
        lines.append(f"title: {self.title}")
        lines.append(f"status: {self.status.value}")
        lines.append(f"priority: {self.priority.value}")
        if self.executor:
            lines.append(f"executor: {self.executor}")
        if self.repo:
            lines.append(f"repo: {self.repo}")
        if self.task_type:
            lines.append(f"task_type: {self.task_type}")
        lines.append(f"created_at: {self.created_at}")
        lines.append(f"timeout_minutes: {self.timeout_minutes}")
        if self.depends_on:
            lines.append(f"depends_on: [{', '.join(self.depends_on)}]")
        if self.tags:
            lines.append(f"tags: [{', '.join(self.tags)}]")
        if self.acceptance_criteria:
            lines.append("acceptance_criteria:")
            for criterion in self.acceptance_criteria:
                lines.append(f"  - {criterion}")
        lines.append("---")
        if self.description:
            lines.append("")
            lines.append(self.description)
        return "\n".join(lines) + "\n"

    @classmethod
    def from_markdown(cls, text: str) -> TaskSpec:
        """Parse from Markdown with YAML frontmatter."""
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
        if not match:
            raise ValueError("Invalid frontmatter format: missing --- delimiters")

        frontmatter_text = match.group(1)
        body = match.group(2).strip()

        data = _parse_simple_yaml(frontmatter_text)
        data["description"] = body
        return cls.from_dict(data)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML-subset parser for flat key-value pairs and simple lists.

    Handles:
    - key: value
    - key: [item1, item2]
    - key:\\n  - item1\\n  - item2
    - Numeric values (int)
    - null/None values
    """
    result: dict[str, Any] = {}
    lines = text.split("\n")
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in lines:
        # Check for list continuation
        list_match = re.match(r"^\s+-\s+(.+)$", line)
        if list_match and current_key is not None:
            if current_list is None:
                current_list = []
            current_list.append(list_match.group(1).strip())
            result[current_key] = current_list
            continue

        # Flush any pending list
        current_key = None
        current_list = None

        # Key-value pair
        kv_match = re.match(r"^(\w[\w_]*)\s*:\s*(.*)$", line)
        if not kv_match:
            continue

        key = kv_match.group(1)
        value_str = kv_match.group(2).strip()

        # Inline list: [item1, item2]
        inline_list = re.match(r"^\[(.*)\]$", value_str)
        if inline_list:
            items = [s.strip() for s in inline_list.group(1).split(",") if s.strip()]
            result[key] = items
            continue

        # Empty value (might be followed by list items)
        if not value_str:
            current_key = key
            result[key] = None
            continue

        # Null
        if value_str.lower() in ("null", "none", "~"):
            result[key] = None
            continue

        # Integer
        try:
            result[key] = int(value_str)
            continue
        except ValueError:
            pass

        # String
        result[key] = value_str

    return result
