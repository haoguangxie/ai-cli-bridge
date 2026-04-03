"""
AI CLI Bridge MCP server.

This server exposes only two tools:
- clink: forward requests to configured external AI CLIs
- version: show server/system version info
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from mcp.server import Server  # noqa: E402
from mcp.server.models import InitializationOptions  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import (  # noqa: E402
    ServerCapabilities,
    TextContent,
    Tool,
    ToolAnnotations,
    ToolsCapability,
)

from config import __version__  # noqa: E402
from tools import CLinkTool, VersionTool  # noqa: E402
from utils.env import get_env  # noqa: E402

log_level = (get_env("LOG_LEVEL", "DEBUG") or "DEBUG").upper()
SERVER_IDLE_TIMEOUT_SECONDS = float(get_env("MCP_SERVER_IDLE_TIMEOUT_SECONDS", "1800") or 1800)
MIN_IDLE_POLL_SECONDS = 5.0
MAX_IDLE_POLL_SECONDS = 30.0


class LocalTimeFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """Use local timezone in log timestamps."""
        ct = self.converter(record.created)
        if datefmt:
            return time.strftime(datefmt, ct)
        t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
        return f"{t},{record.msecs:03.0f}"


def configure_logging() -> None:
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(getattr(logging, log_level, logging.INFO))
    stderr_handler.setFormatter(LocalTimeFormatter(log_format))
    root_logger.addHandler(stderr_handler)

    try:
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)

        file_handler = RotatingFileHandler(
            log_dir / "mcp_server.log",
            maxBytes=20 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(getattr(logging, log_level, logging.INFO))
        file_handler.setFormatter(LocalTimeFormatter(log_format))
        root_logger.addHandler(file_handler)

        mcp_logger = logging.getLogger("mcp_activity")
        mcp_file_handler = RotatingFileHandler(
            log_dir / "mcp_activity.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        mcp_file_handler.setLevel(logging.INFO)
        mcp_file_handler.setFormatter(LocalTimeFormatter("%(asctime)s - %(message)s"))
        mcp_logger.addHandler(mcp_file_handler)
        mcp_logger.setLevel(logging.INFO)
        mcp_logger.propagate = True

        logging.info(f"Logging to: {log_dir / 'mcp_server.log'}")
        logging.info(f"Process PID: {os.getpid()}")
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"Warning: Could not set up file logging: {exc}", file=sys.stderr)


configure_logging()
logger = logging.getLogger(__name__)

server: Server = Server("ai-cli-bridge")

_shutdown_requested = False
_last_activity_monotonic = time.monotonic()
_inflight_requests = 0

TOOLS = {
    "clink": CLinkTool(),
    "version": VersionTool(),
}


def _mark_activity(now: float | None = None) -> float:
    """Record the last time the MCP server did useful work."""
    global _last_activity_monotonic
    _last_activity_monotonic = time.monotonic() if now is None else now
    return _last_activity_monotonic


def _begin_request(now: float | None = None) -> None:
    """Track request lifecycle so idle shutdown never kills active tool work."""
    global _inflight_requests
    _inflight_requests += 1
    _mark_activity(now)


def _end_request(now: float | None = None) -> None:
    """Mark request completion and refresh activity time for post-call idle windows."""
    global _inflight_requests
    if _inflight_requests > 0:
        _inflight_requests -= 1
    else:  # pragma: no cover - defensive guard for mismatched lifecycle bookkeeping
        logger.warning("Request counter underflow while ending MCP request")
    _mark_activity(now)


def _idle_poll_seconds(idle_timeout_seconds: float) -> float:
    return max(MIN_IDLE_POLL_SECONDS, min(MAX_IDLE_POLL_SECONDS, idle_timeout_seconds / 6.0))


def _should_trigger_idle_shutdown(
    now: float,
    *,
    last_activity_monotonic: float,
    inflight_requests: int,
    idle_timeout_seconds: float,
) -> bool:
    if idle_timeout_seconds <= 0:
        return False
    if inflight_requests > 0:
        return False
    return now - last_activity_monotonic >= idle_timeout_seconds


def _trigger_idle_shutdown() -> None:
    """Reuse the existing signal-based shutdown path so cleanup stays centralized."""
    signum = signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.SIGINT
    signal.raise_signal(signum)


async def _monitor_server_idle(idle_timeout_seconds: float, *, poll_interval: float | None = None) -> None:
    """Exit stale MCP server processes after a quiet period with no in-flight requests."""
    if idle_timeout_seconds <= 0:
        logger.info("Server idle reaper disabled (MCP_SERVER_IDLE_TIMEOUT_SECONDS <= 0)")
        return

    poll_seconds = poll_interval if poll_interval is not None else _idle_poll_seconds(idle_timeout_seconds)
    logger.info(
        "Server idle reaper enabled: timeout=%ss poll=%ss",
        idle_timeout_seconds,
        poll_seconds,
    )

    while not _shutdown_requested:
        await asyncio.sleep(poll_seconds)
        now = time.monotonic()
        if _should_trigger_idle_shutdown(
            now,
            last_activity_monotonic=_last_activity_monotonic,
            inflight_requests=_inflight_requests,
            idle_timeout_seconds=idle_timeout_seconds,
        ):
            idle_for = now - _last_activity_monotonic
            logger.info(
                "Server idle timeout reached after %.1fs without requests; initiating shutdown",
                idle_for,
            )
            _trigger_idle_shutdown()
            return


def setup_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Install SIGTERM/SIGINT handlers for graceful shutdown."""

    def signal_handler_sync(signum: int, frame: Any) -> None:  # noqa: ARG001
        nonlocal_loop_msg = f"Received signal {signum}, initiating shutdown..."
        _request_shutdown(nonlocal_loop_msg)

    def signal_handler_async() -> None:
        _request_shutdown("Received shutdown signal, initiating cleanup...")

    if os.name != "nt":
        loop.add_signal_handler(signal.SIGTERM, signal_handler_async)
        loop.add_signal_handler(signal.SIGINT, signal_handler_async)
        logger.info("Signal handlers registered for SIGTERM and SIGINT (POSIX)")
    else:
        signal.signal(signal.SIGTERM, signal_handler_sync)
        signal.signal(signal.SIGINT, signal_handler_sync)
        logger.info("Signal handlers registered for SIGTERM and SIGINT (Windows)")


