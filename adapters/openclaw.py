"""OpenClaw webhook adapter.

Integrates with OpenClaw gateway for event-driven agent coordination.
Supports pushing results and triggering agent runs via webhooks.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any

from autopilot_core.task import TaskSpec


@dataclass
class OpenClawConfig:
    """Configuration for OpenClaw gateway connection."""

    gateway_url: str = ""
    hook_token: str = ""

    @classmethod
    def from_env(cls) -> OpenClawConfig:
        return cls(
            gateway_url=os.environ.get("OPENCLAW_GATEWAY_URL", "http://localhost:18789"),
            hook_token=os.environ.get("OPENCLAW_HOOK_TOKEN", ""),
        )


class OpenClawAdapter:
    """Webhook client for OpenClaw gateway integration.

    Supports:
    - POST to /hooks/wake (system events, trigger heartbeat)
    - POST to /hooks/agent (trigger isolated agent runs)
    - Result push-back from Autopilot to OpenClaw
    """

    def __init__(self, config: OpenClawConfig | None = None):
        self.config = config or OpenClawConfig.from_env()

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to an OpenClaw endpoint."""
        url = f"{self.config.gateway_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.config.hook_token:
            headers["Authorization"] = f"Bearer {self.config.hook_token}"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else {"ok": True}
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}", "body": e.read().decode()[:500]}
        except urllib.error.URLError as e:
            return {"ok": False, "error": str(e.reason)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wake(self, event_type: str, summary: str, **extra: Any) -> dict[str, Any]:
        """Send a wake event to trigger OpenClaw heartbeat."""
        payload = {"event_type": event_type, "summary": summary, **extra}
        return self._post("/hooks/wake", payload)

    def trigger_agent(
        self,
        message: str,
        *,
        session_key: str | None = None,
        agent_id: str = "main",
        timeout: int = 300,
    ) -> dict[str, Any]:
        """Trigger an isolated agent run via OpenClaw."""
        payload: dict[str, Any] = {
            "message": message,
            "agentId": agent_id,
            "timeout": timeout,
        }
        if session_key:
            payload["sessionKey"] = session_key
        return self._post("/hooks/agent", payload)

    def push_result(
        self,
        task: TaskSpec,
        summary: str,
        *,
        success: bool = True,
    ) -> dict[str, Any]:
        """Push a task result summary back to OpenClaw."""
        status_emoji = "done" if success else "blocked"
        message = (
            f"[Autopilot] Task {task.id} [{status_emoji}]: {task.title}\n"
            f"Summary: {summary}"
        )
        return self.wake(
            "autopilot.result",
            message,
            task_id=task.id,
            task_status=task.status.value,
            success=success,
        )
