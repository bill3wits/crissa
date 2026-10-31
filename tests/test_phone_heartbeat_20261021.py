"""The page's heartbeat (round 39, .101 round 38 N7, the User: "i closed phone and she did not return"): his iPhone sent
no end beacon when he closed the page, so phone-session.json held active_until = the last floor + 180 s and the desk
walked her back three minutes later. While visible the page beats every 10 s; when the beats stop the session ends
within BEAT_S whatever happened to the beacon; a page left open and idle still ends at the idle window."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import phone_session as PS  # noqa: E402


@pytest.fixture
def f(tmp_path, monkeypatch):
    for k in ("CRISSA_PHONE_SESSION", "CRISSA_PHONE_IDLE_S", "CRISSA_PHONE_BEAT", "CRISSA_PHONE_BEAT_S"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "phone-session.json"


def test_the_101_case_a_closed_page_with_no_beacon_ends_within_the_beat_window(f):
    t = 1000.0
    PS.beat(f, now=t)                                    # the page opens and beats
    PS.use(f, "floor", now=t + 5)                        # he talks (08:09:50 on .101)
    PS.beat(f, now=t + 10)
    # he closes the page: no end beacon, no more beats
    assert PS.active(PS.read(f), now=t + 10 + PS.BEAT_S - 1)
    assert not PS.active(PS.read(f), now=t + 10 + PS.BEAT_S)
    assert PS.read(f)["use_until"] == t + 5 + PS.IDLE_S  # the old rule would have held her away until here


def test_a_page_left_open_and_idle_still_ends_at_the_idle_window(f):
    t = 1000.0
    PS.beat(f, now=t)
    PS.use(f, "floor", now=t)
    for i in range(1, 30):                               # visible in a pocket: beats every 10 s, no use
        PS.beat(f, now=t + 10 * i)
        d = PS.read(f)
        assert d["active_until"] <= t + PS.IDLE_S        # a beat never lengthens it past the uses
    assert PS.active(PS.read(f), now=t + PS.IDLE_S - 1) and not PS.active(PS.read(f), now=t + PS.IDLE_S)


def test_her_line_holds_while_the_page_beats(f):
    t = 1000.0
    PS.beat(f, now=t)
    PS.use(f, "line", hold_s=40.0, now=t)
    for i in range(1, 6):
        PS.beat(f, now=t + 10 * i)
    assert PS.active(PS.read(f), now=t + 50 + PS.BEAT_S - 1)


def test_a_beat_never_starts_a_session(f):
    PS.beat(f, now=1000.0)
    assert not PS.active(PS.read(f), now=1000.0)
    PS.use(f, "floor", now=1000.0)
    PS.end(f, now=1001.0)
    PS.beat(f, now=1002.0)                               # an ended session stays ended
    assert not PS.active(PS.read(f), now=1002.0)


def test_a_client_that_never_beats_keeps_the_old_rule(f):
    PS.use(f, "floor", now=1000.0)                       # the iPhone app today: no beats
    assert PS.read(f)["active_until"] == 1000.0 + PS.IDLE_S
    PS.beat(f, now=900.0)                                # a beat from a page closed long ago does not cap a new use
    f.unlink()
    PS.beat(f, now=900.0)
    PS.use(f, "floor", now=1000.0)
    assert PS.read(f)["active_until"] == 1000.0 + PS.IDLE_S


def test_the_kill_is_the_old_rule(f, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_BEAT", "0")
    assert PS.beat(f, now=1000.0) == {} and not f.exists()
    PS.use(f, "floor", now=1000.0)
    assert PS.read(f)["active_until"] == 1000.0 + PS.IDLE_S


def test_the_window_is_a_dial(f, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_BEAT_S", "40")
    PS.beat(f, now=1000.0)
    PS.use(f, "floor", now=1000.0)
    assert PS.read(f)["active_until"] == 1040.0


# ---- the server and the page ----

def _server(tmp_path, pairing=False, loopback=True):
    spec = importlib.util.spec_from_file_location("phone_server_beat", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CFG.update({"pairing": pairing, "pair_file": str(tmp_path / "pairing.json"),
                  "session_file": str(tmp_path / "phone-session.json"), "floor_file": str(tmp_path / "phone-floor.json"),
                  "packs": str(tmp_path), "pack": "x", "lines": str(tmp_path / "lines")})
    if not loopback:
        m.H._loopback = lambda self: False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return m, srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def test_the_beat_route_is_not_a_use(tmp_path, f):
    m, srv, base = _server(tmp_path)
    try:
        assert _post(base + "/api/session", {"here": True})["ok"]
        d = PS.read(tmp_path / "phone-session.json")
        assert d.get("beat_until") and not PS.active(d)
        _post(base + "/api/floor", {"on": True})
        d = PS.read(tmp_path / "phone-session.json")
        assert PS.active(d) and d["active_until"] <= d["beat_until"]
    finally:
        srv.shutdown()


def test_an_unpaired_device_cannot_beat(tmp_path, f):
    m, srv, base = _server(tmp_path, pairing=True, loopback=False)
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base + "/api/session", {"here": True})
        assert e.value.code == 401 and not (tmp_path / "phone-session.json").exists()
    finally:
        srv.shutdown()


def test_the_page_beats_every_10_s_while_visible():
    js = (ROOT / "display" / "phone-app" / "app.js").read_text(encoding="utf-8")
    assert "beat(); setInterval(beat, 10000);" in js and "JSON.stringify({ here: true })" in js
    assert "if (document.hidden || !isConnected()) return;" in js
    assert "if (document.hidden) endSession(); else beat();" in js       # the beacon stays: it is the fast path
