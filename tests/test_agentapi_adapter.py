"""Tests for the AgentAPI adapter."""

from unittest.mock import patch, MagicMock
import json

from adapters.agentapi import AgentAPIAdapter
from autopilot_core.task import TaskSpec


class TestAgentAPIAdapter:
    def test_name(self):
        adapter = AgentAPIAdapter()
        assert adapter.name == "agentapi"

    def test_build_prompt(self):
        adapter = AgentAPIAdapter()
        task = TaskSpec.new(
            "Fix auth",
            description="Login returns 500",
            task_type="bugfix",
            acceptance_criteria=["Tests pass"],
        )
        prompt = adapter._build_prompt(task)
        assert "Fix auth" in prompt
        assert "Login returns 500" in prompt
        assert "Tests pass" in prompt

    def test_connection_failure(self):
        adapter = AgentAPIAdapter(base_url="http://localhost:1")
        task = TaskSpec.new("Test", description="test")
        result = adapter.execute(task)
        assert not result.success
        assert "connection failed" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_health_returns_zero_on_connection_failure(self):
        adapter = AgentAPIAdapter(base_url="http://localhost:1")
        assert adapter.health() == 0.0

    def test_api_key_in_headers(self):
        adapter = AgentAPIAdapter(api_key="test-key-123")
        headers = adapter._headers()
        assert headers["Authorization"] == "Bearer test-key-123"
        assert headers["Content-Type"] == "application/json"
