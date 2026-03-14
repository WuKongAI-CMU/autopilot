"""Claude Code executor adapter.

Runs tasks via `claude -p` (headless mode) with configurable flags.
Pre-generates session IDs for resumability.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from adapters.base import BaseAdapter
from autopilot_core.dispatcher import ExecutionResult
from autopilot_core.task import TaskSpec


class ClaudeCodeAdapter(BaseAdapter):
    """Executes tasks via Claude Code CLI in headless mode.

    Args:
        claude_bin: Path to the claude binary. Defaults to "claude".
        output_format: Output format (text, json, stream-json).
        max_turns: Maximum conversation turns.
        default_cwd: Default working directory.
        extra_flags: Additional CLI flags to pass.
    """

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        output_format: str = "text",
        max_turns: int | None = None,
        default_cwd: str | Path | None = None,
        extra_flags: list[str] | None = None,
    ):
        self._bin = claude_bin
        self._output_format = output_format
        self._max_turns = max_turns
        self._default_cwd = Path(default_cwd).resolve() if default_cwd else None
        self._extra_flags = extra_flags or []

    @property
    def name(self) -> str:
        return "claude-code"

    def _build_command(self, task: TaskSpec, session_id: str) -> list[str]:
        """Build the claude CLI command."""
        cmd = [self._bin, "-p"]

        # Prompt from task
        prompt = self._build_prompt(task)
        cmd.append(prompt)

        # Session ID for resumability
        cmd.extend(["--session-id", session_id])

        # Output format
        cmd.extend(["--output-format", self._output_format])

        # Max turns
        if self._max_turns:
            cmd.extend(["--max-turns", str(self._max_turns)])

        # Working directory
        cwd = task.repo or (str(self._default_cwd) if self._default_cwd else None)
        if cwd:
            cmd.extend(["--add-dir", cwd])

        # Extra flags
        cmd.extend(self._extra_flags)

        return cmd

    def _build_prompt(self, task: TaskSpec) -> str:
        """Build a structured prompt from the task spec."""
        parts = [f"Task: {task.title}"]
        if task.description:
            parts.append(f"\n{task.description}")
        if task.acceptance_criteria:
            parts.append("\nAcceptance criteria:")
            for c in task.acceptance_criteria:
                parts.append(f"  - {c}")
        if task.task_type:
            parts.append(f"\nTask type: {task.task_type}")
        return "\n".join(parts)

    def execute(self, task: TaskSpec) -> ExecutionResult:
        # Pre-generate session ID for resumability
        session_id = f"autopilot-{task.id}-{uuid.uuid4().hex[:6]}"
        cmd = self._build_command(task, session_id)

        cwd = task.repo or (str(self._default_cwd) if self._default_cwd else None)
        start = time.monotonic()

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=task.timeout_minutes * 60,
                env=env,
            )
            duration = time.monotonic() - start

            # Parse output
            stdout = proc.stdout
            files_changed: list[str] = []

            if self._output_format == "json" and stdout.strip():
                try:
                    output = json.loads(stdout)
                    if isinstance(output, dict):
                        files_changed = output.get("files_changed", [])
                        stdout = output.get("result", stdout)
                except json.JSONDecodeError:
                    pass

            return ExecutionResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=proc.stderr,
                files_changed=files_changed,
                duration_seconds=round(duration, 2),
            )

        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return ExecutionResult(
                success=False,
                exit_code=124,
                stderr=f"Claude Code timed out after {task.timeout_minutes}m",
                duration_seconds=round(duration, 2),
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                exit_code=127,
                stderr=f"Claude binary not found: {self._bin}",
            )
