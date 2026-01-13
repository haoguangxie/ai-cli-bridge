"""Parser for OpenCode CLI JSON output."""

from __future__ import annotations

import json
from typing import Any

from .base import BaseParser, ParsedCLIResponse, ParserError


class OpenCodeJSONParser(BaseParser):
    """Parse stdout produced by `opencode run --format json`."""

    name = "opencode_json"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        if not stdout.strip():
            raise ParserError("OpenCode CLI returned empty stdout while JSON output was expected")

        events: list[dict[str, Any]] = []
        text_parts: list[str] = []
        metadata: dict[str, Any] = {"events": []}
        error_message: str | None = None

        # Parse JSONL (one JSON object per line)
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                events.append(event)
            except json.JSONDecodeError:
                # Non-JSON line, might be plain text output
                text_parts.append(line)

        metadata["events"] = events

        # Extract content from events
        for event in events:
            event_type = event.get("type", "")

            if event_type == "error":
                error_data = event.get("error", {})
                error_name = error_data.get("name", "UnknownError")
                error_detail = error_data.get("data", {}).get("message", "")
                error_message = f"{error_name}: {error_detail}" if error_detail else error_name
                metadata["error"] = error_data

            elif event_type == "text":
                # Text content from the assistant
                text = event.get("text", "")
                if text:
                    text_parts.append(text)

            elif event_type == "assistant.message.delta":
                # Streaming delta content
                delta = event.get("delta", {})
                content = delta.get("content", "")
                if content:
                    text_parts.append(content)

            elif event_type == "message":
                # Complete message
                content = event.get("content", "")
                if content:
                    text_parts.append(content)

            elif event_type == "tool_use":
                # Tool invocation
                tool_name = event.get("name", "unknown_tool")
                tool_input = event.get("input", {})
                metadata.setdefault("tool_calls", []).append({
                    "name": tool_name,
                    "input": tool_input,
                })

            elif event_type == "session":
                # Session metadata
                session_id = event.get("sessionID")
                if session_id:
                    metadata["session_id"] = session_id

            elif event_type == "usage":
                # Token usage information
                usage = event.get("usage", {})
                if usage:
                    metadata["token_usage"] = usage

            elif event_type == "model":
                # Model information
                model = event.get("model")
                if model:
                    metadata["model_used"] = model

        # Build response content
        response_text = "".join(text_parts).strip()

        if error_message and not response_text:
            # Return error as content if no other content
            if stderr and stderr.strip():
                metadata["stderr"] = stderr.strip()
            return ParsedCLIResponse(content=f"OpenCode error: {error_message}", metadata=metadata)

        if response_text:
            if stderr and stderr.strip():
                metadata["stderr"] = stderr.strip()
            return ParsedCLIResponse(content=response_text, metadata=metadata)

        # Fallback: try to extract any useful content
        if events:
            # Look for any content in the last few events
            for event in reversed(events[-5:]):
                if "content" in event:
                    return ParsedCLIResponse(content=str(event["content"]), metadata=metadata)
                if "text" in event:
                    return ParsedCLIResponse(content=str(event["text"]), metadata=metadata)

        raise ParserError("OpenCode CLI response contains no extractable content")