def _request_shutdown(message: str) -> None:
    global _shutdown_requested
    if _shutdown_requested:
        logger.warning("Shutdown already in progress, ignoring signal")
        return

    _shutdown_requested = True
    logger.info(message)
    raise KeyboardInterrupt


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Return MCP tool metadata for clink and version."""
    _mark_activity()
    logger.debug("MCP client requested tool list")

    # Best-effort MCP client logging during handshake.
    try:
        from utils.client_info import format_client_info, get_client_info_from_context

        client_info = get_client_info_from_context(server)
        if client_info:
            formatted = format_client_info(client_info)
            logger.info(f"MCP Client Connected: {formatted}")
            try:
                mcp_activity_logger = logging.getLogger("mcp_activity")
                friendly_name = client_info.get("friendly_name", "CLI Agent")
                raw_name = client_info.get("name", "Unknown")
                version = client_info.get("version", "Unknown")
                mcp_activity_logger.info(f"MCP_CLIENT_INFO: {friendly_name} (raw={raw_name} v{version})")
            except Exception:
                pass
    except Exception as exc:
        logger.debug(f"Could not log client info during list_tools: {exc}")

    tools: list[Tool] = []
    for tool in TOOLS.values():
        annotations = tool.get_annotations()
        tool_annotations = ToolAnnotations(**annotations) if annotations else None
        tools.append(
            Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.get_input_schema(),
                annotations=tool_annotations,
            )
        )

    logger.debug(f"Returning {len(tools)} tools to MCP client")
    return tools


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    """Route tool requests to clink/version handlers."""
    _begin_request()
    args = arguments or {}
    logger.info(f"MCP tool call: {name}")
    logger.debug(f"MCP tool arguments: {list(args.keys())}")

    try:
        try:
            mcp_activity_logger = logging.getLogger("mcp_activity")
            mcp_activity_logger.info(f"TOOL_CALL: {name} with {len(args)} arguments")
        except Exception:
            pass

        tool = TOOLS.get(name)
        if tool is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        logger.info(f"Executing tool '{name}' with {len(args)} parameter(s)")
        result = await tool.execute(args)
        logger.info(f"Tool '{name}' execution completed")
        try:
            mcp_activity_logger = logging.getLogger("mcp_activity")
            mcp_activity_logger.info(f"TOOL_COMPLETED: {name}")
        except Exception:
            pass
        return result
    finally:
        _end_request()


async def main() -> None:
    """Start MCP server over stdio transport."""
    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    logger.info("AI CLI Bridge starting up...")
    logger.info(f"Log level: {log_level}")
    logger.info(f"Available tools: {list(TOOLS.keys())}")
    logger.info("Server ready - waiting for tool requests...")
    logger.info("Clink-only mode: Forwarding requests to external AI CLIs")

    idle_watchdog = asyncio.create_task(_monitor_server_idle(SERVER_IDLE_TIMEOUT_SECONDS))
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="ai-cli-bridge",
                    server_version=__version__,
                    instructions="Use the clink tool to forward requests to configured AI CLIs.",
                    capabilities=ServerCapabilities(
                        tools=ToolsCapability(),
                    ),
                ),
            )
    finally:
        idle_watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await idle_watchdog


def run() -> None:
    """Console script entry point for ai-cli-bridge."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, cleaning up processes...")
        from clink.agents.base import cleanup_all_processes

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(cleanup_all_processes())
        finally:
            loop.close()
        logger.info("Shutdown complete")
    except Exception as exc:
        logger.error(f"Unexpected error during shutdown: {exc}")
        raise


if __name__ == "__main__":
    run()
