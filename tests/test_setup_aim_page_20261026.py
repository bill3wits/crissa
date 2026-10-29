"""The one-page Setup draws the door step: the camera's picture, the Turn keys and You at 6 (2026-10-26).

The acceptance-loads-the-page rule: the User's walk sat on the clock while the walk was in "aim", because only the
step-by-step page drew the aim step. Here the real page (display/crissa-init.html) is loaded by a headless browser
(its own profile, no window) against a stand-in /v1/init in the room step's aim state, and the DOM is read.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                      "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
KEYS = [["left", "◀ Turn left"], ["right", "Turn right ▶"], ["door_here", "This is my door"],
        ["door_none", "My door is out of view"]]


def _snap(port: int, state: str) -> dict:
    room = {"state": "marks" if state == "door" else state, "marks": {"seat": 6, "speakers": 0, "door": 10}, "order": ["speakers", "door"],
            "now": {"aim": "", "door": "door"}.get(state, "speakers"), "user_hour": 6, "labels": {}, "asks": {},
            "door_unreachable": [12],
            "camera_url": "http://127.0.0.1:%d/snapshot" % port, "camera": "on", "changed": ["speakers", "door"],
            "defaults": {"seat": 6, "speakers": 0}, "computer": True, "camera_kind": "fixed"}
    return {"ok": True, "pack": True, "walker": "crissa", "needed": True, "status": "running", "run_id": "r1",
            "index": 4, "total": 6, "step_id": "room", "kind": "room",
            "steps": [{"id": s} for s in ("connect", "machine", "voice_id", "face", "room", "hours")],
            "plate": {"kind": "room", "title": "Your room", "room": room,
                      "body": "Turn the camera with the two keys until your door is in the middle of the picture.",
                      "hint": "", "keys": KEYS if state == "aim" else [["next", "Next"]]},
            "one_page": {"films": {}, "sections": {}, "page_said": [], "face_section": True, "krew": True},
            "browser": {}, "ids": None}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def page(tmp_path):
    acc = _load("accept_aim", ROOT / "scripts" / "public-loops-accept.py")
    br = acc._browser()
    if not br:
        pytest.skip("no headless browser on this machine")
    state = {"room": "marks"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            p = self.path.split("?")[0]
            if p in ("/", "/init"):
                return self._send((ROOT / "display" / "crissa-init.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/snapshot":
                return self._send(PIXEL, "image/png")
            body = _snap(srv.server_address[1], state["room"]) if p == "/v1/init" else {"ok": True}
            return self._send(json.dumps(body).encode(), "application/json")

        def do_POST(self):  # noqa: N802
            return self._send(b'{"ok": true}', "application/json")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    pg = acc._Page(br, str(tmp_path / "prof"), "http://127.0.0.1:%d/init" % srv.server_address[1], str(tmp_path / "b.log"))
    yield pg, state
    pg.close()
    srv.shutdown()


def _wait(pg, expr, secs=12.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        got = pg.js(expr)
        if got:
            return got
        time.sleep(0.4)
    return pg.js(expr)


def test_the_clock_shows_you_at_6_and_no_camera_mark(page):
    pg, state = page
    tags = _wait(pg, "JSON.stringify([...document.querySelectorAll('#op-clock button.hr')]"
                     ".map(b=>[b.dataset.hour, (b.querySelector('small')||{}).textContent||'']))")
    got = dict(json.loads(tags or "[]"))
    assert got, "the one-page room clock was not drawn"
    assert got.get("6") == "You" and "Camera" not in json.dumps(got), got


def test_the_aim_step_draws_the_picture_and_the_turn_keys(page):
    pg, state = page
    assert _wait(pg, "!!document.querySelector('#op-clock button.hr')"), "the clock first"
    state["room"] = "aim"
    _wait(pg, "!!document.getElementById('op-aim-body')", secs=15.0)
    shown = _wait(pg, "JSON.stringify({cam: !!document.getElementById('rm-cam'), "
                      "body: (document.getElementById('op-aim-body')||{}).textContent||'', "
                      "keys: [...document.querySelectorAll('#op-clock [data-verb]')].map(b=>b.dataset.verb), "
                      "back: !!document.getElementById('rm-back')})", secs=15.0)
    d = json.loads(shown)
    assert d["cam"] and d["back"] and d["body"].startswith("Turn the camera"), d
    assert d["keys"] == ["left", "right", "door_here", "door_none"], d


def test_the_door_hour_the_camera_cannot_turn_to_is_greyed(page):
    """The User 2026-10-26: "must grey out 12" - only while the door is the mark being set."""
    pg, state = page
    q = ("JSON.stringify([...document.querySelectorAll('#op-clock button.hr')].filter(b=>b.disabled)"
         ".map(b=>b.dataset.hour))")
    assert _wait(pg, "!!document.querySelector('#op-clock button.hr')"), "the clock first"
    assert json.loads(pg.js(q)) == []                                  # the speakers' mark: every hour
    state["room"] = "door"
    some = ("(()=>{const a=[...document.querySelectorAll('#op-clock button.hr')].filter(b=>b.disabled)"
            ".map(b=>b.dataset.hour); return a.length ? JSON.stringify(a) : ''})()")
    assert json.loads(_wait(pg, some, secs=15.0) or "[]") == ["12"]


def test_the_page_says_what_voice_id_waits_for():
    page = (ROOT / "display" / "crissa-init.html").read_text()
    assert "Say a few words so I can hear your microphone. Voice ID starts then." in page
    assert "Voice ID starts when Lila finishes and I hear you." in page
