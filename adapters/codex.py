"""Codex executor adapter.

Runs tasks via `codex exec --full-auto` CLI.
Codex is a high-tier executor — good for complex code generation
and modification tasks with full repository context.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from adapters.base import BaseAdapter
from autopilot_core.dispatcher import ExecutionResult
from autopilot_core.task import TaskSpec


class CodexAdapter(BaseAdapter):
    """Executes tasks via Codex CLI in full-auto mode.

    Args:
        codex_bin: Path to the codex binary. Defaults to "codex".
        default_cwd: Default working directory.
        extra_flags: Additional CLI flags to pass.
    """

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        default_cwd: str | Path | None = None,
        extra_flags: list[str] | None = None,
    ):
        self._bin = codex_bin
        self._default_cwd = Path(default_cwd).resolve() if default_cwd else None
        self._extra_flags = extra_flags or []

    @property
    def name(self) -> str:
        return "codex"

    def _build_command(self, task: TaskSpec) -> list[str]:
        """Build the codex CLI command."""
        cmd = [self._bin, "exec", "--full-auto"]

        # Build prompt
        prompt = self._build_prompt(task)
        cmd.append(prompt)

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
        cmd = self._build_command(task)
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

            return ExecutionResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_seconds=round(duration, 2),
            )

        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return ExecutionResult(
                success=False,
                exit_code=124,
                stderr=f"Codex timed out after {task.timeout_minutes}m",
                duration_seconds=round(duration, 2),
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                exit_code=127,
                stderr=f"Codex binary not found: {self._bin}",
            )
