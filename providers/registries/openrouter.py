"""OpenRouter model registry for managing model configurations and aliases."""

from __future__ import annotations

import logging
import httpx
from utils.env import get_env
from ..shared import ModelCapabilities, ProviderType
from .base import CAPABILITY_FIELD_NAMES, CapabilityModelRegistry

logger = logging.getLogger(__name__)


class OpenRouterModelRegistry(CapabilityModelRegistry):
    """Capability registry backed by ``conf/openrouter_models.json`` or dynamic API."""

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(
            env_var_name="OPENROUTER_MODELS_CONFIG_PATH",
            default_filename="openrouter_models.json",
            provider=ProviderType.OPENROUTER,
            friendly_prefix="OpenRouter ({model})",
            config_path=config_path,
        )

    def _load_config_data(self) -> dict:
        """Load model data from API or fallback to local JSON."""
        base_url = get_env("OPENROUTER_BASE_URL")

        if base_url:
            api_url = f"{base_url.rstrip('/')}/models"
            try:
                logger.debug(f"Fetching models from API: {api_url}")

                headers = {}
                api_key = get_env("OPENROUTER_API_KEY")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                response = httpx.get(api_url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()

                if "data" in data and isinstance(data["data"], list):
                    local_data = super()._load_config_data()
                    local_map = {m["model_name"]: m for m in local_data.get("models", [])}

                    models = self._convert_api_models(data["data"], local_map)
                    logger.info(f"Loaded {len(models)} models from API")
                    return {"models": models}
                else:
                    logger.warning("API response missing 'data' field, falling back to local config")
            except Exception as e:
                logger.warning(f"Failed to fetch models from API: {e}, falling back to local config")

        return super()._load_config_data()

    def _convert_api_models(self, api_models: list, local_map: dict) -> list:
        """Convert API models using local config for known patterns."""
        converted = []
        for model in api_models:
            model_id = model.get("id", "")
            if not model_id:
                continue

            local_config = self._find_matching_local_config(model_id, local_map)
            if local_config:
                config = dict(local_config)
                config["model_name"] = model_id
                # Remove aliases to avoid conflicts
                config["aliases"] = []
                converted.append(config)

        return converted

    def _find_matching_local_config(self, model_id: str, local_map: dict) -> dict:
        """Find matching local config by pattern."""
        mid = model_id.lower()

        patterns = {
            "claude-sonnet-4-5": "anthropic/claude-sonnet-4.5",
            "claude-opus-4": "anthropic/claude-opus-4.1",
            "claude-sonnet-4-": "anthropic/claude-sonnet-4.1",
            "claude-3-5-haiku": "anthropic/claude-3.5-haiku",
            "gemini-2.5-pro": "google/gemini-2.5-pro",
            "gemini-2.5-flash": "google/gemini-2.5-flash",
            "gpt-5-pro": "openai/gpt-5-pro",
            "gpt-5-codex": "openai/gpt-5-codex",
            "gpt-5-mini": "openai/gpt-5-mini",
            "gpt-5-nano": "openai/gpt-5-nano",
            "gpt-5": "openai/gpt-5",
            "deepseek-r1": "deepseek/deepseek-r1-0528",
            "grok-4": "x-ai/grok-4",
            "o3-pro": "openai/o3-pro",
            "o3-mini-high": "openai/o3-mini-high",
            "o3-mini": "openai/o3-mini",
            "o4-mini": "openai/o4-mini",
            "o3": "openai/o3",
            "mistral-large": "mistralai/mistral-large-2411",
            "llama-3-70b": "meta-llama/llama-3-70b",
            "llama-3-sonar": "perplexity/llama-3-sonar-large-32k-online",
        }

        for pattern, local_key in patterns.items():
            if pattern in mid and local_key in local_map:
                return local_map[local_key]

        return None

    def _finalise_entry(self, entry: dict) -> tuple[ModelCapabilities, dict]:
        provider_override = entry.get("provider")
        if isinstance(provider_override, str):
            entry_provider = ProviderType(provider_override.lower())
        elif isinstance(provider_override, ProviderType):
            entry_provider = provider_override
        else:
            entry_provider = ProviderType.OPENROUTER

        if entry_provider == ProviderType.CUSTOM:
            entry.setdefault("friendly_name", f"Custom ({entry['model_name']})")
        else:
            entry.setdefault("friendly_name", f"OpenRouter ({entry['model_name']})")

        filtered = {k: v for k, v in entry.items() if k in CAPABILITY_FIELD_NAMES}
        filtered.setdefault("provider", entry_provider)
        capability = ModelCapabilities(**filtered)
        return capability, {}
