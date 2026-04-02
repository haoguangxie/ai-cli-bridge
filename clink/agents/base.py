"""Execute configured CLI agents for the clink tool and parse output."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import signal
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import psutil

from clink.constants import DEFAULT_STREAM_LIMIT
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers import BaseParser, ParsedCLIResponse, ParserError, get_parser

logger = logging.getLogger("clink.agent")

# Global process registry for tracking active processes
# Use threading.RLock (reentrant lock) to avoid deadlock if signal arrives while holding lock
_active_processes: set[int] = set()
_process_lock = threading.RLock()


async def register_process(pid: int) -> None:
    """Register an active process for cleanup tracking."""
    with _process_lock:
        _active_processes.add(pid)
        logger.debug(f"Registered process {pid}, total active: {len(_active_processes)}")


async def unregister_process(pid: int) -> None:
    """Unregister a completed process."""
    with _process_lock:
        _active_processes.discard(pid)
        logger.debug(f"Unregistered process {pid}, total active: {len(_active_processes)}")


async def cleanup_all_processes() -> None:
    """Clean up all active processes."""
    # Copy the process list while holding the lock, then release it
    with _process_lock:
        if not _active_processes:
            return
        processes_to_cleanup = list(_active_processes)

    logger.info(f"Cleaning up {len(processes_to_cleanup)} active CLI processes")

    # Detect platform for process group handling
    is_windows = os.name == "nt"

    # First pass: graceful termination
    for pid in processes_to_cleanup:
        try:
            if is_windows:
                # Windows: terminate process and its children using psutil
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    logger.debug(f"Terminating process {pid} and {len(children)} children (Windows)")
                    parent.terminate()
                    for child in children:
                        child.terminate()
                except psutil.NoSuchProcess:
                    pass
            else:
                # POSIX: use process groups
                logger.debug(f"Sending SIGTERM to process group {pid}")
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.error(f"Failed to terminate process {pid}: {e}")

    # Wait for graceful shutdown
    await asyncio.sleep(2)

    # Second pass: force kill remaining processes
    still_alive = []
    for pid in processes_to_cleanup:
        try:
            if is_windows:
                # Windows: check and force kill using psutil
                try:
                    parent = psutil.Process(pid)
                    if parent.is_running():
                        children = parent.children(recursive=True)
                        logger.warning(f"Force killing process {pid} and {len(children)} children (Windows)")
                        parent.kill()
                        for child in children:
                            child.kill()
                        # Wait briefly and verify process is dead
                        await asyncio.sleep(0.1)
                        if psutil.pid_exists(pid):
                            still_alive.append(pid)
                except psutil.NoSuchProcess:
                    pass
            else:
                # POSIX: check if still alive and force kill
                os.kill(pid, 0)  # Check if still alive
                logger.warning(f"Force killing process {pid}")
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                # Wait briefly and verify process is dead
                await asyncio.sleep(0.1)
                try:
                    os.kill(pid, 0)  # Check if still alive after kill
                    still_alive.append(pid)  # Still alive, add to list
                except ProcessLookupError:
                    pass  # Process is dead, don't add to still_alive
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.error(f"Failed to force kill process {pid}: {e}")
            still_alive.append(pid)

    # Clear the registry, but only remove processes that were successfully cleaned
    with _process_lock:
        for pid in processes_to_cleanup:
            if pid not in still_alive:
                _active_processes.discard(pid)

    if still_alive:
        logger.warning(f"Failed to clean up {len(still_alive)} processes: {still_alive}")

    logger.info("CLI process cleanup completed")


# CPU activity detection thresholds
# Single-sample threshold: minimum CPU delta in one check to count as activity
CPU_ACTIVITY_THRESHOLD = 0.5  # seconds - raised from 0.1 to filter heartbeat/event loop noise

# Sliding window for cumulative activity detection
# If cumulative CPU activity in the window exceeds the threshold, process is considered active
CPU_ACTIVITY_WINDOW_SECONDS = 60.0  # seconds - sliding window size
CPU_CUMULATIVE_THRESHOLD = 15.0  # seconds - minimum cumulative CPU in window to be "active"
# NOTE: Raised from 2.0 to 15.0 to filter out MCP server child process noise.
# When codex runs, it spawns 8+ MCP server children whose collective background CPU
# (~0.003s/check/child × 8 children × 60 checks = ~1.5s/window) was exceeding the old
# threshold of 2.0s, preventing idle timeout from ever firing on stuck processes.

# Per-child noise floor: children whose individual CPU delta is below this threshold
# are excluded from the total CPU calculation. This filters out MCP server event loop
# noise (~0.001-0.005s/check) while still counting tool execution children (>0.05s).
CHILD_CPU_NOISE_FLOOR = 0.02  # seconds - per-child per-check minimum to count

STARTUP_TIMEOUT_SECONDS = 300.0  # seconds - max time to wait for first CPU activity after launch


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

    DENIED_ARGS: ClassVar[frozenset[str]] = frozenset(
        {
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--enable",
            "--print",
            "--output-format",
            "--append-system-prompt",
            "--permission-mode",
            "--model",
            "--disallowedTools",
        }
    )
    _DENIED_ARGS_WITH_VALUE: ClassVar[frozenset[str]] = frozenset(
        {
            "--enable",
            "--output-format",
            "--append-system-prompt",
            "--permission-mode",
            "--model",
            "--disallowedTools",
        }
    )
    _SENSITIVE_HINT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(?i)(token|secret|password|passwd|credential|api[-_]?key|access[-_]?key|private[-_]?key|bearer|auth)"
    )
    _REDACTED_VALUE: ClassVar[str] = "[REDACTED]"
    _CONFIG_OVERRIDE_ARGS: ClassVar[frozenset[str]] = frozenset({"-c", "--config"})
    _PROTECTED_CONFIG_PREFIXES: ClassVar[tuple[str, ...]] = ("mcp_servers.ai-cli-bridge",)

    def __init__(self, client: ResolvedCLIClient):
        self.client = client
        self._parser: BaseParser = get_parser(client.parser)
        self._logger = logging.getLogger(f"clink.runner.{client.name}")
        # Per-child CPU tracking for noise filtering
        # Maps child PID → last observed cumulative CPU time (user + system)
        self._child_cpu_prev: dict[int, float] = {}
        # Tracks the cumulative child CPU time that passed interval filtering.
        # This keeps _get_total_cpu_time() monotonic without re-adding a child's
        # entire historical CPU time when it briefly crosses the noise floor.
        self._filtered_child_cpu_total = 0.0
        self._cpu_tracking_initialized = False

    async def run(
        self,
        *,
        role: ResolvedCLIRole,
        prompt: str,
        system_prompt: str | None = None,
        files: Sequence[str],
        images: Sequence[str],
        extra_args: Sequence[str] = (),
    ) -> AgentOutput:
        # Files and images are already embedded into the prompt by the tool; they are
        # accepted here only to keep parity with SimpleTool callers.
        _ = (files, images)
        # The runner simply executes the configured CLI command for the selected role.
        command = self._build_command(role=role, system_prompt=system_prompt, extra_args=extra_args)
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

        sanitized_command = self._sanitize_args_for_logging(command)

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
            sanitized_command = self._sanitize_args_for_logging(command_with_output_flag)

        self._logger.debug("Executing CLI command: %s", " ".join(sanitized_command))
        if cwd:
            self._logger.debug("Working directory: %s", cwd)

        try:
            # start_new_session is POSIX-only, not supported on Windows
            subprocess_kwargs = {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": cwd,
                "limit": limit,
                "env": env,
            }
            if os.name != "nt":
                subprocess_kwargs["start_new_session"] = True  # Create new process group for easier cleanup

            process = await asyncio.create_subprocess_exec(
                *command_with_output_flag,
                **subprocess_kwargs,
            )
        except FileNotFoundError as exc:
            raise CLIAgentError(f"Executable not found for CLI '{self.client.name}': {exc}") from exc

        # Register the process for cleanup tracking
        if process.pid:
            await register_process(process.pid)

        try:
            try:
                stdout_bytes, stderr_bytes = await self._communicate_with_activity_monitor(
                    process=process,
                    input_data=prompt.encode("utf-8"),
                    idle_timeout=self.client.cpu_idle_timeout_seconds,
                    hard_timeout=self.client.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                # Kill the entire process group, not just the main process
                # This ensures all child processes are cleaned up
                if process.pid:
                    is_windows = os.name == "nt"
                    try:
                        if is_windows:
                            # Windows: use psutil to terminate process tree
                            try:
                                parent = psutil.Process(process.pid)
                                children = parent.children(recursive=True)
                                self._logger.info(
                                    "Terminating process %d and %d children for CLI '%s' (Windows)",
                                    process.pid,
                                    len(children),
                                    self.client.name,
                                )
                                parent.terminate()
                                for child in children:
                                    child.terminate()
                            except psutil.NoSuchProcess:
                                self._logger.debug("Process already terminated for CLI '%s'", self.client.name)
                        else:
                            # POSIX: use process groups
                            self._logger.info(
                                "Sending SIGTERM to process group %d for CLI '%s'",
                                process.pid,
                                self.client.name,
                            )
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)

                        # Wait 2 seconds for graceful shutdown
                        await asyncio.sleep(2)

                        # Check if process is still alive and force kill if needed
                        try:
                            if is_windows:
                                parent = psutil.Process(process.pid)
                                if parent.is_running():
                                    children = parent.children(recursive=True)
                                    self._logger.warning(
                                        "Process %d did not terminate gracefully, force killing (Windows)",
                                        process.pid,
                                    )
                                    parent.kill()
                                    for child in children:
                                        child.kill()
                            else:
                                os.kill(process.pid, 0)  # Check if process exists
                                # Process still alive, force kill
                                self._logger.warning(
                                    "Process group %d did not terminate gracefully, sending SIGKILL",
                                    process.pid,
                                )
                                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except (ProcessLookupError, psutil.NoSuchProcess):
                            # Process already terminated
                            self._logger.debug("Process %d terminated gracefully", process.pid)

                    except (ProcessLookupError, psutil.NoSuchProcess):
                        # Process group already gone
                        self._logger.debug("Process already terminated for CLI '%s'", self.client.name)
                    except Exception as e:
                        self._logger.error(
                            "Failed to kill process for CLI '%s': %s",
                            self.client.name,
                            e,
                        )

                # Wait for process cleanup to complete
                try:
                    await asyncio.wait_for(process.communicate(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._logger.error(
                        "Process cleanup timed out after 5 seconds for CLI '%s'",
                        self.client.name,
                    )

                raise CLIAgentError(
                    f"CLI '{self.client.name}' timed out (no IO activity for {self.client.cpu_idle_timeout_seconds}s or exceeded {self.client.timeout_seconds}s total)",
                    returncode=None,
                ) from exc
        finally:
            # Always unregister the process when done
            if process.pid:
                await unregister_process(process.pid)

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

    def _build_command(self, *, role: ResolvedCLIRole, system_prompt: str | None, extra_args: Sequence[str] = ()) -> list[str]:
        base = list(self.client.executable)
        base.extend(self.client.internal_args)
        base.extend(self.client.config_args)
        base.extend(role.role_args)
        self._extend_with_safe_extra_args(base, extra_args)

        return base

    def _extend_with_safe_extra_args(self, command: list[str], extra_args: Sequence[str]) -> None:
        command.extend(self._filter_denied_extra_args(extra_args))

    def _filter_denied_extra_args(self, extra_args: Sequence[str]) -> list[str]:
        filtered: list[str] = []
        removed: list[str] = []

        index = 0
        while index < len(extra_args):
            arg = extra_args[index]
            arg_name, has_inline_value = self._split_arg_name(arg)
            is_flag = arg.startswith("-")
            is_subcommand_position = index == 0 and not is_flag
            should_check_denylist = is_flag or is_subcommand_position

            if arg_name in self._CONFIG_OVERRIDE_ARGS:
                config_value, consumes_next = self._extract_config_override_value(extra_args, index, arg, has_inline_value)
                if config_value and self._is_protected_config_override(config_value):
                    removed.append(arg)
                    if consumes_next:
                        removed.append(config_value)
                        index += 2
                    else:
                        index += 1
                    continue

            if should_check_denylist and arg_name in self.DENIED_ARGS:
                removed.append(arg)
                if (
                    arg_name in self._DENIED_ARGS_WITH_VALUE
                    and not has_inline_value
                    and index + 1 < len(extra_args)
                    and not extra_args[index + 1].startswith("-")
                ):
                    removed.append(extra_args[index + 1])
                    index += 2
                    continue

                index += 1
                continue

            filtered.append(arg)
            index += 1

        if removed:
            removed_display = " ".join(self._sanitize_args_for_logging(removed))
            self._logger.warning("Removed denied extra_args for CLI '%s': %s", self.client.name, removed_display)

        return filtered

    def _extract_config_override_value(
        self,
        extra_args: Sequence[str],
        index: int,
        arg: str,
        has_inline_value: bool,
    ) -> tuple[str | None, bool]:
        if has_inline_value:
            _, value = arg.split("=", 1)
            return value, False

        if index + 1 >= len(extra_args):
            return None, False

        return extra_args[index + 1], True

    def _is_protected_config_override(self, config_value: str) -> bool:
        key = config_value.split("=", 1)[0].strip()
        return any(key == prefix or key.startswith(f"{prefix}.") for prefix in self._PROTECTED_CONFIG_PREFIXES)

    def _sanitize_args_for_logging(self, args: Sequence[str]) -> list[str]:
        sanitized: list[str] = []
        redact_next_value = False

        for arg in args:
            if redact_next_value:
                sanitized.append(self._REDACTED_VALUE)
                redact_next_value = False
                continue

            if arg.startswith("-") and "=" in arg:
                arg_name, arg_value = arg.split("=", 1)
                if self._looks_sensitive_text(arg_name) or self._looks_sensitive_text(arg_value):
                    sanitized.append(f"{arg_name}={self._REDACTED_VALUE}")
                else:
                    sanitized.append(arg)
                continue

            if arg.startswith("-"):
                sanitized.append(arg)
                if self._looks_sensitive_text(arg):
                    redact_next_value = True
                continue

            if self._looks_sensitive_text(arg):
                sanitized.append(self._REDACTED_VALUE)
            else:
                sanitized.append(arg)

        return sanitized

    def _split_arg_name(self, arg: str) -> tuple[str, bool]:
        if arg.startswith("-") and "=" in arg:
            name, _ = arg.split("=", 1)
            return name, True
        return arg, False

    def _looks_sensitive_text(self, value: str) -> bool:
        return bool(value) and bool(self._SENSITIVE_HINT_PATTERN.search(value))

    def _build_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.client.env)
        return env

    # ------------------------------------------------------------------
    # CPU-based timeout monitoring
    # ------------------------------------------------------------------

    def _get_total_cpu_time(self, pid: int) -> float:
        """Get a monotonic CPU counter for process and filtered child activity.

        Works on macOS/Linux/Windows unlike io_counters() which is Linux-only.

        **Noise filtering**: Each child process is tracked individually. Only
        children whose CPU delta since the last check exceeds CHILD_CPU_NOISE_FLOOR
        are added to this counter. This filters out MCP server event loop noise
        (~0.001-0.005s/check) while correctly counting tool execution children
        that do real work (>0.05s/check) without re-counting their historical CPU.

        The main process is always counted using its real cumulative CPU time.
        Children contribute only their accepted interval delta, accumulated over
        time, so callers can continue to derive `cpu_delta` via subtraction.
        """
        total = 0.0
        current_child_cpu: dict[int, float] = {}

        try:
            proc = psutil.Process(pid)
            # Main process: always counted (no noise filtering)
            try:
                cpu = proc.cpu_times()
                total += cpu.user + cpu.system
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            # Children: apply per-child noise filtering
            try:
                children = proc.children(recursive=True)
                for child in children:
                    try:
                        child_cpu = child.cpu_times()
                        child_total = child_cpu.user + child_cpu.system
                        child_pid = child.pid
                        current_child_cpu[child_pid] = child_total

                        # Compute per-child delta since last check
                        prev = self._child_cpu_prev.get(child_pid)
                        if prev is None:
                            # On the initial baseline sample we record the child's
                            # current cumulative CPU without treating its history
                            # as new activity. For children that appear after the
                            # baseline, their current CPU time is the interval delta.
                            delta = 0.0 if not self._cpu_tracking_initialized else child_total
                        else:
                            delta = max(child_total - prev, 0.0)

                        if delta >= CHILD_CPU_NOISE_FLOOR:
                            self._filtered_child_cpu_total += delta
                        # else: noise-level CPU, skip this child's contribution

                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        pass
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        except psutil.NoSuchProcess:
            pass

        # Update tracking for next check (also prunes dead children)
        self._child_cpu_prev = current_child_cpu
        self._cpu_tracking_initialized = True

        return total + self._filtered_child_cpu_total

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

        **PER-PROCESS TRACKING**: Each process is tracked independently.
        This prevents one stuck process from keeping all processes "alive".

        **DUAL DETECTION**: Uses both single-sample threshold AND sliding window
        cumulative activity to distinguish real work from heartbeat/event loop noise.

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
        # Reset per-child CPU tracking for this new process
        self._child_cpu_prev = {}
        self._filtered_child_cpu_total = 0.0
        self._cpu_tracking_initialized = False
        last_cpu_time = self._get_total_cpu_time(pid)
        last_activity_time = start_time

        # Startup timeout: process must show first CPU activity within configured time
        has_seen_cpu_activity = False

        # Sliding window for cumulative activity detection
        # Each entry is (timestamp, cpu_delta)
        activity_window: list[tuple[float, float]] = []

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

                # Check startup timeout - must show CPU activity within configured time
                if not has_seen_cpu_activity and elapsed >= STARTUP_TIMEOUT_SECONDS:
                    self._logger.warning(
                        "CLI '%s' no CPU activity within %ds of startup, considering stuck at launch",
                        self.client.name,
                        STARTUP_TIMEOUT_SECONDS,
                    )
                    raise asyncio.TimeoutError(f"No CPU activity within {STARTUP_TIMEOUT_SECONDS}s of startup")

                # Check CPU activity for THIS process ONLY
                current_cpu_time = self._get_total_cpu_time(pid)
                cpu_delta = current_cpu_time - last_cpu_time
                # Clamp negative deltas to zero (can occur when subprocess exits or permission denied)
                cpu_delta = max(cpu_delta, 0.0)
                last_cpu_time = current_cpu_time

                # Add to sliding window
                activity_window.append((current_time, cpu_delta))

                # Remove entries outside the window
                window_start = current_time - CPU_ACTIVITY_WINDOW_SECONDS
                activity_window = [(t, d) for t, d in activity_window if t >= window_start]

                # Calculate cumulative CPU activity in window
                cumulative_cpu = sum(d for _, d in activity_window)

                # Determine if process is truly active using DUAL detection:
                # 1. Single large burst (>= CPU_ACTIVITY_THRESHOLD) indicates immediate activity
                # 2. Cumulative activity in window (>= CPU_CUMULATIVE_THRESHOLD) indicates sustained work
                is_active = cpu_delta >= CPU_ACTIVITY_THRESHOLD or cumulative_cpu >= CPU_CUMULATIVE_THRESHOLD

                # For startup detection: any CPU activity (even minor) counts
                # This uses a lower threshold than idle detection to avoid false startup timeouts
                if cpu_delta > 0:
                    has_seen_cpu_activity = True

                if is_active:
                    # Only update last_activity_time when truly active (meets threshold)
                    # This prevents heartbeat/event loop noise (0.1-0.2s) from keeping stuck processes alive
                    last_activity_time = current_time
                    self._logger.debug(
                        "CLI '%s' (PID %d) CPU activity: delta=+%.3fs, window_cumulative=%.3fs (elapsed: %.1fs)",
                        self.client.name,
                        pid,
                        cpu_delta,
                        cumulative_cpu,
                        elapsed,
                    )
                elif cpu_delta > 0:
                    # Log small activity that doesn't meet threshold (for debugging)
                    # NOTE: Does NOT update last_activity_time - minor CPU noise should not reset idle timeout
                    self._logger.debug(
                        "CLI '%s' (PID %d) minor CPU: delta=+%.3fs, window_cumulative=%.3fs (not counted as active)",
                        self.client.name,
                        pid,
                        cpu_delta,
                        cumulative_cpu,
                    )

                # Check idle timeout using THIS PROCESS's activity only
                idle_time = current_time - last_activity_time
                if idle_time >= idle_timeout:
                    self._logger.warning(
                        "CLI '%s' (PID %d) no significant CPU activity for %.1fs (window_cumulative=%.3fs), considering stuck",
                        self.client.name,
                        pid,
                        idle_time,
                        cumulative_cpu,
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
