"""Execute configured CLI agents for the clink tool and parse output."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil

from clink.constants import DEFAULT_IO_IDLE_TIMEOUT_SECONDS, DEFAULT_STREAM_LIMIT
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers import BaseParser, ParsedCLIResponse, ParserError, get_parser

logger = logging.getLogger("clink.agent")


@dataclass
class AgentOutput:
    """Container returned by CLI agents after successful execution."""

    parsed: ParsedCLIResponse
    sanitized_command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    parser_name: str
    output_file_content: str | None = None


class CLIAgentError(RuntimeError):
    """Raised when a CLI agent fails (non-zero exit, timeout, parse errors)."""

    def __init__(self, message: str, *, returncode: int | None = None, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class BaseCLIAgent:
    """Execute a configured CLI command and parse its output."""

    def __init__(self, client: ResolvedCLIClient):
        self.client = client
        self._parser: BaseParser = get_parser(client.parser)
        self._logger = logging.getLogger(f"clink.runner.{client.name}")

    async def run(
        self,
        *,
        role: ResolvedCLIRole,
        prompt: str,
        system_prompt: str | None = None,
        files: Sequence[str],
        images: Sequence[str],
    ) -> AgentOutput:
        # Files and images are already embedded into the prompt by the tool; they are
        # accepted here only to keep parity with SimpleTool callers.
        _ = (files, images)
        # The runner simply executes the configured CLI command for the selected role.
        command = self._build_command(role=role, system_prompt=system_prompt)
        env = self._build_environment()

        # Resolve executable path for cross-platform compatibility (especially Windows)
        executable_name = command[0]
        resolved_executable = shutil.which(executable_name)
        if resolved_executable is None:
            raise CLIAgentError(
                f"Executable '{executable_name}' not found in PATH for CLI '{self.client.name}'. "
                f"Ensure the command is installed and accessible."
            )
        command[0] = resolved_executable

        sanitized_command = list(command)

        cwd = str(self.client.working_dir) if self.client.working_dir else None
        limit = DEFAULT_STREAM_LIMIT

        stdout_text = ""
        stderr_text = ""
        output_file_content: str | None = None
        start_time = time.monotonic()

        output_file_path: Path | None = None
        command_with_output_flag = list(command)

        if self.client.output_to_file:
            fd, tmp_path = tempfile.mkstemp(prefix="clink-", suffix=".json")
            os.close(fd)
            output_file_path = Path(tmp_path)
            flag_template = self.client.output_to_file.flag_template
            try:
                rendered_flag = flag_template.format(path=str(output_file_path))
            except KeyError as exc:  # pragma: no cover - defensive
                raise CLIAgentError(f"Invalid output flag template '{flag_template}': missing placeholder {exc}")
            command_with_output_flag.extend(shlex.split(rendered_flag))
            sanitized_command = list(command_with_output_flag)

        self._logger.debug("Executing CLI command: %s", " ".join(sanitized_command))
        if cwd:
            self._logger.debug("Working directory: %s", cwd)

        try:
            process = await asyncio.create_subprocess_exec(
                *command_with_output_flag,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                limit=limit,
                env=env,
            )
        except FileNotFoundError as exc:
            raise CLIAgentError(f"Executable not found for CLI '{self.client.name}': {exc}") from exc

        try:
            stdout_bytes, stderr_bytes = await self._communicate_with_activity_monitor(
                process=process,
                input_data=prompt.encode("utf-8"),
                idle_timeout=DEFAULT_IO_IDLE_TIMEOUT_SECONDS,
                hard_timeout=self.client.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            try:
                await asyncio.wait_for(process.communicate(), timeout=5.0)
            except asyncio.TimeoutError:
                self._logger.warning(
                    "Process cleanup timed out after 5 seconds for CLI '%s'",
                    self.client.name,
                )
            raise CLIAgentError(
                f"CLI '{self.client.name}' timed out (no IO activity for {DEFAULT_IO_IDLE_TIMEOUT_SECONDS}s or exceeded {self.client.timeout_seconds}s total)",
                returncode=None,
            ) from exc

        duration = time.monotonic() - start_time
        return_code = process.returncode
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        if output_file_path and output_file_path.exists():
            output_file_content = output_file_path.read_text(encoding="utf-8", errors="replace")
            if self.client.output_to_file and self.client.output_to_file.cleanup:
                try:
                    output_file_path.unlink()
                except OSError:  # pragma: no cover - best effort cleanup
                    pass

            if output_file_content and not stdout_text.strip():
                stdout_text = output_file_content

        if return_code != 0:
            recovered = self._recover_from_error(
                returncode=return_code,
                stdout=stdout_text,
                stderr=stderr_text,
                sanitized_command=sanitized_command,
                duration_seconds=duration,
                output_file_content=output_file_content,
            )
            if recovered is not None:
                return recovered

        if return_code != 0:
            raise CLIAgentError(
                f"CLI '{self.client.name}' exited with status {return_code}",
                returncode=return_code,
                stdout=stdout_text,
                stderr=stderr_text,
            )

        try:
            parsed = self._parser.parse(stdout_text, stderr_text)
        except ParserError as exc:
            raise CLIAgentError(
                f"Failed to parse output from CLI '{self.client.name}': {exc}",
                returncode=return_code,
                stdout=stdout_text,
                stderr=stderr_text,
            ) from exc

        return AgentOutput(
            parsed=parsed,
            sanitized_command=sanitized_command,
            returncode=return_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_seconds=duration,
            parser_name=self._parser.name,
            output_file_content=output_file_content,
        )

    def _build_command(self, *, role: ResolvedCLIRole, system_prompt: str | None) -> list[str]:
        base = list(self.client.executable)
        base.extend(self.client.internal_args)
        base.extend(self.client.config_args)
        base.extend(role.role_args)

        return base

    def _build_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.client.env)
        return env

    # ------------------------------------------------------------------
    # CPU-based timeout monitoring
    # ------------------------------------------------------------------

    def _get_total_cpu_time(self, pid: int) -> float:
        """Get total CPU time (user + system) for process and all children.

        Works on macOS/Linux/Windows unlike io_counters() which is Linux-only.
        """
        total = 0.0
        try:
            proc = psutil.Process(pid)
            # Get CPU times for main process
            try:
                cpu = proc.cpu_times()
                total += cpu.user + cpu.system
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            # Get CPU times for all children (recursive)
            try:
                for child in proc.children(recursive=True):
                    try:
                        child_cpu = child.cpu_times()
                        total += child_cpu.user + child_cpu.system
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        pass
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        except psutil.NoSuchProcess:
            pass
        return total

    async def _communicate_with_activity_monitor(
        self,
        *,
        process: asyncio.subprocess.Process,
        input_data: bytes,
        idle_timeout: float,
        hard_timeout: float,
    ) -> tuple[bytes, bytes]:
        """Communicate with process while monitoring CPU activity.

        Unlike simple timeout, this monitors actual CPU activity of the process.
        If the process has no CPU activity for idle_timeout seconds, it's
        considered stuck. This allows long-running processes that are actively
        doing work (like waiting for API responses) to continue.

        Args:
            process: The subprocess to communicate with
            input_data: Data to send to stdin
            idle_timeout: Seconds without CPU activity before timeout
            hard_timeout: Absolute maximum time to wait (safety net)

        Returns:
            Tuple of (stdout_bytes, stderr_bytes)

        Raises:
            asyncio.TimeoutError: If process is idle or exceeds hard timeout
        """
        pid = process.pid
        start_time = time.monotonic()
        last_cpu_time = self._get_total_cpu_time(pid)
        last_activity_time = start_time

        # Start the communicate task
        communicate_task = asyncio.create_task(process.communicate(input_data))

        try:
            while True:
                # Check if communicate is done
                done, _ = await asyncio.wait({communicate_task}, timeout=1.0)
                if done:
                    return communicate_task.result()

                current_time = time.monotonic()
                elapsed = current_time - start_time

                # Check hard timeout
                if elapsed >= hard_timeout:
                    self._logger.warning(
                        "CLI '%s' exceeded hard timeout of %ds",
                        self.client.name,
                        hard_timeout,
                    )
                    raise asyncio.TimeoutError(f"Hard timeout after {hard_timeout}s")

                # Check CPU activity
                current_cpu_time = self._get_total_cpu_time(pid)
                if current_cpu_time > last_cpu_time:
                    # CPU activity detected, reset idle timer
                    cpu_delta = current_cpu_time - last_cpu_time
                    self._logger.debug(
                        "CLI '%s' CPU activity: +%.3fs (total elapsed: %.1fs)",
                        self.client.name,
                        cpu_delta,
                        elapsed,
                    )
                    last_cpu_time = current_cpu_time
                    last_activity_time = current_time
                else:
                    # No CPU activity, check idle timeout
                    idle_time = current_time - last_activity_time
                    if idle_time >= idle_timeout:
                        self._logger.warning(
                            "CLI '%s' no CPU activity for %.1fs, considering stuck",
                            self.client.name,
                            idle_time,
                        )
                        raise asyncio.TimeoutError(f"No CPU activity for {idle_timeout}s")

        except asyncio.TimeoutError:
            communicate_task.cancel()
            try:
                await communicate_task
            except asyncio.CancelledError:
                pass
            raise

    # ------------------------------------------------------------------
    # Error recovery hooks
    # ------------------------------------------------------------------

    def _recover_from_error(
        self,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        sanitized_command: list[str],
        duration_seconds: float,
        output_file_content: str | None,
    ) -> AgentOutput | None:
        """Hook for subclasses to convert CLI errors into successful outputs.

        Return an AgentOutput to treat the failure as success, or None to signal
        that normal error handling should proceed.
        """

        return None
