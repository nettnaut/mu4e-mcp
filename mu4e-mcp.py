#!/usr/bin/env python3
# Copyright (C) 2026 Kjetil Rohde Jakobsen
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software under the GNU General Public License v3
# or later; see the LICENSE file or <https://www.gnu.org/licenses/>.
"""Minimal MCP server exposing mu email search to Claude."""

import json
import subprocess
import sys
import email
import os


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def mu_find(query, max_results=20, include_body=False):
    cmd = ["mu", "find", "--format", "json", f"--maxnum={max_results}"] + query.split()
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 4):  # 4 = no results found
        raise RuntimeError(result.stderr.strip())
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def mu_view(path):
    """Read and parse a raw email file."""
    with open(path, "rb") as f:
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


def handle(request):
    method = request.get("method")
    rid = request.get("id")

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mu4e-mcp", "version": "1.0.0"},
            },
        })

    elif method == "notifications/initialized":
        pass  # no response needed

    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})

    elif method == "tools/call":
        name = request["params"]["name"]
        args = request["params"].get("arguments", {})
        try:
            if name == "search_email":
                results = mu_find(args["query"], args.get("max_results", 20))
                # Slim down the output — keep only useful fields
                slim = []
                for m in results:
                    raw_msgid = m.get(":message-id", "") or ""
                    message_id = raw_msgid.strip().strip("<>")
                    subject = m.get(":subject", "") or ""
                    slim.append({
                        "path": m.get(":path"),
                        "subject": subject,
                        "from": m.get(":from"),
                        "to": m.get(":to"),
                        "date": m.get(":date-unix"),
                        "maildir": m.get(":maildir"),
                        "flags": m.get(":flags"),
                        "message_id": message_id,
                        "mu4e_link": f"[[mu4e:msgid:{message_id}][{subject}]]" if message_id else "",
                    })
                content = json.dumps(slim, indent=2)
            elif name == "get_email":
                content = json.dumps(mu_view(args["path"]), indent=2)
            else:
                raise ValueError(f"Unknown tool: {name}")

            send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"content": [{"type": "text", "text": content}]},
            })
        except Exception as e:
            send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            })
    else:
        if rid is not None:
            send({
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(request)


if __name__ == "__main__":
    main()
