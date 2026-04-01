import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import clink.agents.base as base_module
from clink.agents.base import CLIAgentError
from clink.agents.claude import ClaudeAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole


class DummyProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.stdin_data: bytes | None = None
        self.pid = 99999  # Fake PID for testing

    async def communicate(self, input_data=None):
        if input_data is not None:
            self.stdin_data = input_data
        return self._stdout, self._stderr


class HangingProcess:
    def __init__(self, pid: int):
        self.pid = pid

    async def communicate(self, input_data=None):
        del input_data
        await asyncio.Future()


class _FakeCPUProcess:
    def __init__(self, pid: int, totals: list[float]):
        self.pid = pid
        self._totals = iter(totals)
        self._last_total = totals[-1]

    def cpu_times(self):
        try:
            self._last_total = next(self._totals)
        except StopIteration:
            pass
        return SimpleNamespace(user=self._last_total, system=0.0)


class _FakeRootProcess(_FakeCPUProcess):
    def __init__(self, pid: int, totals: list[float], children: list[_FakeCPUProcess]):
        super().__init__(pid, totals)
        self._children = children

    def children(self, recursive=True):
        assert recursive is True
        return self._children


@pytest.fixture()
def claude_agent():
    prompt_path = Path("systemprompts/clink/default.txt").resolve()
    role = ResolvedCLIRole(name="default", prompt_path=prompt_path, role_args=[])
    client = ResolvedCLIClient(
        name="claude",
        executable=["claude"],
        internal_args=["--print", "--output-format", "json"],
        config_args=["--permission-mode", "acceptEdits"],
        env={},
        timeout_seconds=30,
        cpu_idle_timeout_seconds=60,
        parser="claude_json",
        runner="claude",
        roles={"default": role},
        output_to_file=None,
        working_dir=None,
    )
    return ClaudeAgent(client), role


async def _run_agent_with_process(monkeypatch, agent, role, process, *, system_prompt="System prompt"):
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    def fake_which(executable_name):
        return f"/usr/bin/{executable_name}"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(shutil, "which", fake_which)

    return await agent.run(
        role=role,
        prompt="Respond with 42",
        system_prompt=system_prompt,
        files=[],
        images=[],
    )


@pytest.mark.asyncio
async def test_claude_agent_injects_system_prompt(monkeypatch, claude_agent):
    agent, role = claude_agent
    stdout_payload = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "42",
        }
    ).encode()
    process = DummyProcess(stdout=stdout_payload)

    result = await _run_agent_with_process(monkeypatch, agent, role, process)

    assert "--append-system-prompt" in result.sanitized_command
    idx = result.sanitized_command.index("--append-system-prompt")
    assert result.sanitized_command[idx + 1] == "System prompt"
    assert process.stdin_data.decode().startswith("Respond with 42")


@pytest.mark.asyncio
async def test_claude_agent_recovers_error_payload(monkeypatch, claude_agent):
    agent, role = claude_agent
    stdout_payload = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": "API Error",
        }
    ).encode()
    process = DummyProcess(stdout=stdout_payload, returncode=2)

    result = await _run_agent_with_process(monkeypatch, agent, role, process)

    assert result.returncode == 2
    assert result.parsed.content == "API Error"
    assert result.parsed.metadata["is_error"] is True


@pytest.mark.asyncio
async def test_claude_agent_propagates_unparseable_output(monkeypatch, claude_agent):
    agent, role = claude_agent
    process = DummyProcess(stdout=b"", returncode=1)

    with pytest.raises(CLIAgentError):
        await _run_agent_with_process(monkeypatch, agent, role, process)


@pytest.mark.asyncio
async def test_activity_monitor_times_out_despite_child_history(monkeypatch, claude_agent):
    agent, _ = claude_agent
    process = HangingProcess(pid=4242)
    child = _FakeCPUProcess(pid=4243, totals=[100.0, 100.01, 100.03])
    proc = _FakeRootProcess(pid=process.pid, totals=[0.0, 0.0, 0.0], children=[child])

    monkeypatch.setattr(base_module.psutil, "Process", lambda pid: proc)

    start = asyncio.get_running_loop().time()
    with pytest.raises(asyncio.TimeoutError, match="No CPU activity for 1.5s"):
        await agent._communicate_with_activity_monitor(
            process=process,
            input_data=b"",
            idle_timeout=1.5,
            hard_timeout=5.0,
        )
    assert asyncio.get_running_loop().time() - start < 3.5
