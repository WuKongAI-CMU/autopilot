"""AgentAPI universal adapter.

Wraps any coding agent that implements the AgentAPI HTTP protocol:
  POST /message  — send a prompt
  GET  /status   — check agent status

This allows Autopilot to dispatch to any AgentAPI-compatible agent
without needing a specific CLI adapter.

Uses only stdlib (urllib) — no external HTTP dependencies.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from adapters.base import BaseAdapter
from autopilot_core.dispatcher import ExecutionResult
from autopilot_core.task import TaskSpec


class AgentAPIAdapter(BaseAdapter):
    """Executes tasks via the AgentAPI HTTP protocol.

    Args:
        base_url: AgentAPI server URL. Defaults to http://localhost:3284.
        poll_interval: Seconds between status polls. Defaults to 5.
        api_key: Optional API key for authentication.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:3284",
        poll_interval: float = 5.0,
        api_key: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "agentapi"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _request(
        self, method: str, path: str, data: dict | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Make an HTTP request to the AgentAPI server."""
        url = f"{self._base_url}{path}"
        body = json.dumps(data).encode() if data else None

        req = urllib.request.Request(
            url,
            data=body,
            headers=self._headers(),
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                response_body = resp.read().decode()
                try:
                    return resp.status, json.loads(response_body)
                except json.JSONDecodeError:
                    return resp.status, {"raw": response_body}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            try:
                return e.code, json.loads(body_text)
            except json.JSONDecodeError:
                return e.code, {"error": body_text}
        except urllib.error.URLError as e:
            return 0, {"error": str(e.reason)}

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
        start = time.monotonic()
        prompt = self._build_prompt(task)

        # Send message
        status_code, response = self._request(
            "POST", "/message", {"content": prompt}
        )

        if status_code == 0:
            return ExecutionResult(
                success=False,
                exit_code=1,
                stderr=f"AgentAPI connection failed: {response.get('error', 'unknown')}",
                duration_seconds=round(time.monotonic() - start, 2),
            )

        if status_code >= 400:
            return ExecutionResult(
                success=False,
                exit_code=1,
                stderr=f"AgentAPI error {status_code}: {response}",
                duration_seconds=round(time.monotonic() - start, 2),
            )

        # Poll for completion
        timeout = task.timeout_minutes * 60
        while (time.monotonic() - start) < timeout:
            time.sleep(self._poll_interval)
            code, status = self._request("GET", "/status")

            if code == 0:
                continue  # Connection issue, retry

            agent_status = status.get("status", "")
            if agent_status in ("idle", "completed", "done"):
                duration = time.monotonic() - start
                output = status.get("output", status.get("result", ""))
                return ExecutionResult(
                    success=True,
                    exit_code=0,
                    stdout=str(output),
                    duration_seconds=round(duration, 2),
                )
            elif agent_status in ("error", "failed"):
                duration = time.monotonic() - start
                return ExecutionResult(
                    success=False,
                    exit_code=1,
                    stderr=status.get("error", "Agent execution failed"),
                    duration_seconds=round(duration, 2),
                )
            # Still running, continue polling

        duration = time.monotonic() - start
        return ExecutionResult(
            success=False,
            exit_code=124,
            stderr=f"AgentAPI timed out after {task.timeout_minutes}m",
            duration_seconds=round(duration, 2),
        )

    def health(self) -> float:
        """Check agent health via GET /status."""
        code, _ = self._request("GET", "/status")
        if code == 0:
            return 0.0
        if code == 200:
            return 1.0
        return 0.5
