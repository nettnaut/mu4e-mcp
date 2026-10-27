#!/usr/bin/env python3
"""Tests for mu4e-mcp: JSON-RPC handling, input hardening, and HTTP mode.

No `mu` needed — subprocess.run is stubbed. Run: python3 tests/test_mu4e_mcp.py
"""
import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "mu4emcp", os.path.join(os.path.dirname(__file__), "..", "mu4e-mcp.py"))
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)

MESSAGE = (b"From: Alice <alice@example.com>\r\nTo: me@example.com\r\n"
           b"Subject: Hello\r\nDate: Tue, 27 Oct 2026 10:00:00 +0800\r\n"
           b"Message-ID: <abc@example.com>\r\n\r\nHi there.\r\n")


def rpc(method, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        msg["id"] = rid
    if params is not None:
        msg["params"] = params
    return msg


class FakeRun:
    """Stand-in for subprocess.run: records argv, returns canned mu output."""
    def __init__(self, stdout="[]"):
        self.calls, self.stdout = [], stdout

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = self.stdout if cmd[:2] == ["mu", "find"] else ""
        return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    def __enter__(self):
        self._orig = mcp.subprocess.run
        mcp.subprocess.run = self
        return self

    def __exit__(self, *exc):
        mcp.subprocess.run = self._orig

    def find_argv(self):
        return next(c for c in self.calls if c[:2] == ["mu", "find"])


class Maildir:
    """A temporary maildir with one message; points mcp.MU_MAILDIR at it."""
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root / "mail"
        (self.root / "acct" / "INBOX" / "cur").mkdir(parents=True)
        self.msg = self.root / "acct" / "INBOX" / "cur" / "1.eml"
        self.msg.write_bytes(MESSAGE)
        self.outside = root / "secret.txt"
        self.outside.write_text("deploy-key-material\n")
        self._orig = mcp.MU_MAILDIR
        mcp.MU_MAILDIR = str(self.root)
        return self

    def __exit__(self, *exc):
        mcp.MU_MAILDIR = self._orig
        self.tmp.cleanup()


def call(name, args):
    return mcp.handle(rpc("tools/call", {"name": name, "arguments": args}))


# --------------------------------------------------------------------------- #
class Handle(unittest.TestCase):
    def test_initialize(self):
        r = mcp.handle(rpc("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(r["result"]["serverInfo"]["name"], "mu4e-mcp")
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_unknown_version_falls_back(self):
        r = mcp.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}))
        self.assertIn(r["result"]["protocolVersion"], mcp.PROTOCOL_VERSIONS)

    def test_notification_no_response(self):
        self.assertIsNone(mcp.handle(rpc("notifications/initialized", rid=None)))

    def test_tools_list(self):
        names = {t["name"] for t in mcp.handle(rpc("tools/list"))["result"]["tools"]}
        self.assertEqual(names, {"search_email", "get_email"})

    def test_unknown_tool_is_32602(self):
        self.assertEqual(call("send_email", {})["error"]["code"], -32602)

    def test_unknown_method(self):
        self.assertEqual(mcp.handle(rpc("nope"))["error"]["code"], -32601)


# --------------------------------------------------------------------------- #
class SearchHardening(unittest.TestCase):
    def test_query_terms_follow_end_of_options(self):
        with FakeRun() as run:
            call("search_email", {"query": "from:alice subject:invoice"})
        argv = run.find_argv()
        self.assertIn("--", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["from:alice", "subject:invoice"])

    def test_option_injection_is_only_a_search_term(self):
        with FakeRun() as run:
            call("search_email", {"query": "invoice --exec=rm\\ -rf\\ ~ --muhome=/tmp"})
        argv = run.find_argv()
        opts = argv[:argv.index("--")]
        self.assertFalse(any(a.startswith("--exec") for a in opts), argv)
        self.assertEqual([a for a in opts if a.startswith("--muhome")],
                         [f"--muhome={mcp.MU_HOME}"])

    def test_max_results_must_be_integer(self):
        with FakeRun() as run:
            r = call("search_email", {"query": "x", "max_results": "5 --exec=id"})
        self.assertTrue(r["result"]["isError"])
        self.assertFalse(any(c[:2] == ["mu", "find"] for c in run.calls))

    def test_search_results_are_slimmed(self):
        hit = [{":path": "/m/1", ":subject": "Hi", ":message-id": "<a@b>",
                ":date": [27000, 0, 0]}]
        with FakeRun(stdout=json.dumps(hit)):
            r = call("search_email", {"query": "x"})
        out = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(out[0]["message_id"], "a@b")
        self.assertEqual(out[0]["mu4e_link"], "[[mu4e:msgid:a@b][Hi]]")


