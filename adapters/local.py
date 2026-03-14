"""Local shell executor adapter.

Runs tasks as shell commands in a subprocess. Useful for testing
and for simple automation tasks.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from adapters.base import BaseAdapter
from autopilot_core.dispatcher import ExecutionResult
from autopilot_core.task import TaskSpec


class LocalAdapter(BaseAdapter):
    """Executes tasks as local shell commands.

    The task description is used as the command to run.

    Args:
        cwd: Working directory for command execution.
        shell: Whether to run commands through the shell.
    """

    def __init__(self, cwd: str | Path | None = None, *, shell: bool = True):
        self._cwd = Path(cwd).resolve() if cwd else Path.cwd()
        self._shell = shell

    @property
    def name(self) -> str:
        return "local"

    def execute(self, task: TaskSpec) -> ExecutionResult:
        command = task.description.strip()
        if not command:
            return ExecutionResult(
                success=False,
                exit_code=1,
                stderr="No command in task description",
            )

        cwd = Path(task.repo).resolve() if task.repo else self._cwd
        start = time.monotonic()

        try:
            proc = subprocess.run(
                command,
                shell=self._shell,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=task.timeout_minutes * 60,
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
                stderr=f"Command timed out after {task.timeout_minutes}m",
                duration_seconds=round(duration, 2),
            )
        except OSError as e:
            return ExecutionResult(
                success=False,
                exit_code=1,
                stderr=str(e),
            )
