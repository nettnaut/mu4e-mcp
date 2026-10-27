# mu4e-mcp

[![CI](https://github.com/nettnaut/mu4e-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/nettnaut/mu4e-mcp/actions/workflows/ci.yml)

A minimal MCP server that exposes local `mu` email search to Claude Code. Written in pure Python stdlib — no dependencies beyond `mu` itself.

## What it does

Gives Claude two tools:

| Tool | Description |
|------|-------------|
| `search_email` | Run any `mu find` query and get matching messages as JSON |
| `get_email` | Fetch the full body and headers of a message by file path (must be inside the maildir) |

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
- `get_email` only reads files inside the maildir (`MU_MCP_MAILDIR`, default
  `~/.maildir`) after resolving symlinks and `..`; anything else is refused
- Query words are passed to `mu find` after `--`, so a query can never smuggle
  in `mu` options (e.g. `--exec`)

## Remote access (HTTP mode)

To reach your mail from a client that isn't on this machine — e.g. a claude.ai
custom connector — run the server in HTTP mode behind an authenticating proxy:

```sh
python3 mu4e-mcp.py --http 127.0.0.1:8766
# MCP endpoint: http://127.0.0.1:8766/mcp
```

- **Transport:** stateless MCP *Streamable HTTP* — `POST /mcp` with a JSON-RPC
  message (or batch), JSON response; notifications get `202`. No server-initiated
  stream, so `GET` is `405`. Requests are handled one at a time.
- **Bind to loopback** and put real authentication (e.g. an OAuth proxy) in
  front. The server warns on a non-loopback bind.
- **`MU_MCP_HTTP_TOKEN`** — optional: require `Authorization: Bearer <token>`
  on every request, as a second lock behind the proxy.
- **`MU_MCP_ALLOWED_ORIGINS`** — comma-separated; requests carrying any other
  `Origin` header get `403` (DNS-rebinding guard). Requests without `Origin`
  (server-to-server) are allowed.
- In HTTP mode every `tools/call` is logged to stderr (tool, argument *names*,
  ok, result size — never argument values or results).

## Tests

```sh
python3 tests/test_mu4e_mcp.py   # no mu needed: JSON-RPC, hardening, HTTP mode
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[GPL-3.0-or-later](LICENSE) © 2026 Kjetil Rohde Jakobsen. Contributions welcome.