# --------------------------------------------------------------------------- #
class GetEmailConfinement(unittest.TestCase):
    def test_reads_message_inside_maildir(self):
        with Maildir() as md:
            r = call("get_email", {"path": str(md.msg)})
        self.assertFalse(r["result"].get("isError", False))
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["subject"], "Hello")

    def test_file_outside_maildir_refused(self):
        with Maildir() as md:
            r = call("get_email", {"path": str(md.outside)})
        self.assertTrue(r["result"]["isError"])
        self.assertNotIn("deploy-key-material", json.dumps(r))

    def test_dotdot_traversal_refused(self):
        with Maildir() as md:
            r = call("get_email", {"path": str(md.root / "acct" / ".." / ".." / "secret.txt")})
        self.assertTrue(r["result"]["isError"])
        self.assertNotIn("deploy-key-material", json.dumps(r))

    def test_symlink_out_of_maildir_refused(self):
        with Maildir() as md:
            link = md.root / "acct" / "INBOX" / "cur" / "2.eml"
            link.symlink_to(md.outside)
            r = call("get_email", {"path": str(link)})
        self.assertTrue(r["result"]["isError"])
        self.assertNotIn("deploy-key-material", json.dumps(r))

    def test_sibling_prefix_dir_refused(self):
        # /tmp/x/mail-evil must not pass a naive startswith("/tmp/x/mail") check.
        with Maildir() as md:
            evil = Path(str(md.root) + "-evil")
            evil.mkdir()
            (evil / "m.eml").write_bytes(MESSAGE)
            r = call("get_email", {"path": str(evil / "m.eml")})
        self.assertTrue(r["result"]["isError"])

    def test_directory_refused(self):
        with Maildir() as md:
            r = call("get_email", {"path": str(md.root / "acct")})
        self.assertTrue(r["result"]["isError"])


# --------------------------------------------------------------------------- #
class HTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = mcp.make_http_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self._token, self._origins = mcp.HTTP_TOKEN, mcp.ALLOWED_ORIGINS

    def tearDown(self):
        mcp.HTTP_TOKEN, mcp.ALLOWED_ORIGINS = self._token, self._origins

    def request(self, method="POST", body=None, path="/mcp", headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp, (json.loads(payload) if payload else None)

    def test_initialize(self):
        resp, body = self.request(body=rpc("initialize", {"protocolVersion": "2025-03-26"}))
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")

    def test_notification_202(self):
        resp, body = self.request(body=rpc("notifications/initialized", rid=None))
        self.assertEqual((resp.status, body), (202, None))

    def test_batch(self):
        resp, body = self.request(body=[rpc("tools/list", rid=1),
                                        rpc("notifications/initialized", rid=None)])
        self.assertEqual(resp.status, 200)
        self.assertEqual([r["id"] for r in body], [1])

    def test_get_405(self):
        resp, _ = self.request(method="GET")
        self.assertEqual(resp.status, 405)

    def test_wrong_path_404(self):
        resp, _ = self.request(body=rpc("tools/list"), path="/")
        self.assertEqual(resp.status, 404)

    def test_bad_json_400(self):
        resp, body = self.request(raw=b"nope")
        self.assertEqual((resp.status, body["error"]["code"]), (400, -32700))

    def test_oversized_413(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", "/mcp")
        conn.putheader("Content-Length", str(mcp.MAX_BODY + 1))
        conn.endheaders()
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 413)

    def test_foreign_origin_403(self):
        mcp.ALLOWED_ORIGINS = set()
        resp, _ = self.request(body=rpc("tools/list"), headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 403)

    def test_token(self):
        mcp.HTTP_TOKEN = "t0ken-for-tests"
        self.assertEqual(self.request(body=rpc("tools/list"))[0].status, 401)
        ok, _ = self.request(body=rpc("tools/list"),
                             headers={"Authorization": "Bearer t0ken-for-tests"})
        self.assertEqual(ok.status, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
