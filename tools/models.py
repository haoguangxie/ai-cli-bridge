"""
Data models for tool responses and interactions (clink-only mode)
"""

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ToolModelCategory(Enum):
    """Categories for tool model selection based on requirements."""

    EXTENDED_REASONING = "extended_reasoning"  # Requires deep thinking capabilities
    FAST_RESPONSE = "fast_response"  # Speed and cost efficiency preferred
    BALANCED = "balanced"  # Balance of capability and performance


class ContinuationOffer(BaseModel):
    """Offer for CLI agent to continue conversation when Gemini doesn't ask follow-up"""

    continuation_id: str = Field(
        ..., description="Thread continuation ID for multi-turn conversations across different tools"
    )
    note: str = Field(..., description="Message explaining continuation opportunity to CLI agent")
    remaining_turns: int = Field(..., description="Number of conversation turns remaining")


class ToolOutput(BaseModel):
    """Standardized output format for all tools"""

    status: Literal[
        "success",
        "error",
        "files_required_to_continue",
        "full_codereview_required",
        "focused_review_required",
        "test_sample_needed",
        "more_tests_required",
        "refactor_analysis_complete",
        "trace_complete",
        "resend_prompt",
        "code_too_large",
        "continuation_available",
        "no_bug_found",
    ] = "success"
    content: Optional[str] = Field(None, description="The main content/response from the tool")
    content_type: Literal["text", "markdown", "json"] = "text"
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)
    continuation_offer: Optional[ContinuationOffer] = Field(
        None, description="Optional offer for Agent to continue conversation"
    )


# Special status models used by specific tools
# (Not needed in clink-only mode, but kept for compatibility with shared code)

SPECIAL_STATUS_MODELS = {
    "files_required_to_continue",
    "full_codereview_required",
    "focused_review_required",
    "test_sample_needed",
    "more_tests_required",
    "refactor_analysis_complete",
    "trace_complete",
    "resend_prompt",
    "code_too_large",
    "continuation_available",
    "no_bug_found",
}
