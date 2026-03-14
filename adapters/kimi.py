"""Kimi Code executor adapter.

Runs tasks via `kimi --print -p` (headless mode).
Kimi K2.5 is a volume executor — lower cost, massive compute,
good for parallelizable tasks that don't need architectural decisions.

CLI interface mirrors Claude Code:
  kimi --print -p "prompt" --max-steps-per-turn N -w /path
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


class KimiAdapter(BaseAdapter):
    """Executes tasks via Kimi Code CLI in headless mode.

    Args:
        kimi_bin: Path to the kimi binary. Defaults to "kimi".
        output_format: Output format (text, stream-json).
        max_steps: Maximum steps per turn.
        default_cwd: Default working directory.
        api_key: Kimi API key (or set KIMI_API_KEY env var).
        model: Model name override (default uses config).
        thinking: Enable thinking mode (default True).
        extra_flags: Additional CLI flags to pass.
    """

    def __init__(
        self,
        *,
        kimi_bin: str = "kimi",
        output_format: str = "text",
        max_steps: int = 50,
        default_cwd: str | Path | None = None,
        api_key: str | None = None,
        model: str | None = None,
        thinking: bool = True,
        extra_flags: list[str] | None = None,
    ):
        self._bin = kimi_bin
        self._output_format = output_format
        self._max_steps = max_steps
        self._default_cwd = Path(default_cwd).resolve() if default_cwd else None
        self._api_key = api_key
        self._model = model
        self._thinking = thinking
        self._extra_flags = extra_flags or []

    @property
    def name(self) -> str:
        return "kimi"

    def _build_command(self, task: TaskSpec, session_id: str) -> list[str]:
        """Build the kimi CLI command."""
        cmd = [self._bin, "--print"]

        # Prompt from task
        prompt = self._build_prompt(task)
        cmd.extend(["-p", prompt])

        # Session ID for resumability
        cmd.extend(["-S", session_id])

        # Max steps
        cmd.extend(["--max-steps-per-turn", str(self._max_steps)])

        # Thinking mode
        if self._thinking:
            cmd.append("--thinking")
        else:
            cmd.append("--no-thinking")

        # Model override
        if self._model:
            cmd.extend(["-m", self._model])

        # Output format
        cmd.extend(["--output-format", self._output_format])

        # Working directory
        cwd = task.repo or (str(self._default_cwd) if self._default_cwd else None)
        if cwd:
            cmd.extend(["-w", cwd])

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

    def _extract_text_output(self, raw_output: str) -> str:
        """Extract the actual text content from kimi's print mode output.

        Kimi's print mode outputs metadata lines (TurnBegin, StepBegin,
        StatusUpdate, TurnEnd) mixed with TextPart lines. Extract just
        the text content.
        """
        lines = []
        for line in raw_output.splitlines():
            # Skip metadata lines
            if line.startswith(("TurnBegin(", "TurnEnd(", "StepBegin(",
                                "StatusUpdate(", "ToolCall(", "ToolResult(",
                                "    ", ")")):
                continue
            # Extract TextPart content
            if line.startswith("TextPart("):
                # TextPart(type='text', text='...')
                start = line.find("text='", 6)
                if start != -1:
                    start += 6
                    end = line.rfind("')")
                    if end > start:
                        lines.append(line[start:end])
                continue
            # Plain text lines (shouldn't happen in print mode but just in case)
            if line.strip():
                lines.append(line)
        return "\n".join(lines)

    def execute(self, task: TaskSpec) -> ExecutionResult:
        # Pre-generate session ID for resumability
        session_id = f"autopilot-{task.id}-{uuid.uuid4().hex[:6]}"
        cmd = self._build_command(task, session_id)

        cwd = task.repo or (str(self._default_cwd) if self._default_cwd else None)
        start = time.monotonic()

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        # Allow API key override via adapter config
        if self._api_key:
            env["KIMI_API_KEY"] = self._api_key

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
            if self._output_format == "text":
                stdout = self._extract_text_output(stdout)

            return ExecutionResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=proc.stderr,
                duration_seconds=round(duration, 2),
            )

        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return ExecutionResult(
                success=False,
                exit_code=124,
                stderr=f"Kimi timed out after {task.timeout_minutes}m",
                duration_seconds=round(duration, 2),
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                exit_code=127,
                stderr=f"Kimi binary not found: {self._bin}",
            )
