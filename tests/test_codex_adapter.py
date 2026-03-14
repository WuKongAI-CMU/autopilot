"""Tests for the Codex adapter."""

from adapters.codex import CodexAdapter
from autopilot_core.task import TaskSpec


class TestCodexAdapter:
    def test_name(self):
        adapter = CodexAdapter()
        assert adapter.name == "codex"

    def test_build_command(self):
        adapter = CodexAdapter()
        task = TaskSpec.new("Fix bug", description="Fix the auth bug", task_type="bugfix")
        cmd = adapter._build_command(task)
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert cmd[2] == "--full-auto"
        assert "Fix bug" in cmd[3]

    def test_build_prompt(self):
        adapter = CodexAdapter()
        task = TaskSpec.new(
            "Refactor module",
            description="Split into smaller files",
            task_type="refactor",
            acceptance_criteria=["Tests pass", "No regressions"],
        )
        prompt = adapter._build_prompt(task)
        assert "Refactor module" in prompt
        assert "Split into smaller files" in prompt
        assert "Tests pass" in prompt

    def test_execute_binary_not_found(self):
        adapter = CodexAdapter(codex_bin="/nonexistent/codex")
        task = TaskSpec.new("Test", description="test")
        result = adapter.execute(task)
        assert not result.success
        assert result.exit_code == 127
        assert "not found" in result.stderr

    def test_health(self):
        assert CodexAdapter().health() == 1.0
