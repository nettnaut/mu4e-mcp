#!/usr/bin/env python3
# Copyright (C) 2026 Kjetil Rohde Jakobsen
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software under the GNU General Public License v3
# or later; see the LICENSE file or <https://www.gnu.org/licenses/>.
"""Minimal MCP server exposing mu email search to Claude.

Transports: stdio (default) or Streamable HTTP (`--http HOST:PORT`), both
served by the same `handle`. Read-only: search and read mail, nothing else.
"""

import argparse
import hmac
import ipaddress
import json
import shlex
import subprocess
import sys
import email
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Separate mu database so we don't conflict with mu4e's mu server write lock.
MU_HOME = Path(os.environ.get("MU_MCP_HOME", Path.home() / ".cache" / "mu-mcp"))
MU_MAILDIR = os.environ.get("MU_MCP_MAILDIR", str(Path.home() / ".maildir"))
MU_ADDRESSES = os.environ.get(
    "MU_MCP_ADDRESSES",
    "kjetil@nettnaut.com,kjetil.jakobsen@shortcut.io,nettnaut@gmail.com",
)

# Newest first; `initialize` echoes the client's version when it is listed here.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# HTTP mode. Meant to sit behind an authenticating proxy on loopback; the
# optional static bearer token is a second lock, not the primary auth.
HTTP_PATH = "/mcp"
HTTP_TOKEN = os.environ.get("MU_MCP_HTTP_TOKEN") or None
ALLOWED_ORIGINS = {o.strip() for o in os.environ.get("MU_MCP_ALLOWED_ORIGINS", "").split(",")
                   if o.strip()}
MAX_BODY = 1 << 20
AUDIT = False  # log every tools/call to stderr; on in HTTP mode


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def lisp_time_to_iso(t):
    """Convert mu's Lisp time [HIGH, LOW, USEC] to ISO 8601 string."""
    if isinstance(t, list) and len(t) >= 2:
        ts = t[0] * 65536 + t[1]
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return None


def _ensure_mu_db():
    """Initialise the MCP-private mu database if it doesn't exist yet."""
    db_path = MU_HOME / "xapian"
    if not db_path.exists():
        addr_args = []
        for a in MU_ADDRESSES.split(","):
            a = a.strip()
            if a:
                addr_args += ["--my-address", a]
        subprocess.run(
            ["mu", "init", f"--muhome={MU_HOME}", f"--maildir={MU_MAILDIR}"]
            + addr_args,
            capture_output=True, text=True,
        )


def _reindex():
    """Full re-index to pick up new/changed/deleted mail."""
    _ensure_mu_db()
    subprocess.run(
        ["mu", "index", f"--muhome={MU_HOME}"],
        capture_output=True, text=True,
    )


def mu_find(query, max_results=20):
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ValueError("max_results must be an integer")
    _reindex()
    # `--` ends mu's options: without it a query word like `--exec=CMD` would
    # be parsed as an option and run CMD on every matching message.
    cmd = ["mu", "find", f"--muhome={MU_HOME}",
           "--format", "json", f"--maxnum={max_results}",
           "--sortfield=date", "--reverse", "--"] + shlex.split(query)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 2, 4):  # 2 or 4 = no results found
        raise RuntimeError(result.stderr.strip())
    if not result.stdout.strip():
        return []
    # mu emits some header values (e.g. folded/multi-line Subjects) with raw
    # control characters, which strict JSON rejects. strict=False permits
    # control chars inside strings so one bad message can't kill the whole result.
    return json.loads(result.stdout, strict=False)


def _confine_to_maildir(path):
    """Resolve PATH (symlinks, `..`) and refuse anything outside MU_MAILDIR."""
    root = Path(MU_MAILDIR).resolve()
    real = Path(path).resolve()
    if real != root and root not in real.parents:
        raise PermissionError("path is outside the maildir")
    if not real.is_file():
        raise FileNotFoundError("no such message file")
    return real


def mu_view(path):
    """Read and parse a raw email file (must live inside MU_MAILDIR)."""
    with open(_confine_to_maildir(path), "rb") as f:
        msg = email.message_from_bytes(f.read())

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(errors="replace")
                break
    else:
        body = msg.get_payload(decode=True).decode(errors="replace")

    raw_msgid = msg.get("Message-ID", "")
    message_id = raw_msgid.strip().strip("<>")

    return {
        "subject": msg.get("Subject", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "date": msg.get("Date", ""),
        "message_id": message_id,
        "mu4e_link": f"[[mu4e:msgid:{message_id}][{msg.get('Subject', '')}]]" if message_id else "",
        "body": body[:8000],  # cap to avoid huge context
    }


TOOLS = [
    {
        "name": "search_email",
        "description": (
            "Search emails using mu find query syntax. "
            "Examples: 'from:alice subject:invoice', 'maildir:/work/INBOX date:7d..now', "
            "'flag:unread AND NOT flag:trashed'. "
            "Returns a list of matching messages with metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "mu find query string",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 20)",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_email",
        "description": "Fetch the full body and headers of an email by its file path (from search_email results).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute file path to the email (the :path field from search_email)",
                },
            },
            "required": ["path"],
        },
    },
]


