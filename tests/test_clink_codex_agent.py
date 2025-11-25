import asyncio
import shutil
from pathlib import Path

import pytest

from clink.agents.base import CLIAgentError
from clink.agents.codex import CodexAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole


class DummyStdin:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:  # pragma: no cover - compatibility shim
        return

    def close(self) -> None:
        self.closed = True


class DummyProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = DummyStdin()
        if stdout:
            self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        if stderr:
            self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = returncode
        self._done = asyncio.Event()
        self._done.set()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def kill(self):
        self.returncode = -9
        self._done.set()
        self.stdout.feed_eof()
        self.stderr.feed_eof()


class HangingProcess(DummyProcess):
    def __init__(self):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = DummyStdin()
        self.returncode: int | None = None
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode if self.returncode is not None else -9

    def kill(self):
        self.returncode = -9
        self._done.set()
        self.stdout.feed_eof()
        self.stderr.feed_eof()


@pytest.fixture()
def codex_agent():
    prompt_path = Path("systemprompts/clink/codex_default.txt").resolve()
    role = ResolvedCLIRole(name="default", prompt_path=prompt_path, role_args=[])
    client = ResolvedCLIClient(
        name="codex",
        executable=["codex"],
        internal_args=["exec"],
        config_args=["--json", "--dangerously-bypass-approvals-and-sandbox"],
        env={},
        timeout_seconds=30,
        idle_timeout_seconds=None,
        parser="codex_jsonl",
        roles={"default": role},
        output_to_file=None,
        working_dir=None,
    )
    return CodexAgent(client), role


async def _run_agent_with_process(monkeypatch, agent, role, process):
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    def fake_which(executable_name):
        return f"/usr/bin/{executable_name}"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(shutil, "which", fake_which)
    return await agent.run(role=role, prompt="do something", files=[], images=[])


@pytest.mark.asyncio
async def test_codex_agent_recovers_jsonl(monkeypatch, codex_agent):
    agent, role = codex_agent
    stdout = b"""
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Hello from Codex"}}
{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}
"""
    process = DummyProcess(stdout=stdout, returncode=124)
    result = await _run_agent_with_process(monkeypatch, agent, role, process)

    assert result.returncode == 124
    assert "Hello from Codex" in result.parsed.content
    assert result.parsed.metadata["usage"]["output_tokens"] == 5


@pytest.mark.asyncio
async def test_codex_agent_propagates_invalid_json(monkeypatch, codex_agent):
    agent, role = codex_agent
    stdout = b"not json"
    process = DummyProcess(stdout=stdout, returncode=1)

    with pytest.raises(CLIAgentError):
        await _run_agent_with_process(monkeypatch, agent, role, process)


@pytest.mark.asyncio
async def test_codex_agent_idle_timeout(monkeypatch, codex_agent):
    agent, role = codex_agent
    agent.client.idle_timeout_seconds = 1
    process = HangingProcess()

    with pytest.raises(CLIAgentError) as excinfo:
        await _run_agent_with_process(monkeypatch, agent, role, process)

    assert "no output" in str(excinfo.value)
