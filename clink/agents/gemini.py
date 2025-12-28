"""Gemini-specific CLI agent hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import logging

from clink.models import ResolvedCLIClient
from clink.parsers.base import ParsedCLIResponse

from .base import AgentOutput, BaseCLIAgent

logger = logging.getLogger("clink.agents.gemini")

# Gemini CLI config path
GEMINI_ENV_PATH = Path.home() / ".gemini" / ".env"


class GeminiAgent(BaseCLIAgent):
    """Gemini-specific behaviour."""

    def __init__(self, client: ResolvedCLIClient):
        super().__init__(client)

    def _build_environment(self) -> dict[str, str]:
        """Build environment for Gemini CLI, loading from ~/.gemini/.env if needed."""
        env = super()._build_environment()

        # Load Gemini config from ~/.gemini/.env if the file exists
        # This ensures CC Switch configurations are respected
        if GEMINI_ENV_PATH.exists():
            gemini_env = self._load_gemini_env()
            for key, value in gemini_env.items():
                # Only set if not already present or if we cleared it
                if key not in env or env.get(key) == "":
                    env[key] = value

        return env

    def _load_gemini_env(self) -> dict[str, str]:
        """Load environment variables from ~/.gemini/.env file."""
        result: dict[str, str] = {}
        try:
            content = GEMINI_ENV_PATH.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    # Remove quotes if present
                    if value and value[0] in ('"', "'") and value[-1] == value[0]:
                        value = value[1:-1]
                    if key:
                        result[key] = value
        except Exception:
            pass  # Silently ignore errors reading the file
        return result

    def _recover_from_error(
        self,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        sanitized_command: list[str],
        duration_seconds: float,
        output_file_content: str | None,
    ) -> AgentOutput | None:
        combined = "\n".join(part for part in (stderr, stdout) if part)
        if not combined:
            return None

        brace_index = combined.find("{")
        if brace_index == -1:
            return None

        json_candidate = combined[brace_index:]
        try:
            payload: dict[str, Any] = json.loads(json_candidate)
        except json.JSONDecodeError:
            return None

        error_block = payload.get("error")
        if not isinstance(error_block, dict):
            return None

        code = error_block.get("code")
        err_type = error_block.get("type")
        detail_message = error_block.get("message")

        prologue = combined[:brace_index].strip()
        lines: list[str] = []
        if prologue and (not detail_message or prologue not in detail_message):
            lines.append(prologue)
        if detail_message:
            lines.append(detail_message)

        header = "Gemini CLI reported a tool failure"
        if code:
            header = f"{header} ({code})"
        elif err_type:
            header = f"{header} ({err_type})"

        content_lines = [header.rstrip(".") + "."]
        content_lines.extend(lines)
        message = "\n".join(content_lines).strip()

        # Log the full error details for debugging
        logger.warning(
            "Gemini CLI tool failure recovered: code=%s, type=%s, message=%s",
            code,
            err_type,
            detail_message[:500] if detail_message else "(none)",
        )

        metadata = {
            "cli_error_recovered": True,
            "cli_error_code": code,
            "cli_error_type": err_type,
            "cli_error_payload": payload,
        }

        parsed = ParsedCLIResponse(content=message or header, metadata=metadata)
        return AgentOutput(
            parsed=parsed,
            sanitized_command=sanitized_command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
            parser_name=self._parser.name,
            output_file_content=output_file_content,
        )