def _slim(m):
    raw_msgid = m.get(":message-id", "") or ""
    message_id = raw_msgid.strip().strip("<>")
    subject = m.get(":subject", "") or ""
    return {
        "path": m.get(":path"),
        "subject": subject,
        "from": m.get(":from"),
        "to": m.get(":to"),
        "date": lisp_time_to_iso(m.get(":date")),
        "maildir": m.get(":maildir"),
        "flags": m.get(":flags"),
        "message_id": message_id,
        "mu4e_link": f"[[mu4e:msgid:{message_id}][{subject}]]" if message_id else "",
    }


def call_tool(name, args):
    if name == "search_email":
        return [_slim(m) for m in mu_find(args["query"], args.get("max_results", 20))]
    if name == "get_email":
        return mu_view(args["path"])
    raise KeyError(name)


def _audit(name, args, ok, size):
    if AUDIT:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        keys = sorted(args) if isinstance(args, dict) else []
        sys.stderr.write(f"mu4e-mcp audit: {ts} tool={name} args={keys} "
                         f"ok={ok} bytes={size}\n")


def handle(request):
    """Answer one JSON-RPC message: the response dict, or None for a notification."""
    method = request.get("method")
    rid = request.get("id")

    def result(r):
        return {"jsonrpc": "2.0", "id": rid, "result": r}

    if method == "initialize":
        asked = (request.get("params") or {}).get("protocolVersion")
        return result({
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mu4e-mcp", "version": "1.2.0"},
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            _audit(name, args, False, 0)
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        try:
            content = json.dumps(call_tool(name, args), indent=2)
        except Exception as e:
            _audit(name, args, False, 0)
            return result({"content": [{"type": "text", "text": f"Error: {e}"}],
                           "isError": True})
        _audit(name, args, True, len(content))
        return result({"content": [{"type": "text", "text": content}]})
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


# --------------------------------------------------------------------------- #
# Streamable HTTP (stateless: POST in, JSON out; no server-initiated stream)
# --------------------------------------------------------------------------- #
class _MCPHandler(BaseHTTPRequestHandler):
    server_version = "mu4e-mcp"
    timeout = 30  # the server is single-threaded: don't let one slow client stall it

    def log_message(self, fmt, *a):
        sys.stderr.write("mu4e-mcp http: " + fmt % a + "\n")

    def _reply(self, code, body=None, headers=()):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _admit(self):
        """Path, Origin and token checks shared by every method. False = replied."""
        if self.path.split("?", 1)[0] != HTTP_PATH:
            self._reply(404)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            self._reply(403)
            return False
        if HTTP_TOKEN:
            got = self.headers.get("Authorization", "")
            if not hmac.compare_digest(got.encode(), f"Bearer {HTTP_TOKEN}".encode()):
                self._reply(401, headers=[("WWW-Authenticate", "Bearer")])
                return False
        return True

    def do_GET(self):
        if self._admit():
            self._reply(405, headers=[("Allow", "POST")])

    do_DELETE = do_GET

    def do_POST(self):
        if not self._admit():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            self._reply(413)
            return
        try:
            msg = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}})
            return
        if isinstance(msg, list):  # 2025-03-26 batches
            out = [r for r in (handle(m) for m in msg if isinstance(m, dict)) if r]
        elif isinstance(msg, dict):
            out = handle(msg)
        else:
            out = {"jsonrpc": "2.0", "id": None,
                   "error": {"code": -32600, "message": "Invalid Request"}}
        if out:
            self._reply(200, out)
        else:
            self._reply(202)


def make_http_server(host, port):
    # Single-threaded on purpose: one mu index/find at a time.
    return HTTPServer((host, port), _MCPHandler)


def serve_http(addr):
    global AUDIT
    host, _, port = addr.rpartition(":")
    host = host.strip("[]") or "127.0.0.1"
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        sys.stderr.write(f"mu4e-mcp: WARNING binding {host} (not loopback) — put an "
                         "authenticating proxy in front\n")
    AUDIT = True
    httpd = make_http_server(host, int(port))
    sys.stderr.write(f"mu4e-mcp: listening on http://{addr}{HTTP_PATH}\n")
    httpd.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(description="MCP server for mu email search")
    ap.add_argument("--http", metavar="HOST:PORT",
                    help="serve Streamable HTTP at HOST:PORT/mcp instead of stdio")
    opts = ap.parse_args(argv)
    if opts.http:
        serve_http(opts.http)
        return
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()
