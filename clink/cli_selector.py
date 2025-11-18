"""Intelligent CLI selection based on task characteristics."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clink.registry import CLIRegistry

logger = logging.getLogger(__name__)


class CLISelector:
    """Selects the most appropriate CLI based on task analysis."""

    # Keywords that indicate code review tasks
    REVIEW_KEYWORDS = {
        "review",
        "审查",
        "检查",
        "analyze",
        "分析",
        "inspect",
        "检视",
        "critique",
        "评审",
        "security",
        "安全",
        "vulnerability",
        "漏洞",
        "bug",
        "错误",
        "issue",
        "问题",
        "quality",
        "质量",
        "lint",
        "test coverage",
        "测试覆盖",
    }

    # Keywords that indicate implementation/coding tasks
    IMPLEMENTATION_KEYWORDS = {
        "implement",
        "实现",
        "create",
        "创建",
        "build",
        "构建",
        "write",
        "编写",
        "add",
        "添加",
        "develop",
        "开发",
        "编码",
        "fix",
        "修复",
        "refactor",
        "重构",
        "update",
        "更新",
        "modify",
        "修改",
        "generate",
        "生成",
    }

    # Keywords that indicate planning tasks
    PLANNING_KEYWORDS = {
        "plan",
        "计划",
        "design",
        "设计",
        "architecture",
        "架构",
        "strategy",
        "策略",
        "approach",
        "方案",
        "outline",
        "大纲",
        "roadmap",
        "路线图",
    }

    def __init__(self, registry: CLIRegistry):
        self._registry = registry
        self._available_clis = registry.list_clients()

    def select_cli(
        self,
        prompt: str,
        role: str | None = None,
        requested_cli: str | None = None,
    ) -> str:
        """Select the most appropriate CLI based on task analysis.

        Args:
            prompt: The user's request text
            role: Optional role hint (e.g., 'codereviewer', 'planner')
            requested_cli: If user explicitly requested a CLI, use it

        Returns:
            The selected CLI name

        Raises:
            ValueError: If no suitable CLI is available
        """
        if not self._available_clis:
            raise ValueError("No CLI clients are configured")

        # Filter available CLIs by role support if role is specified
        available_for_role = self._filter_clis_by_role(role) if role else self._available_clis

        if not available_for_role:
            role_name = role or "default"
            raise ValueError(
                f"No configured CLI supports the role '{role_name}'. "
                f"Available CLIs: {', '.join(self._available_clis)}"
            )

        # If user explicitly requested a CLI, validate and use it
        if requested_cli:
            requested_lower = requested_cli.lower()
            if requested_lower in [cli.lower() for cli in available_for_role]:
                logger.info("Using explicitly requested CLI: %s", requested_cli)
                return requested_cli
            if requested_lower in [cli.lower() for cli in self._available_clis]:
                raise ValueError(
                    f"CLI '{requested_cli}' does not support the role '{role}'. "
                    f"CLIs supporting this role: {', '.join(available_for_role)}"
                )
            logger.warning(
                "Requested CLI '%s' not available, falling back to auto-selection",
                requested_cli,
            )

        # If role is specified, use role-based selection within filtered list
        if role:
            selected = self._select_by_role(role, available_clis=available_for_role)
            if selected:
                logger.info("Selected CLI '%s' based on role '%s'", selected, role)
                return selected

        # Analyze prompt content for task type within filtered list
        selected = self._select_by_prompt_analysis(prompt, available_clis=available_for_role)
        logger.info("Selected CLI '%s' based on prompt analysis", selected)
        return selected

    def _filter_clis_by_role(self, role: str) -> list[str]:
        """Filter available CLIs to only those supporting the requested role."""
        if not role:
            return self._available_clis

        filtered = []
        for cli_name in self._available_clis:
            try:
                cli_roles = self._registry.list_roles(cli_name)
                # Normalize role names for comparison
                normalized_roles = [r.lower() for r in cli_roles]
                if role.lower() in normalized_roles:
                    filtered.append(cli_name)
            except Exception as exc:
                logger.warning("Failed to check roles for CLI '%s': %s", cli_name, exc)
                continue

        return filtered

    def _select_by_role(self, role: str, available_clis: list[str] | None = None) -> str | None:
        """Select CLI based on role preference."""
        clis = available_clis if available_clis is not None else self._available_clis
        role_lower = role.lower()

        # Normalize CLI names for case-insensitive comparison
        cli_lower_map = {cli.lower(): cli for cli in clis}

        # For code review role, prefer codex if available
        if "review" in role_lower or "reviewer" in role_lower:
            if "codex" in cli_lower_map:
                return cli_lower_map["codex"]

        # For planner role, prefer claude if available
        if "plan" in role_lower or "planner" in role_lower:
            if "claude" in cli_lower_map:
                return cli_lower_map["claude"]

        return None

    def _select_by_prompt_analysis(self, prompt: str, available_clis: list[str] | None = None) -> str:
        """Analyze prompt content and select appropriate CLI."""
        clis = available_clis if available_clis is not None else self._available_clis
        prompt_lower = prompt.lower()

        # Normalize CLI names for case-insensitive comparison
        cli_lower_map = {cli.lower(): cli for cli in clis}

        # Calculate scores for each task type
        review_score = self._calculate_keyword_score(prompt_lower, self.REVIEW_KEYWORDS)
        implementation_score = self._calculate_keyword_score(prompt_lower, self.IMPLEMENTATION_KEYWORDS)
        planning_score = self._calculate_keyword_score(prompt_lower, self.PLANNING_KEYWORDS)

        logger.debug(
            "Task type scores - review: %d, implementation: %d, planning: %d",
            review_score,
            implementation_score,
            planning_score,
        )

        # Select based on highest score
        if review_score > implementation_score and review_score > planning_score:
            # Code review task - prefer codex
            if "codex" in cli_lower_map:
                return cli_lower_map["codex"]

        if implementation_score > review_score and implementation_score > planning_score:
            # Implementation task - prefer claude
            if "claude" in cli_lower_map:
                return cli_lower_map["claude"]

        if planning_score > review_score and planning_score > implementation_score:
            # Planning task - prefer claude
            if "claude" in cli_lower_map:
                return cli_lower_map["claude"]

        # Default fallback: prefer claude > codex > gemini > others
        return self._get_default_cli(available_clis=clis)

    def _calculate_keyword_score(self, text: str, keywords: set[str]) -> int:
        """Calculate how many keywords from the set appear in the text."""
        score = 0
        for keyword in keywords:
            # For Chinese keywords, use simple substring match
            if any("\u4e00" <= char <= "\u9fff" for char in keyword):
                matches = re.findall(re.escape(keyword), text, re.IGNORECASE)
            else:
                # For English keywords, try word boundary first
                # If no match, try without boundaries (for mixed Chinese-English text)
                pattern = r"\b" + re.escape(keyword) + r"\b"
                matches = re.findall(pattern, text, re.IGNORECASE)
                if not matches:
                    # Fallback: simple substring match for mixed text
                    matches = re.findall(re.escape(keyword), text, re.IGNORECASE)
            score += len(matches)
        return score

    def _get_default_cli(self, available_clis: list[str] | None = None) -> str:
        """Get default CLI with preference order: claude > codex > gemini > others."""
        clis = available_clis if available_clis is not None else self._available_clis

        # Normalize CLI names to lowercase for case-insensitive comparison
        cli_lower_map = {cli.lower(): cli for cli in clis}

        # Preference order (now including gemini)
        preferred_order = ["claude", "codex", "gemini"]

        for preferred in preferred_order:
            if preferred in cli_lower_map:
                return cli_lower_map[preferred]

        # Fallback to first available CLI
        return clis[0]
