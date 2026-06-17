# mu4e-mcp

[![CI](https://github.com/nettnaut/mu4e-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/nettnaut/mu4e-mcp/actions/workflows/ci.yml)

A minimal MCP server that exposes local `mu` email search to Claude Code. Written in pure Python stdlib — no dependencies beyond `mu` itself.

## What it does

Gives Claude two tools:

| Tool | Description |
|------|-------------|
| `search_email` | Run any `mu find` query and get matching messages as JSON |
| `get_email` | Fetch the full body and headers of a message by file path |

Example queries you can ask Claude:
- "Search for unread emails in my work inbox"
- "Find emails from alice@example.com in the last 7 days"
- "Show me the body of that email"

For a fuller, plain-language guide to *asking* Claude to search and read your
mail (with example prompts), see **[USAGE.md](USAGE.md)**.

## Requirements

- Python 3.x (stdlib only)
- [`mu`](https://www.djcbsoftware.nl/code/mu/) installed and indexed (`mu init && mu index`)
- Claude Code

## Installation

### 1. Place the server script

```bash
mkdir -p ~/.local/mcp
cp mu4e-mcp.py ~/.local/mcp/mu4e-mcp.py
chmod +x ~/.local/mcp/mu4e-mcp.py
```

### 2. Register with Claude Code

Add to `~/.claude/settings.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "mu4e": {
      "command": "python3",
      "args": ["/home/<your-username>/.local/mcp/mu4e-mcp.py"]
    }
  }
}
```

Replace `<your-username>` with your actual username, or use an absolute path.

### 3. Restart Claude Code

The MCP server is picked up on startup. After restarting, the `search_email` and `get_email` tools will be available.

## Query syntax

`search_email` accepts any `mu find` query string. Examples:

| Query | Meaning |
|-------|---------|
| `flag:unread AND NOT flag:trashed` | All unread, non-trashed mail |
| `from:alice subject:invoice` | From alice, subject contains "invoice" |
| `maildir:/work/INBOX date:7d..now` | Work inbox, last 7 days |
| `flag:unread maildir:/personal/INBOX` | Unread personal mail |

See the [mu-find man page](https://www.djcbsoftware.nl/code/mu/mu4e/Searching.html) for full query syntax.

## Notes

- Email bodies are capped at 8000 characters to avoid flooding context
- The server returns up to 20 results by default (configurable per query)
- Requires `mu` to already be indexed — run `mu index` if results are stale

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[GPL-3.0-or-later](LICENSE) © 2026 Kjetil Rohde Jakobsen. Contributions welcome.
