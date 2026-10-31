"""Plain errors (the User 2026-10-07): a failed agent call says what to fix on the Connect page.

A 404 = the model name; a refused connection with no Address = the Address is empty; 401/403 = the key;
a timeout says so. The raw error stays in `detail` (the page's tooltip, the conductor's log), never the key.
"""
from __future__ import annotations

import socket
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_agent as za  # noqa: E402


class _Status(BaseHTTPRequestHandler):
    code = 404

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(type(self).code)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture()
def status():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Status)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/v1" % srv.server_address[1]
    srv.shutdown()


@pytest.mark.parametrize("code,want", [(404, "Model not found - check the Model name."),
                                       (401, "check the API key"), (403, "check the API key"),
                                       (500, "HTTP 500")])
def test_http_status_is_plain(status, code, want):
    _Status.code = code
    r = za.test({"url": status, "model": "nope", "key": "sk-secret"}, timeout=5)
    assert r["ok"] is False and want in r["error"]
    assert str(code) in r["detail"] and "sk-secret" not in r["detail"] + r["error"]


def test_an_empty_address_says_so():
    r = za.test({"url": "", "model": "m"}, timeout=2)
    assert r["error"] == za.EMPTY_ADDRESS


def test_refused_with_an_empty_address_says_the_address_is_empty():
    e = za.plain_error(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")), {"url": ""})
    assert str(e) == za.EMPTY_ADDRESS and "Connection refused" in e.detail


def test_refused_at_an_address_says_check_it():
    r = za.test({"url": "http://127.0.0.1:9/v1"}, timeout=2)
    assert r["ok"] is False and r["error"] == za.REFUSED_PLAIN and "refused" in r["detail"].lower()


def test_a_timeout_says_so():
    e = za.plain_error(urllib.error.URLError(socket.timeout("timed out")), {"url": "http://h/v1"})
    assert str(e) == za.TIMEOUT_PLAIN and "timed out" in e.detail
    assert str(za.plain_error(TimeoutError("timed out"), {"url": "http://h/v1"})) == za.TIMEOUT_PLAIN


def test_the_page_says_the_address_is_empty_before_asking_and_shows_the_raw_error_as_detail():
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert "const EMPTY_ADDRESS = \"The Address is empty - enter your agent's address.\";" in page
    assert page.count("if (addressMissing())") == 2          # Test and Save
    assert 'j.ok ? "" : j.detail' in page and "el.title = detail" in page


def test_the_conductor_logs_the_raw_reason():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'action("ask", "failed: %s [%s]" % (exc, getattr(exc, "detail", "")), by)' in src
