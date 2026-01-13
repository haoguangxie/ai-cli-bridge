# zen-mcp-server (Clink-Only Mode)

> **Notice:** This is a minimal clink-only fork. For the full PAL MCP experience with all workflow tools, see the [main repository](https://github.com/BeehiveInnovations/pal-mcp-server).

## What is This?

This is a streamlined version of PAL MCP Server that provides **only the clink tool** - a bridge that forwards MCP requests to external AI CLI agents.

### Available Tools

1. **`clink`** - Forward requests to configured AI CLIs (Gemini CLI, Qwen CLI, etc.)
2. **`version`** - Display server version and system information

## Why Clink-Only Mode?

- **No API Keys Required** - Server starts without any AI provider configuration
- **Minimal Dependencies** - No provider abstraction layers or model management
- **CLI-to-CLI Bridge** - Leverage external CLI capabilities through MCP protocol
- **Lightweight** - ~70% smaller codebase compared to full PAL MCP

## Quick Start

### Installation

```bash
# Clone this repository
git clone https://github.com/your-fork/zen-mcp-server.git
cd zen-mcp-server

# Install dependencies
pip install -r requirements.txt
```

### Configuration

1. Configure your external CLI clients in `conf/cli_clients/` directory
2. No API keys needed for the server itself
3. External CLIs handle their own authentication

### Running the Server

```bash
# Start the MCP server
python server.py
```

### Using with Claude Desktop

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "zen-mcp": {
      "command": "python",
      "args": ["/path/to/zen-mcp-server/server.py"]
    }
  }
}
```

## How Clink Works

Clink acts as a bridge between MCP protocol and external CLI agents:

```
Claude Desktop → MCP Protocol → zen-mcp-server (clink) → External CLI → AI Model
                                                                ↓
                                                           Response
```

**Example Usage:**

```bash
# Forward to Gemini CLI
Use clink with gemini to analyze this codebase architecture

# Forward to Codex CLI with code review role
Use clink with codex codereviewer to audit authentication module

# Chain conversations across tools
Use clink with gemini planner to create a refactoring plan
Continue with clink codex to implement the changes
```

## Configuration Files

### CLI Client Configuration

Edit `conf/cli_clients/` to configure your external CLIs:

- `gemini.toml` - Gemini CLI configuration
- `claude.toml` - Claude Code configuration
- `codex.toml` - OpenAI Codex CLI configuration

Each configuration specifies:
- CLI executable path
- Available roles (default, planner, codereviewer)
- System prompts for each role

## Project Structure

```
zen-mcp-server/
├── server.py              # Main MCP server (clink-only)
├── config.py              # Minimal configuration
├── tools/
│   ├── clink.py          # Clink tool implementation
│   ├── version.py        # Version tool
│   ├── models.py         # Shared type definitions
│   ├── shared/           # Base tool framework
│   └── simple/           # Simple tool patterns
├── clink/                # Clink agent implementation
│   ├── agents/          # CLI agent runners
│   ├── models.py        # Clink-specific models
│   └── registry.py      # CLI client registry
├── systemprompts/
│   └── clink/           # Role-specific prompts
└── conf/
    └── cli_clients/     # CLI client configurations
```

## What's Missing (vs Full PAL MCP)

This clink-only fork **does not include**:

- ❌ Direct AI provider integration (OpenAI, Gemini, Anthropic, etc.)
- ❌ Workflow tools (analyze, codereview, debug, planner, etc.)
- ❌ Model selection and provider abstraction
- ❌ Conversation continuation across local tools
- ❌ Auto mode and model fallback logic

**For these features**, use the [full PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server).

## Testing

Minimal test suite for clink functionality:

```bash
# Run clink-specific tests
pytest tests/test_clink*.py -v
```

## Contributing

This is a minimal fork. For feature development, please contribute to the [main PAL MCP repository](https://github.com/BeehiveInnovations/pal-mcp-server).

Bug fixes specific to clink-only mode are welcome via pull requests.

## License

Same as the main PAL MCP Server project.

## Credits

This clink-only mode is derived from [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server).

Original authors and contributors deserve all credit for the architecture and clink implementation.
