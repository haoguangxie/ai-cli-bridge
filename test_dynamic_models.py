#!/usr/bin/env python3
"""Test script for dynamic model list from API."""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from providers.registries.openrouter import OpenRouterModelRegistry


def test_dynamic_models():
    """Test loading models from API."""

    # Test with sucloud.vip API
    base_url = "https://sucloud.vip/v1"
    os.environ["OPENROUTER_BASE_URL"] = base_url

    print(f"Testing dynamic model loading from: {base_url}/models")
    print("-" * 60)

    try:
        registry = OpenRouterModelRegistry()
        models = registry.list_models()

        print(f"\n✓ Successfully loaded {len(models)} models from API")
        print("\nFirst 10 models:")
        for i, model in enumerate(models[:10], 1):
            print(f"  {i}. {model}")

        if len(models) > 10:
            print(f"  ... and {len(models) - 10} more models")

        # Test model resolution
        if models:
            test_model = models[0]
            capabilities = registry.get_capabilities(test_model)
            if capabilities:
                print(f"\n✓ Model capabilities test passed")
                print(f"  Model: {capabilities.model_name}")
                print(f"  Context: {capabilities.context_window}")
                print(f"  Max output: {capabilities.max_output_tokens}")

        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_fallback_to_local():
    """Test fallback to local JSON when API fails."""

    # Clear base URL to test local fallback
    if "OPENROUTER_BASE_URL" in os.environ:
        del os.environ["OPENROUTER_BASE_URL"]

    print("\n" + "=" * 60)
    print("Testing fallback to local JSON config")
    print("-" * 60)

    try:
        registry = OpenRouterModelRegistry()
        models = registry.list_models()

        print(f"\n✓ Successfully loaded {len(models)} models from local config")
        print("\nFirst 5 models:")
        for i, model in enumerate(models[:5], 1):
            print(f"  {i}. {model}")

        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Dynamic Model List Test")
    print("=" * 60)

    success = True

    # Test 1: Dynamic API loading
    if not test_dynamic_models():
        success = False

    # Test 2: Fallback to local
    if not test_fallback_to_local():
        success = False

    print("\n" + "=" * 60)
    if success:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed")
    print("=" * 60)

    sys.exit(0 if success else 1)
