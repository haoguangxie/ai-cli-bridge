"""Tests for the Claude CLI JSON parser."""

import json

import pytest

from clink.parsers.base import ParserError
from clink.parsers.claude import ClaudeJSONParser


def _build_success_payload() -> str:
    return (
        '{"type":"result","subtype":"success","is_error":false,"duration_ms":1234,'
        '"duration_api_ms":1800,"num_turns":1,"result":"42","session_id":"abc","total_cost_usd":0.12,'
        '"usage":{"input_tokens":10,"output_tokens":5},'
        '"modelUsage":{"claude-sonnet-4-5-20250929":{"inputTokens":10,"outputTokens":5}}}'
    )


def test_claude_parser_extracts_result_and_metadata():
    parser = ClaudeJSONParser()
    stdout = _build_success_payload()

    parsed = parser.parse(stdout=stdout, stderr="")

    assert parsed.content == "42"
    assert parsed.metadata["model_used"] == "claude-sonnet-4-5-20250929"
    assert parsed.metadata["usage"]["output_tokens"] == 5
    assert parsed.metadata["is_error"] is False


def test_claude_parser_falls_back_to_message():
    parser = ClaudeJSONParser()
    stdout = '{"type":"result","is_error":true,"message":"API error message"}'

    parsed = parser.parse(stdout=stdout, stderr="warning")

    assert parsed.content == "API error message"
    assert parsed.metadata["is_error"] is True
    assert parsed.metadata["stderr"] == "warning"


def test_claude_parser_requires_output():
    parser = ClaudeJSONParser()

    with pytest.raises(ParserError):
        parser.parse(stdout="", stderr="")


def test_claude_parser_handles_empty_result_on_success():
    """Regression: Claude CLI returns success with empty result when work was done via tool use."""
    parser = ClaudeJSONParser()
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 363284,
            "duration_api_ms": 69820,
            "num_turns": 7,
            "result": "",
            "stop_reason": None,
            "session_id": "c289108c-8180-4fa5-832e-e35b5c4458a2",
            "total_cost_usd": 0.57,
            "usage": {"input_tokens": 31604, "output_tokens": 1332},
            "modelUsage": {
                "claude-opus-4-6": {"inputTokens": 31604, "outputTokens": 1332}
            },
        }
    )

    parsed = parser.parse(stdout=stdout, stderr="")

    assert parsed.content  # should not be empty
    assert "completed successfully" in parsed.content.lower()
    assert parsed.metadata["is_error"] is False
    assert parsed.metadata["session_id"] == "c289108c-8180-4fa5-832e-e35b5c4458a2"


def test_claude_parser_still_errors_on_empty_result_with_error_flag():
    """Empty result with is_error=true and no message should still raise."""
    parser = ClaudeJSONParser()
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "",
        }
    )

    with pytest.raises(ParserError):
        parser.parse(stdout=stdout, stderr="")


def test_claude_parser_handles_array_payload_with_result_event():
    parser = ClaudeJSONParser()
    events = [
        {"type": "system", "session_id": "abc"},
        {"type": "assistant", "message": "intermediate"},
        {
            "type": "result",
            "subtype": "success",
            "result": "42",
            "duration_api_ms": 9876,
            "usage": {"input_tokens": 12, "output_tokens": 3},
        },
    ]
    stdout = json.dumps(events)

    parsed = parser.parse(stdout=stdout, stderr="warning")

    assert parsed.content == "42"
    assert parsed.metadata["duration_api_ms"] == 9876
    assert parsed.metadata["raw_events"] == events
    assert parsed.metadata["raw"] == events
