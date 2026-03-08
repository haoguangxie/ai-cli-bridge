import json
import os
import re
import shutil

import pytest

from clink import get_registry
from clink.registry import RegistryLoadError
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError

AUTH_SKIP_PATTERNS = (
    r"\bunauthenticated\b",
    r"\bauthentication required\b",
    r"\bunauthorized\b",
    r"\bnot logged in\b",
    r"\bplease log ?in\b",
    r"\blogin required\b",
    r"\bmissing (api )?key\b",
    r"\bapi key required\b",
    r"\btoken expired\b",
    r"\bmissing credentials?\b",
    r"\bcredentials? required\b",
    r"\binvalid credentials?\b",
    r"\bno credentials?\b",
)


def _list_configured_clis() -> list[str]:
    try:
        return get_registry().list_clients()
    except RegistryLoadError:
        return []


def _configured_cli_params() -> list[object]:
    configured = _list_configured_clis()
    if not configured:
        return [
            pytest.param(
                None,
                marks=pytest.mark.skip(reason="No CLI clients configured in clink registry"),
                id="no-configured-cli",
            )
        ]
    return [pytest.param(name, id=name) for name in sorted(configured)]


def _skip_if_not_configured(cli_name: str) -> None:
    configured = _list_configured_clis()
    if cli_name not in configured:
        pytest.skip(f"CLI '{cli_name}' is not configured in clink registry; skipping integration test")


def _skip_if_executable_missing(cli_name: str) -> None:
    try:
        client = get_registry().get_client(cli_name)
    except (RegistryLoadError, KeyError):
        pytest.skip(f"CLI '{cli_name}' is not configured in clink registry; skipping integration test")

    executable = client.executable[0] if client.executable else ""
    if not executable or shutil.which(executable) is None:
        pytest.skip(
            f"CLI '{cli_name}' is configured in clink registry but executable '{executable or '<missing>'}' "
            "is not on PATH"
        )


def _skip_if_provider_credentials_missing(cli_name: str) -> None:
    # Gemini CLI may depend on provider keys even when command exists.
    if cli_name == "gemini" and not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        pytest.skip("CLI 'gemini' is configured but GEMINI_API_KEY/GOOGLE_API_KEY is not set")


def _parse_error_reason(payload: dict[str, object]) -> str:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        message = metadata.get("message")
        if isinstance(message, str) and message.strip():
            return message
        stderr = metadata.get("stderr")
        if isinstance(stderr, str) and stderr.strip():
            return stderr
        stdout = metadata.get("stdout")
        if isinstance(stdout, str) and stdout.strip():
            return stdout

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return "CLI reported an error"


def _collect_error_text(payload: dict[str, object]) -> str:
    segments: list[str] = []

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        segments.append(content)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("message", "stderr", "stdout", "error"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                segments.append(value)

    return "\n".join(segments)


def _is_auth_or_credential_error(*messages: str) -> bool:
    combined = "\n".join(message for message in messages if message).casefold()
    return any(re.search(pattern, combined) for pattern in AUTH_SKIP_PATTERNS)


async def _run_single_digit_sum(cli_name: str) -> dict[str, object]:
    _skip_if_not_configured(cli_name)
    _skip_if_executable_missing(cli_name)
    _skip_if_provider_credentials_missing(cli_name)

    tool = CLinkTool()
    prompt = "Respond with a single digit equal to the sum of 2 + 2. Output only that digit."

    try:
        results = await tool.execute(
            {
                "prompt": prompt,
                "cli_name": cli_name,
                "role": "default",
                "absolute_file_paths": [],
                "images": [],
            }
        )
    except ToolExecutionError as exc:
        reason = str(exc)
        raw_payload = exc.payload
        parsed_error_text = ""
        try:
            payload = json.loads(exc.payload)
            if isinstance(payload, dict):
                reason = _parse_error_reason(payload)
                parsed_error_text = _collect_error_text(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if _is_auth_or_credential_error(reason, parsed_error_text, raw_payload):
            pytest.skip(f"Skipping {cli_name} integration test due to missing auth/credentials: {reason}")
        pytest.fail(
            f"{cli_name} integration test failed with ToolExecutionError.\n"
            f"Reason: {reason}\n"
            f"Full error payload:\n{raw_payload}"
        )

    assert results, "clink tool returned no outputs"
    payload = json.loads(results[0].text)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name", _configured_cli_params())
async def test_clink_configured_cli_single_digit_sum(cli_name: str | None):
    if cli_name is None:
        pytest.skip("No CLI clients configured in clink registry")

    payload = await _run_single_digit_sum(cli_name)
    status = payload["status"]

    if status == "error":
        reason = _parse_error_reason(payload)
        payload_dump = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if _is_auth_or_credential_error(reason, _collect_error_text(payload), payload_dump):
            pytest.skip(f"Skipping {cli_name} integration test due to missing auth/credentials: {reason}")
        pytest.fail(
            f"{cli_name} integration test returned status=error.\n"
            f"Reason: {reason}\n"
            f"Full error payload:\n{payload_dump}"
        )

    content = payload.get("content", "").strip()
    first_line = content.split("\n")[0].strip() if content else ""
    assert status in {"success", "continuation_available"}
    assert first_line == "4" or "4" in content, f"Expected '4' in response, got: {content[:100]}"

    if status == "continuation_available":
        offer = payload.get("continuation_offer") or {}
        assert offer.get("continuation_id"), "Expected continuation metadata when status indicates availability"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clink_gemini_single_digit_sum_if_configured():
    payload = await _run_single_digit_sum("gemini")
    status = payload["status"]

    if status == "error":
        reason = _parse_error_reason(payload)
        payload_dump = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if _is_auth_or_credential_error(reason, _collect_error_text(payload), payload_dump):
            pytest.skip(f"Skipping gemini integration test due to missing auth/credentials: {reason}")
        pytest.fail(
            "gemini integration test returned status=error.\n"
            f"Reason: {reason}\n"
            f"Full error payload:\n{payload_dump}"
        )

    assert status in {"success", "continuation_available"}

    content = payload.get("content", "").strip()
    first_line = content.split("\n")[0].strip() if content else ""
    assert first_line == "4" or "4" in content, f"Expected '4' in response, got: {content[:100]}"

    if status == "continuation_available":
        offer = payload.get("continuation_offer") or {}
        assert offer.get("continuation_id"), "Expected continuation metadata when status indicates availability"
