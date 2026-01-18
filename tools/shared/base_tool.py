"""
Minimal Base Tool Stub for Clink-Only Mode

This stub provides the essential BaseTool interface without provider dependencies.
Clink-only mode doesn't need AI provider configuration or model selection.
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from mcp.types import TextContent

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from config import MCP_PROMPT_SIZE_LIMIT
from utils import estimate_tokens
from utils.env import get_env
from utils.file_utils import read_file_content

# Import models from tools.models for compatibility
try:
    from tools.models import SPECIAL_STATUS_MODELS, ContinuationOffer, ToolOutput
except ImportError:
    SPECIAL_STATUS_MODELS = {}
    ContinuationOffer = None
    ToolOutput = None

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """
    Minimal base class for clink-only tools.

    Provides only the essential interface needed for CLinkTool and VersionTool
    without AI provider dependencies.
    """

    def __init__(self):
        # Cache tool metadata at initialization
        self.name = self.get_name()
        self.description = self.get_description()
        self.default_temperature = self.get_default_temperature()

    @abstractmethod
    def get_name(self) -> str:
        """Return the unique name identifier for this tool."""
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Return a detailed description of what this tool does."""
        pass

    @abstractmethod
    def get_input_schema(self) -> dict[str, Any]:
        """Return the JSON Schema that defines this tool's parameters."""
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt that configures the AI model's behavior."""
        pass

    def get_annotations(self) -> Optional[dict[str, Any]]:
        """Return optional annotations for this tool."""
        return None

    def requires_model(self) -> bool:
        """Return whether this tool requires AI model access."""
        return True

    def get_default_temperature(self) -> float:
        """Return the default temperature setting for this tool."""
        return 0.5

    def get_model_category(self) -> "ToolModelCategory":
        """Return the model category for this tool."""
        from tools.models import ToolModelCategory
        return ToolModelCategory.BALANCED

    @abstractmethod
    def get_request_model(self):
        """Return the Pydantic model class used for validating requests."""
        pass

    def validate_file_paths(self, request) -> Optional[str]:
        """Validate that all file paths in the request are absolute."""
        file_fields = [
            "absolute_file_paths",
            "file",
            "path",
            "directory",
        ]

        for field_name in file_fields:
            if hasattr(request, field_name):
                field_value = getattr(request, field_name)
                if field_value is None:
                    continue

                paths_to_check = field_value if isinstance(field_value, list) else [field_value]
                for path in paths_to_check:
                    if path and not os.path.isabs(path):
                        return f"All file paths must be FULL absolute paths. Invalid path: '{path}'"

        return None

    def _validate_token_limit(self, content: str, content_type: str = "Content") -> None:
        """Validate that user-provided content doesn't exceed the MCP prompt size limit."""
        if not content:
            logger.debug(f"{self.name} tool {content_type.lower()} validation skipped (no content)")
            return

        char_count = len(content)
        if char_count > MCP_PROMPT_SIZE_LIMIT:
            token_estimate = estimate_tokens(content)
            error_msg = (
                f"{char_count:,} characters (~{token_estimate:,} tokens). "
                f"Maximum is {MCP_PROMPT_SIZE_LIMIT:,} characters."
            )
            logger.error(f"{self.name} tool {content_type.lower()} validation failed: {error_msg}")
            raise ValueError(f"{content_type} too large: {error_msg}")

        token_estimate = estimate_tokens(content)
        logger.debug(
            f"{self.name} tool {content_type.lower()} validation passed: "
            f"{char_count:,} characters (~{token_estimate:,} tokens)"
        )

    def handle_prompt_file(self, files: Optional[list[str]]) -> tuple[Optional[str], Optional[list[str]]]:
        """Check for and handle prompt.txt in the absolute file paths list."""
        if not files:
            return None, files

        prompt_content = None
        updated_files = []

        for file_path in files:
            if os.path.basename(file_path) == "prompt.txt":
                try:
                    content, _ = read_file_content(file_path)
                    if "--- BEGIN FILE:" in content and "--- END FILE:" in content:
                        lines = content.split("\n")
                        in_content = False
                        content_lines = []
                        for line in lines:
                            if line.startswith("--- BEGIN FILE:"):
                                in_content = True
                                continue
                            elif line.startswith("--- END FILE:"):
                                break
                            elif in_content:
                                content_lines.append(line)
                        prompt_content = "\n".join(content_lines)
                    else:
                        if not content.startswith("\n--- ERROR"):
                            prompt_content = content
                        else:
                            prompt_content = None
                except Exception:
                    pass
            else:
                updated_files.append(file_path)

        return prompt_content, updated_files if updated_files else None

    def get_language_instruction(self) -> str:
        """Generate language instruction based on LOCALE configuration."""
        locale = (get_env("LOCALE", "") or "").strip()
        if not locale:
            return ""
        return f"Always respond in {locale}.\n\n"

    def check_prompt_size(self, text: str) -> Optional[dict[str, Any]]:
        """
        Check if USER INPUT text is too large for MCP transport boundary.

        IMPORTANT: This method should ONLY be used to validate user input that crosses
        the CLI ↔ MCP Server transport boundary. It should NOT be used to limit
        internal MCP Server operations.

        Args:
            text: The user input text to check (NOT internal prompt content)

        Returns:
            Optional[Dict[str, Any]]: Response asking for file handling if too large, None otherwise
        """
        if text and len(text) > MCP_PROMPT_SIZE_LIMIT:
            return {
                "status": "resend_prompt",
                "content": (
                    f"MANDATORY ACTION REQUIRED: The prompt is too large for MCP's token limits (>{MCP_PROMPT_SIZE_LIMIT:,} characters). "
                    "YOU MUST IMMEDIATELY save the prompt text to a temporary file named 'prompt.txt' in the working directory. "
                    "DO NOT attempt to shorten or modify the prompt. SAVE IT AS-IS to 'prompt.txt'. "
                    "Then resend the request, passing the absolute file path to 'prompt.txt' as part of the tool call, "
                    "along with any other files you wish to share as context. Leave the prompt text itself empty or very brief in the new request. "
                    "This is the ONLY way to handle large prompts - you MUST follow these exact steps."
                ),
                "content_type": "text",
                "metadata": {
                    "prompt_size": len(text),
                    "limit": MCP_PROMPT_SIZE_LIMIT,
                    "instructions": "MANDATORY: Save prompt to 'prompt.txt' in current folder and provide full path when recalling this tool.",
                },
            }
        return None

    @abstractmethod
    async def prepare_prompt(self, request) -> str:
        """Prepare the complete prompt for the AI model."""
        pass

    def format_response(self, response: str, request, model_info: dict = None) -> str:
        """Format the AI model's response for the user."""
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Execute the tool."""
        raise NotImplementedError("Subclasses must implement execute method")
