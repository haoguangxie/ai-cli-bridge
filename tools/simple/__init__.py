"""
Simple tools for PAL MCP.

Simple tools follow a basic request → AI model → response pattern.
They inherit from SimpleTool which provides streamlined functionality
for tools that don't need multi-step workflows.
"""

from .base import SimpleTool

__all__ = ["SimpleTool"]
