# Claude Development Guide for AI CLI Bridge

This guide documents the current clink-only development workflow for this repository.

## Available MCP Tools

This server exposes only two MCP tools:
- `clink`: forwards requests to configured external AI CLI clients
- `version`: returns server and environment metadata

## Quick Reference Commands

### Code Quality Checks

Before committing changes, run the full quality gate:

```bash
# Activate virtual environment first
source .pal_venv/bin/activate

# Run lint + format + unit tests (non-integration)
./code_quality_checks.sh
```

This script runs:
- Ruff linting with auto-fix
- Black formatting
- isort import sorting
- Pytest unit suite (`-m "not integration"`)

### Server Management

#### Setup/Update the Server
```bash
# Setup environment, dependencies, and local MCP config helpers
./run-server.sh
```

#### Run Server Directly
```bash
python server.py
```

#### View Logs
```bash
# Follow logs in real-time
./run-server.sh -f

# Or manually
tail -f logs/mcp_server.log
```

### Log Management

#### View Server Logs
```bash
# View last 500 lines
tail -n 500 logs/mcp_server.log

# Follow in real-time
tail -f logs/mcp_server.log

# Search for errors
grep "ERROR" logs/mcp_server.log
```

#### Monitor Tool Executions Only
```bash
# View recent tool activity
tail -n 100 logs/mcp_activity.log

# Follow tool activity in real-time
tail -f logs/mcp_activity.log

# Filter key events
tail -f logs/mcp_activity.log | grep -E "(TOOL_CALL|TOOL_COMPLETED|ERROR|WARNING)"
```

#### Available Log Files
```bash
# Main server log (rotation enabled)
tail -f logs/mcp_server.log

# Tool activity log (rotation enabled)
tail -f logs/mcp_activity.log
```

### Testing

Use the `tests/` pytest suite.

**IMPORTANT**: Restart your Claude session after server code changes so the updated MCP server is reloaded.

#### Run Unit Tests Only
```bash
# Run all non-integration tests
python -m pytest tests/ -v -m "not integration"

# Run a specific test file
python -m pytest tests/test_clink_tool.py -v

# Run one test function
python -m pytest tests/test_clink_tool.py::test_clink_tool_execute -v

# Run tests with coverage
python -m pytest tests/ --cov=. --cov-report=html -m "not integration"
```

#### Run Integration Tests
```bash
# Run all integration tests
python -m pytest tests/ -v -m "integration"

# Run integration test module
python -m pytest tests/test_clink_integration.py -v -m integration
```

Note: Integration tests depend on available external CLI clients and their authentication state.

### Development Workflow

#### Before Making Changes
1. Activate virtual environment: `source .pal_venv/bin/activate`
2. Run baseline checks: `./code_quality_checks.sh`
3. Confirm server health: `tail -n 50 logs/mcp_server.log`

#### After Making Changes
1. Re-run checks: `./code_quality_checks.sh`
2. Run affected test modules (for example `tests/test_clink_tool.py`)
3. Run integration tests if clink runtime behavior changed: `python -m pytest tests/ -v -m "integration"`
4. Check logs for regressions: `tail -n 100 logs/mcp_server.log`
5. Restart Claude session to load updated server code

#### Before Committing/PR
1. Final check run: `./code_quality_checks.sh`
2. Re-run relevant clink tests
3. Run integration tests when changes affect process execution/parsing
4. Verify all required checks pass

### Common Troubleshooting

#### Server Issues
```bash
# Re-run environment/bootstrap setup
./run-server.sh

# View recent errors
grep "ERROR" logs/mcp_server.log | tail -20

# Check active Python path
which python
# Expected in repo venv: .../ai-cli-bridge/.pal_venv/bin/python
```

#### Test Failures
```bash
# Re-run failing tests with verbose output
python -m pytest tests/ -v -k clink

# Stop on first failure for faster isolation
python -m pytest tests/ -v -x

# Show live server logs while reproducing
tail -f logs/mcp_server.log

# Increase server log verbosity for debugging
LOG_LEVEL=DEBUG python server.py
```

#### Linting Issues
```bash
# Auto-fix most linting issues
ruff check . --fix
black .
isort .

# Check without modifying files
ruff check .
black --check .
isort --check-only .
```

### File Structure Context

- `./code_quality_checks.sh` - lint/format/test quality gate
- `./run-server.sh` - setup and local MCP configuration helper
- `./server.py` - MCP server entrypoint (clink + version only)
- `./tests/` - pytest suite for clink parsers/agents/tool/integration
- `./tools/` - MCP tool implementations
- `./clink/` - CLI bridge runtime and registry
- `./systemprompts/` - role prompt definitions
- `./logs/` - server log files

### Environment Requirements

- Python 3.9+ and virtual environment
- Dependencies installed from `requirements.txt`
- At least one CLI client configured in `conf/cli_clients/`

Always run `./code_quality_checks.sh` before creating or updating a PR.
