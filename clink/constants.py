"""Internal defaults and constants for clink."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 1800  # Hard timeout as fallback (30 minutes)
DEFAULT_IO_IDLE_TIMEOUT_SECONDS = 300  # 5 minutes without CPU activity = stuck
# NOTE: Idle timeout reduced from 1200→300 after fixing per-child noise filtering
# in base.py. Previously MCP server child process CPU noise prevented idle timeout
# from ever firing, so 1200s was meaningless. Now that it works properly, 5 minutes
# without ANY real CPU activity is a clear signal the process is stuck.
DEFAULT_STREAM_LIMIT = 10 * 1024 * 1024  # 10MB per stream

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_PROMPTS_DIR = PROJECT_ROOT / "systemprompts" / "clink"
CONFIG_DIR = PROJECT_ROOT / "conf" / "cli_clients"
USER_CONFIG_DIR = Path.home() / ".pal" / "cli_clients"


@dataclass(frozen=True)
class CLIInternalDefaults:
    """Internal defaults applied to a CLI client during registry load."""

    parser: str
    additional_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    default_role_prompt: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    cpu_idle_timeout_seconds: int = DEFAULT_IO_IDLE_TIMEOUT_SECONDS
    runner: str | None = None


INTERNAL_DEFAULTS: dict[str, CLIInternalDefaults] = {
    "codex": CLIInternalDefaults(
        parser="codex_jsonl",
        additional_args=["exec"],
        default_role_prompt="systemprompts/clink/default.txt",
        runner="codex",
        cpu_idle_timeout_seconds=DEFAULT_IO_IDLE_TIMEOUT_SECONDS,
    ),
    "claude": CLIInternalDefaults(
        parser="claude_json",
        additional_args=["--print", "--output-format", "json"],
        default_role_prompt="systemprompts/clink/default.txt",
        runner="claude",
        cpu_idle_timeout_seconds=DEFAULT_IO_IDLE_TIMEOUT_SECONDS,
    ),
}
