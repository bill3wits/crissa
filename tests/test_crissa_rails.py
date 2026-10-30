"""The services she may sit beside, through the player (2026-10-06): reads go through on any path, writes only on
the named paths and only from this machine, a service this Crissa has not set up says so, and the ear's health
reads both parts of the voice module (lib/crissa_rails.py, GET /v1/ear, /v1/rail/<name>/, POST /v1/ear/open)."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_rails as zr  # noqa: E402


@pytest.fixture()
def svc():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, raw, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen.append(("GET", self.path, None))
            if self.path.startswith("/snapshot"):
                return self._send(200, b"\xff\xd8jpeg" * 50000, "image/jpeg")
            if self.path == "/missing":
                return self._send(404, b'{"error": "nope"}')
            self._send(200, json.dumps({"ok": True, "headcount": 1, "path": self.path}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            seen.append(("POST", self.path, body))
            self._send(200, json.dumps({"ok": True, "got": body}).encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], seen
    srv.shutdown()


def test_reads_go_through_whole_with_the_services_status_and_type(svc):
    url, seen = svc
    env = {"CRISSA_CAMERA_URL": url + "/", "CRISSA_PRESENCE_URL": url}
    code, ctype, raw = zr.forward("camera", "/snapshot", query="w=1", env=env)
    assert code == 200 and ctype == "image/jpeg" and len(raw) == 6 * 50000, "nothing is shortened"
    assert seen[-1] == ("GET", "/snapshot?w=1", None)
    code, _, raw = zr.forward("presence", "/v1/presence", env=env)
    assert code == 200 and json.loads(raw)["headcount"] == 1
    assert zr.forward("presence", "/missing", env=env)[0] == 404


def test_writes_only_on_the_named_paths(svc):
    url, seen = svc
    env = {"CRISSA_ENROLL_URL": url}
    code, _, raw = zr.forward("enroll", "/start", method="POST", body={"display": "Ann", "mode": "face"}, env=env)
    assert code == 200 and json.loads(raw)["got"]["display"] == "Ann"
    code, _, raw = zr.forward("enroll", "/delete", method="POST", body={}, env=env)
    assert code == 405 and "only reads" in json.loads(raw)["why"]
    assert [s for s in seen if s[0] == "POST"] == [("POST", "/start", {"display": "Ann", "mode": "face"})]


def test_not_set_up_unknown_down_and_bad_paths_say_so():
    code, _, raw = zr.forward("presence", "/v1/presence", env={})
    assert code == 503 and "this Crissa has no presence feed (set CRISSA_PRESENCE_URL" in json.loads(raw)["why"]
    assert zr.forward("garage", "/x", env={})[0] == 404
    code, _, raw = zr.forward("camera", "/health", env={"CRISSA_CAMERA_URL": "http://127.0.0.1:9"})
    assert code == 502 and "not answering at http://127.0.0.1:9" in json.loads(raw)["why"]
    assert zr.forward("camera", "@evil.example/x", env={"CRISSA_CAMERA_URL": "http://127.0.0.1:9"})[0] == 400
    assert zr.forward("camera", "/../x", env={"CRISSA_CAMERA_URL": "http://127.0.0.1:9"})[0] == 400
    assert zr.split("/v1/rail/presence/v1/presence") == ("presence", "/v1/presence")
    assert zr.split("/v1/rail/") == ("", "")


def test_ear_reads_both_parts_or_one(svc):
    url, _ = svc
    got = zr.ear(url, url)
    assert got["ok"] and got["stt"]["path"] == "/health" and got["asr"]["path"] == "/health"
    half = zr.ear(url, "http://127.0.0.1:9", timeout=0.5)
    assert not half["ok"] and half["stt"] and half["asr"] is None and "speech to text is not answering" in half["why"]
    assert zr.ear(url, "http://127.0.0.1:9", part="stt")["ok"]
    assert not zr.ear("http://127.0.0.1:9", url, part="stt", timeout=0.5)["ok"]


def test_the_player_routes_follow_the_loopback_rule():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    get = src.split("            # PRESENCE, CAMERA, REGISTRATION through the player", 1)[1][:600]
    assert "connect_origin_ok(self.headers)" in get and "crissa_rails.forward(name, sub, query=" in get
    post = src.split('        if head == "/v1/ear/open":', 1)[1][:1400]
    assert post.count("connect_origin_ok(self.headers)") == 2 and 'method="POST"' in post
    ear = src.split('        if head == "/v1/ear":', 1)[1][:600]
    assert 'self._json(200 if out["ok"] else 503, out)' in ear
    for gone in ("8191", "8192", "8196"):
        assert gone not in (ROOT / "lib" / "crissa_rails.py").read_text()


def test_the_aligner_is_crissa_own_rail_read_only(svc):
    root, seen = svc[0], svc[1]
    assert zr.base("align", {}) == "http://127.0.0.1:8195"
    assert zr.base("align", {"CRISSA_PORT_OFFSET": "10000"}) == "http://127.0.0.1:18195"
    code, _, raw = zr.forward("align", "/health", env={"CRISSA_ALIGN_URL": root})
    assert code == 200
    code, _, raw = zr.forward("align", "/health", method="POST", body={}, env={"CRISSA_ALIGN_URL": root})
    assert code == 405 and b"nothing" in raw
    code, _, raw = zr.forward("align", "/health", env={"CRISSA_ALIGN_URL": "http://127.0.0.1:9"})
    assert code == 502 and b"aligner" in raw
