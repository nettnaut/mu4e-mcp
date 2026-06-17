# Contributing to mu4e-mcp

Thanks for your interest! mu4e-mcp is a tiny, dependency-free MCP server that
exposes local [`mu`](https://www.djcbsoftware.nl/code/mu/) email search to an
MCP client (e.g. Claude). Contributions — bug reports, new tools, docs — are
welcome.

## How it works

`mu4e-mcp.py` is a single, **stdlib-only** Python 3 file implementing a
hand-rolled JSON-RPC **stdio** server. It shells out to the `mu` CLI:

- `search_email` → `mu find --format json --maxnum=N <query>` (parsed straight
  into JSON), returning slim per-message summaries incl. a `mu4e:msgid:` link
- `get_email` → reads a message file with Python's `email` module and returns
  headers + a body capped at 8000 chars

There is no build step and nothing to install beyond `mu` itself.

## Adding or changing a tool

1. Implement the behaviour as a Python function.
2. Declare it in the `tools/list` response (name + `inputSchema`) and handle it
   in the `tools/call` dispatch.
3. Update the tool table and query examples in `README.md`.

### Guidelines

- **Stdlib only** — please don't add runtime dependencies; the appeal of this
  server is that it's a single file.
- **Read-only by default.** The server searches and reads mail; it does not
  modify the maildir. Keep it that way unless a change is discussed in an issue
  first.
- **Bound the output.** Bodies are capped (8000 chars) and results limited
  (default 20) to avoid flooding the client's context — preserve those caps.
- **Never interpolate raw input into a shell.** Pass `mu` arguments as a list to
  `subprocess` (no `shell=True`), so query strings can't break out.

## Testing

There's no formal suite yet (contributions welcome!). At minimum, before a PR:

```sh
python3 -m py_compile mu4e-mcp.py          # syntax
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mu4e-mcp.py
```

and try a real `search_email` / `get_email` round-trip against a `mu`-indexed
maildir.

## Submitting

1. Fork, branch, commit (focused commits; `feat:` / `fix:` / `docs:` subjects
   appreciated).
2. Verify as above.
3. Open a PR describing the change and how you tested it.

## License

By contributing, you agree your contributions are licensed under the project's
**GPL-3.0-or-later** license (see `LICENSE`).
