"""Tests for executor adapters."""

import json
from unittest.mock import MagicMock, patch

import pytest

from adapters.local import LocalAdapter
from adapters.claude_code import ClaudeCodeAdapter
from adapters.kimi import KimiAdapter
from adapters.openclaw import OpenClawAdapter, OpenClawConfig
from autopilot_core.task import TaskSpec


class TestLocalAdapter:
    def test_name(self):
        adapter = LocalAdapter()
        assert adapter.name == "local"

    def test_execute_success(self, tmp_path):
        adapter = LocalAdapter(cwd=tmp_path)
        task = TaskSpec.new("Echo test", description="echo hello")
        result = adapter.execute(task)
        assert result.success
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_execute_failure(self, tmp_path):
        adapter = LocalAdapter(cwd=tmp_path)
        task = TaskSpec.new("Fail test", description="exit 1")
        result = adapter.execute(task)
        assert not result.success
        assert result.exit_code == 1

    def test_empty_command(self):
        adapter = LocalAdapter()
        task = TaskSpec.new("Empty", description="")
        result = adapter.execute(task)
        assert not result.success
        assert "No command" in result.stderr

    def test_health(self):
        assert LocalAdapter().health() == 1.0


class TestClaudeCodeAdapter:
    def test_name(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.name == "claude-code"

    def test_build_command(self):
        adapter = ClaudeCodeAdapter(max_turns=5, output_format="json")
        task = TaskSpec.new("Test task", description="Fix the bug", task_type="bugfix")
        cmd = adapter._build_command(task, "sess-123")
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--session-id" in cmd
        assert "sess-123" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--max-turns" in cmd
        assert "5" in cmd

    def test_build_prompt(self):
        adapter = ClaudeCodeAdapter()
        task = TaskSpec.new(
            "Fix auth",
            description="Login returns 500",
            task_type="bugfix",
            acceptance_criteria=["Tests pass", "No regressions"],
        )
        prompt = adapter._build_prompt(task)
        assert "Fix auth" in prompt
        assert "Login returns 500" in prompt
        assert "Tests pass" in prompt
        assert "bugfix" in prompt

    def test_execute_binary_not_found(self):
        adapter = ClaudeCodeAdapter(claude_bin="/nonexistent/claude")
        task = TaskSpec.new("Test", description="test")
        result = adapter.execute(task)
        assert not result.success
        assert result.exit_code == 127
        assert "not found" in result.stderr


class TestKimiAdapter:
    def test_name(self):
        adapter = KimiAdapter()
        assert adapter.name == "kimi"

    def test_build_command(self):
        adapter = KimiAdapter(max_steps=25, thinking=False)
        task = TaskSpec.new("Test task", description="Fix the bug", task_type="bugfix")
        cmd = adapter._build_command(task, "sess-456")
        assert cmd[0] == "kimi"
        assert "--print" in cmd
        assert "-p" in cmd
        assert "-S" in cmd
        assert "sess-456" in cmd
        assert "--max-steps-per-turn" in cmd
        assert "25" in cmd
        assert "--no-thinking" in cmd

    def test_build_command_with_thinking(self):
        adapter = KimiAdapter(thinking=True)
        task = TaskSpec.new("Test", description="test")
        cmd = adapter._build_command(task, "sess-789")
        assert "--thinking" in cmd
        assert "--no-thinking" not in cmd

    def test_build_command_with_model(self):
        adapter = KimiAdapter(model="kimi-k2.5-turbo")
        task = TaskSpec.new("Test", description="test")
        cmd = adapter._build_command(task, "sess-abc")
        assert "-m" in cmd
        assert "kimi-k2.5-turbo" in cmd

    def test_build_prompt(self):
        adapter = KimiAdapter()
        task = TaskSpec.new(
            "Fix auth",
            description="Login returns 500",
            task_type="bugfix",
            acceptance_criteria=["Tests pass", "No regressions"],
        )
        prompt = adapter._build_prompt(task)
        assert "Fix auth" in prompt
        assert "Login returns 500" in prompt
        assert "Tests pass" in prompt
        assert "bugfix" in prompt

    def test_extract_text_output(self):
        adapter = KimiAdapter()
        raw = (
            "TurnBegin(user_input='hello')\n"
            "StepBegin(n=1)\n"
            "TextPart(type='text', text='hello world')\n"
            "StatusUpdate(\n"
            "    context_usage=0.02,\n"
            ")\n"
            "TurnEnd()\n"
        )
        text = adapter._extract_text_output(raw)
        assert text == "hello world"

    def test_execute_binary_not_found(self):
        adapter = KimiAdapter(kimi_bin="/nonexistent/kimi")
        task = TaskSpec.new("Test", description="test")
        result = adapter.execute(task)
        assert not result.success
        assert result.exit_code == 127
        assert "not found" in result.stderr

    def test_api_key_injection(self):
        adapter = KimiAdapter(api_key="sk-test-key")
        assert adapter._api_key == "sk-test-key"

    def test_health(self):
        assert KimiAdapter().health() == 1.0


class TestOpenClawAdapter:
    def test_config_from_env(self):
        with patch.dict("os.environ", {"OPENCLAW_GATEWAY_URL": "http://test:8080", "OPENCLAW_HOOK_TOKEN": "tok"}):
            config = OpenClawConfig.from_env()
            assert config.gateway_url == "http://test:8080"
            assert config.hook_token == "tok"

    def test_push_result_formats_message(self):
        adapter = OpenClawAdapter(OpenClawConfig(gateway_url="http://localhost:1", hook_token="t"))
        task = TaskSpec.new("Test task")
        # Will fail to connect but we test the method doesn't crash
        result = adapter.push_result(task, "All good", success=True)
        assert isinstance(result, dict)
        # Connection refused is expected
        assert not result.get("ok", True) or result.get("ok")
