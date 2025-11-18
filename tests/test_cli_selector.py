"""Tests for intelligent CLI selection."""

import pytest

from clink.cli_selector import CLISelector


class MockRegistry:
    """Mock registry for testing."""

    def __init__(self, clients, roles_map=None):
        self._clients = clients
        # Default roles map: all CLIs support 'default', 'codereviewer', and 'planner'
        self._roles_map = roles_map or {cli: ["default", "codereviewer", "planner"] for cli in clients}

    def list_clients(self):
        return self._clients

    def list_roles(self, cli_name):
        """List roles supported by a CLI."""
        return self._roles_map.get(cli_name, ["default"])


class TestCLISelector:
    """Test intelligent CLI selection logic."""

    def test_review_task_selects_codex(self):
        """Test that code review tasks select codex."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # Test various review-related prompts
        review_prompts = [
            "请帮我review这段代码",
            "Can you review this code for security issues?",
            "检查这个函数有没有bug",
            "Analyze this code for vulnerabilities",
            "Code quality check needed",
        ]

        for prompt in review_prompts:
            result = selector.select_cli(prompt)
            assert result == "codex", f"Failed for prompt: {prompt}"

    def test_implementation_task_selects_claude(self):
        """Test that implementation tasks select claude."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # Test various implementation-related prompts
        impl_prompts = [
            "帮我实现一个登录功能",
            "Create a new API endpoint",
            "写一个函数来处理用户输入",
            "Build a REST API",
            "Add authentication to the app",
            "Fix the bug in login.py",
        ]

        for prompt in impl_prompts:
            result = selector.select_cli(prompt)
            assert result == "claude", f"Failed for prompt: {prompt}"

    def test_planning_task_selects_claude(self):
        """Test that planning tasks select claude."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # Test various planning-related prompts
        planning_prompts = [
            "帮我设计一个微服务架构",
            "Plan the implementation of user authentication",
            "设计数据库schema",
            "Create a roadmap for the project",
        ]

        for prompt in planning_prompts:
            result = selector.select_cli(prompt)
            assert result == "claude", f"Failed for prompt: {prompt}"

    def test_role_based_selection(self):
        """Test that role parameter overrides prompt analysis."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # Even with implementation prompt, codereviewer role should select codex
        result = selector.select_cli(
            prompt="实现一个新功能",
            role="codereviewer",
        )
        assert result == "codex"

        # Planner role should select claude
        result = selector.select_cli(
            prompt="review this code",
            role="planner",
        )
        assert result == "claude"

    def test_explicit_cli_request(self):
        """Test that explicit CLI request is honored."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # Explicit request should override everything
        result = selector.select_cli(
            prompt="review this code",  # Would normally select codex
            requested_cli="claude",
        )
        assert result == "claude"

    def test_fallback_when_only_one_cli(self):
        """Test fallback when only one CLI is available."""
        registry = MockRegistry(["claude"])
        selector = CLISelector(registry)

        # Should use claude regardless of task type
        assert selector.select_cli("review this code") == "claude"
        assert selector.select_cli("implement a feature") == "claude"

    def test_default_cli_preference(self):
        """Test default CLI preference order."""
        # Test claude preference
        registry = MockRegistry(["codex", "claude", "gemini"])
        selector = CLISelector(registry)
        assert selector._get_default_cli() == "claude"

        # Test codex as second choice
        registry = MockRegistry(["codex", "gemini"])
        selector = CLISelector(registry)
        assert selector._get_default_cli() == "codex"

        # Test gemini as third choice (now included in preference order)
        registry = MockRegistry(["gemini", "other"])
        selector = CLISelector(registry)
        assert selector._get_default_cli() == "gemini"

        # Test fallback to first available for unknown CLI
        registry = MockRegistry(["unknown"])
        selector = CLISelector(registry)
        assert selector._get_default_cli() == "unknown"

    def test_mixed_keywords_highest_score_wins(self):
        """Test that highest keyword score determines selection."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # More review keywords should select codex
        result = selector.select_cli("Review and analyze this code for security vulnerabilities and bugs")
        assert result == "codex"

        # More implementation keywords should select claude
        result = selector.select_cli("Implement and create a new feature, build the API and write tests")
        assert result == "claude"

    def test_chinese_keywords(self):
        """Test that Chinese keywords work correctly."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # Chinese review keywords
        assert selector.select_cli("审查代码质量") == "codex"
        assert selector.select_cli("检查安全漏洞") == "codex"

        # Chinese implementation keywords
        assert selector.select_cli("实现新功能") == "claude"
        assert selector.select_cli("创建API接口") == "claude"

    def test_no_cli_available_raises_error(self):
        """Test that error is raised when no CLI is available."""
        registry = MockRegistry([])
        selector = CLISelector(registry)

        with pytest.raises(ValueError, match="No CLI clients are configured"):
            selector.select_cli("any prompt")

    def test_invalid_requested_cli_falls_back(self):
        """Test that invalid CLI request falls back to auto-selection."""
        registry = MockRegistry(["claude", "codex"])
        selector = CLISelector(registry)

        # Invalid CLI should fall back to prompt analysis
        result = selector.select_cli(
            prompt="review this code",
            requested_cli="nonexistent",
        )
        assert result == "codex"
