import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import clink.agents.base as base_module
from clink.agents.base import CLIAgentError
from clink.agents.codex import CodexAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole


class DummyProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 99999  # Fake PID for testing

    async def communicate(self, _input=None):
        return self._stdout, self._stderr


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
        cpu_idle_timeout_seconds=60,
        parser="codex_jsonl",
        roles={"default": role},
        output_to_file=None,
        working_dir=None,
    )
    return CodexAgent(client), role


async def _run_agent_with_process(monkeypatch, agent, role, process, *, extra_args=()):
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    def fake_which(executable_name):
        return f"/usr/bin/{executable_name}"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(shutil, "which", fake_which)
    return await agent.run(role=role, prompt="do something", files=[], images=[], extra_args=extra_args)


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
async def test_codex_agent_filters_denied_extra_args(monkeypatch, codex_agent):
    agent, role = codex_agent
    stdout = b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'
    process = DummyProcess(stdout=stdout, returncode=0)

    result = await _run_agent_with_process(
        monkeypatch,
        agent,
        role,
        process,
        extra_args=["--json", "--enable", "shell", "--worktree", "exec"],
    )

    # Internal reserved args remain, denied extra_args are removed, and allowed flag values are preserved.
    assert result.sanitized_command.count("exec") == 2
    assert result.sanitized_command.count("--json") == 1
    assert "--enable" not in result.sanitized_command
    assert "shell" not in result.sanitized_command
    assert "--worktree" in result.sanitized_command
    assert result.sanitized_command[result.sanitized_command.index("--worktree") + 1] == "exec"


@pytest.mark.asyncio
async def test_codex_agent_redacts_sensitive_extra_arg_values(monkeypatch, codex_agent):
    agent, role = codex_agent
    stdout = b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'
    process = DummyProcess(stdout=stdout, returncode=0)

    result = await _run_agent_with_process(
        monkeypatch,
        agent,
        role,
        process,
        extra_args=[
            "--api-key",
            "very-secret-token",
            "--session-token=abc123",
            "--worktree",
            "task-2",
        ],
    )

    assert "--api-key" in result.sanitized_command
    api_key_index = result.sanitized_command.index("--api-key")
    assert result.sanitized_command[api_key_index + 1] == "[REDACTED]"
    assert "--session-token=[REDACTED]" in result.sanitized_command
    assert "--worktree" in result.sanitized_command
    assert "task-2" in result.sanitized_command


def test_child_cpu_history_is_not_recounted_as_fake_delta(monkeypatch, codex_agent):
    agent, _ = codex_agent
    child = _FakeCPUProcess(pid=4243, totals=[100.0, 100.01, 100.04])
    proc = _FakeRootProcess(pid=4242, totals=[10.0, 10.0, 10.0], children=[child])

    monkeypatch.setattr(base_module.psutil, "Process", lambda pid: proc)

    baseline = agent._get_total_cpu_time(proc.pid)
    quiet_sample = agent._get_total_cpu_time(proc.pid)
    active_sample = agent._get_total_cpu_time(proc.pid)

    assert quiet_sample - baseline == pytest.approx(0.0)
    assert active_sample - quiet_sample == pytest.approx(0.03, abs=1e-9)
    assert active_sample == pytest.approx(10.03, abs=1e-9)
