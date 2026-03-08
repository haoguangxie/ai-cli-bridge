# ai-cli-bridge

AI CLI Bridge is an MCP server focused on one job: bridging MCP requests to external AI CLI clients through `clink`.

## Available Tools

1. `clink` - Forward prompts/files/images to a configured CLI client and return its response.
2. `version` - Show server version/runtime metadata.

## Positioning

This repository is a clink bridge implementation. It does not implement local workflow tools in `server.py`; tool routing is limited to `clink` and `version`.

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 20.19.5 (required for Codex CLI integration)

If you use `nvm`:

```bash
nvm install
nvm use
```

### Installation

```bash
git clone https://github.com/your-fork/ai-cli-bridge.git
cd ai-cli-bridge
pip install -r requirements.txt
```

### Configuration

1. Copy environment template:
```bash
cp .env.example .env
```
2. Configure CLI clients via JSON files in `conf/cli_clients/`.
3. Ensure those external CLI clients are already installed and authenticated in your shell environment.

### Run the Server

```bash
python server.py
```

Or use setup helper:

```bash
./run-server.sh
```

## Claude Desktop Integration

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "ai-cli-bridge": {
      "command": "python",
      "args": ["/path/to/ai-cli-bridge/server.py"]
    }
  }
}
```

## How Clink Works

```text
MCP Client -> ai-cli-bridge (clink) -> External CLI -> Model/Agent Runtime
```

## CLI Client Configuration

Built-in configs live in `conf/cli_clients/`.

- `claude.json`
- `codex.json`

Each client config defines:
- executable command and args
- role-to-prompt mapping
- timeout/env/runtime options

Optional override: set `CLI_CLIENTS_CONFIG_PATH` in `.env` to use a custom config file or directory.

## Testing

```bash
# Unit tests
python -m pytest tests/ -v -m "not integration"

# Integration tests
python -m pytest tests/ -v -m "integration"
```

## Project Structure

```text
ai-cli-bridge/
├── server.py
├── config.py
├── tools/
│   ├── clink.py
│   └── version.py
├── clink/
│   ├── agents/
│   ├── parsers/
│   └── registry.py
├── conf/
│   └── cli_clients/
├── systemprompts/
│   └── clink/
└── tests/
```

## License

Same as the upstream PAL MCP project license.
