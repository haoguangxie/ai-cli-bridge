"""
Model context management for dynamic token allocation.

This module provides a provider-free abstraction for model-specific token
management in clink-only mode. It uses local model metadata (from conf/*.json)
when available and falls back to a conservative default context window.

CONVERSATION MEMORY INTEGRATION:
This module works closely with the conversation memory system to provide
optimal token allocation for multi-turn conversations:

1. DUAL PRIORITIZATION STRATEGY SUPPORT:
   - Provides separate token budgets for conversation history vs. files
   - Enables the conversation memory system to apply newest-first prioritization
   - Ensures optimal balance between context preservation and new content

2. MODEL-SPECIFIC ALLOCATION:
   - Dynamic allocation based on model capabilities (context window size)
   - Conservative allocation for smaller models
   - Generous allocation for larger models
   - Adapts token distribution ratios based on model capacity

3. CROSS-TOOL CONSISTENCY:
   - Provides consistent token budgets across different tools
   - Enables seamless conversation continuation between tools
   - Supports conversation reconstruction with proper budget management
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from config import DEFAULT_MODEL
from utils.token_utils import DEFAULT_CONTEXT_WINDOW
from utils.token_utils import estimate_tokens as estimate_tokens_util

logger = logging.getLogger(__name__)

_CONF_MODEL_FILES = (
    "openai_models.json",
    "gemini_models.json",
    "openrouter_models.json",
    "xai_models.json",
    "azure_models.json",
    "dial_models.json",
    "custom_models.json",
)

_MODEL_METADATA_CACHE: Optional[dict[str, dict[str, Any]]] = None


@dataclass
class ModelCapabilities:
    """Minimal model capability set for clink-only mode."""

    context_window: int
    supports_extended_thinking: bool = False
    supports_images: bool = False
    supports_temperature: bool = True
    max_image_size_mb: Optional[float] = None


@dataclass
class TokenAllocation:
    """Token allocation strategy for a model."""

    total_tokens: int
    content_tokens: int
    response_tokens: int
    file_tokens: int
    history_tokens: int

    @property
    def available_for_prompt(self) -> int:
        """Tokens available for the actual prompt after allocations."""
        return self.content_tokens - self.file_tokens - self.history_tokens


def _load_model_metadata() -> dict[str, dict[str, Any]]:
    """Load model metadata from conf/*.json and cache the result."""
    global _MODEL_METADATA_CACHE

    if _MODEL_METADATA_CACHE is not None:
        return _MODEL_METADATA_CACHE

    metadata: dict[str, dict[str, Any]] = {}
    conf_dir = Path(__file__).resolve().parent.parent / "conf"

    for filename in _CONF_MODEL_FILES:
        path = conf_dir / filename
        if not path.exists():
            continue

        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            logger.debug("Failed to load model metadata from %s: %s", path, exc)
            continue

        for entry in data.get("models", []):
            if not isinstance(entry, dict):
                continue

            model_name = str(entry.get("model_name") or "").strip()
            if not model_name:
                continue

            key = model_name.lower()
            metadata.setdefault(key, entry)

            aliases = entry.get("aliases") or []
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_key = str(alias or "").strip().lower()
                    if alias_key:
                        metadata.setdefault(alias_key, entry)

    _MODEL_METADATA_CACHE = metadata
    return metadata


def get_available_model_names() -> list[str]:
    """Return available model names from local metadata (deduplicated)."""
    metadata = _load_model_metadata()
    model_names: set[str] = set()
    for entry in metadata.values():
        if not isinstance(entry, dict):
            continue
        model_name = str(entry.get("model_name") or "").strip()
        if model_name:
            model_names.add(model_name)
    return sorted(model_names)


def get_preferred_fallback_model(model_category: Any = None) -> Optional[str]:
    """
    Return a preferred fallback model for clink-only mode.

    model_category is accepted for API parity but is not used in clink-only mode.
    """
    _ = model_category
    if DEFAULT_MODEL and DEFAULT_MODEL.lower() != "auto":
        return DEFAULT_MODEL
    available_models = get_available_model_names()
    if available_models:
        return available_models[0]
    return None


def _resolve_capabilities(model_name: str) -> ModelCapabilities:
    lookup_name = (model_name or "").strip().lower()
    metadata = _load_model_metadata().get(lookup_name) if lookup_name else None

    context_window = DEFAULT_CONTEXT_WINDOW
    supports_extended_thinking = False
    supports_images = False
    supports_temperature = True
    max_image_size_mb = None

    if isinstance(metadata, dict):
        try:
            context_window = int(metadata.get("context_window") or context_window)
        except (TypeError, ValueError):
            context_window = DEFAULT_CONTEXT_WINDOW
        supports_extended_thinking = bool(metadata.get("supports_extended_thinking", False))
        supports_images = bool(metadata.get("supports_images", False))
        supports_temperature = bool(metadata.get("supports_temperature", True))
        max_image_size_mb = metadata.get("max_image_size_mb")
        if max_image_size_mb is not None:
            try:
                max_image_size_mb = float(max_image_size_mb)
            except (TypeError, ValueError):
                max_image_size_mb = None

    if context_window <= 0:
        context_window = DEFAULT_CONTEXT_WINDOW

    return ModelCapabilities(
        context_window=context_window,
        supports_extended_thinking=supports_extended_thinking,
        supports_images=supports_images,
        supports_temperature=supports_temperature,
        max_image_size_mb=max_image_size_mb,
    )


class ModelContext:
    """
    Encapsulates model-specific information and token calculations.

    This class provides a single source of truth for token calculations,
    using local metadata without relying on provider registries.
    """

    def __init__(self, model_name: str, model_option: Optional[str] = None):
        self.model_name = model_name
        self.model_option = model_option  # Store optional model option (e.g., "for", "against", etc.)
        self._capabilities: Optional[ModelCapabilities] = None
        self._token_allocation = None

    @property
    def provider(self):
        """Providers are not available in clink-only mode."""
        raise RuntimeError("Provider system is not available in clink-only mode.")

    @property
    def capabilities(self) -> ModelCapabilities:
        """Get model capabilities lazily from local metadata."""
        if self._capabilities is None:
            self._capabilities = _resolve_capabilities(self.model_name)
        return self._capabilities

    def calculate_token_allocation(self, reserved_for_response: Optional[int] = None) -> TokenAllocation:
        """
        Calculate token allocation based on model capacity and conversation requirements.

        This method implements the core token budget calculation that supports the
        dual prioritization strategy used in conversation memory and file processing:

        TOKEN ALLOCATION STRATEGY:
        1. CONTENT vs RESPONSE SPLIT:
           - Smaller models (< 300K): 60% content, 40% response (conservative)
           - Larger models (≥ 300K): 80% content, 20% response (generous)

        2. CONTENT SUB-ALLOCATION:
           - File tokens: 30-40% of content budget for newest file versions
           - History tokens: 40-50% of content budget for conversation context
           - Remaining: Available for tool-specific prompt content

        3. CONVERSATION MEMORY INTEGRATION:
           - History allocation enables conversation reconstruction in reconstruct_thread_context()
           - File allocation supports newest-first file prioritization in tools
           - Remaining budget passed to tools via _remaining_tokens parameter

        Args:
            reserved_for_response: Override response token reservation

        Returns:
            TokenAllocation with calculated budgets for dual prioritization strategy
        """
        total_tokens = self.capabilities.context_window

        # Dynamic allocation based on model capacity
        if total_tokens < 300_000:
            # Smaller context models: Conservative allocation
            content_ratio = 0.6  # 60% for content
            response_ratio = 0.4  # 40% for response
            file_ratio = 0.3  # 30% of content for files
            history_ratio = 0.5  # 50% of content for history
        else:
            # Larger context models: More generous allocation
            content_ratio = 0.8  # 80% for content
            response_ratio = 0.2  # 20% for response
            file_ratio = 0.4  # 40% of content for files
            history_ratio = 0.4  # 40% of content for history

        # Calculate allocations
        content_tokens = int(total_tokens * content_ratio)
        response_tokens = reserved_for_response or int(total_tokens * response_ratio)

        # Sub-allocations within content budget
        file_tokens = int(content_tokens * file_ratio)
        history_tokens = int(content_tokens * history_ratio)

        allocation = TokenAllocation(
            total_tokens=total_tokens,
            content_tokens=content_tokens,
            response_tokens=response_tokens,
            file_tokens=file_tokens,
            history_tokens=history_tokens,
        )

        logger.debug("Token allocation for %s:", self.model_name)
        logger.debug("  Total: %s", f"{allocation.total_tokens:,}")
        logger.debug("  Content: %s (%s)", f"{allocation.content_tokens:,}", f"{content_ratio:.0%}")
        logger.debug("  Response: %s (%s)", f"{allocation.response_tokens:,}", f"{response_ratio:.0%}")
        logger.debug("  Files: %s (%s of content)", f"{allocation.file_tokens:,}", f"{file_ratio:.0%}")
        logger.debug("  History: %s (%s of content)", f"{allocation.history_tokens:,}", f"{history_ratio:.0%}")

        return allocation

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text using model-agnostic heuristic.
        """
        return estimate_tokens_util(text)

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> "ModelContext":
        """Create ModelContext from tool arguments."""
        model_name = arguments.get("model") or DEFAULT_MODEL
        return cls(model_name)
