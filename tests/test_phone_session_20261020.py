"""She is on the phone (the User 2026-10-20, .101 ASK-20261020-0230): lib/phone_session and the phone server's
/api/session; the desk Wall's whole rule is active_until > now (the kit thread's side reads the same file)."""
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
    monkeypatch.delenv("CRISSA_PHONE_SESSION", raising=False)
    monkeypatch.delenv("CRISSA_PHONE_IDLE_S", raising=False)
    return tmp_path / "phone-session.json"


def test_no_file_is_not_active(f):
    assert PS.read(f) == {} and not PS.active(PS.read(f))
    f.write_text("{not json")
    assert not PS.active(PS.read(f))


def test_a_use_is_active_for_the_idle_window(f):
    d = PS.use(f, "floor", now=1000.0)
    assert d["active_until"] == 1000.0 + PS.IDLE_S and d["since"] == 1000.0
    assert PS.active(PS.read(f), now=1000.0 + PS.IDLE_S - 1) and not PS.active(PS.read(f), now=1000.0 + PS.IDLE_S)


def test_her_line_holds_for_its_length_then_the_idle_window(f, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_IDLE_S", "60")
    PS.use(f, "line", hold_s=12.0, now=1000.0)
    assert PS.read(f)["active_until"] == 1072.0 and PS.read(f)["idle_s"] == 60.0


def test_a_use_never_shortens_a_longer_one_and_keeps_its_start(f):
    PS.use(f, "line", hold_s=30.0, now=1000.0)
    d = PS.use(f, "floor", now=1005.0)
    assert d["active_until"] == 1030.0 + PS.IDLE_S and d["since"] == 1000.0 and d["last"] == 1005.0


def test_a_pocket_goes_idle_and_the_next_use_is_a_new_session(f):
    PS.use(f, "floor", now=1000.0)
    d = PS.use(f, "floor", now=2000.0)
    assert d["since"] == 2000.0


def test_the_page_closing_ends_it_now(f):
    PS.use(f, "line", hold_s=20.0, now=1000.0)
    d = PS.end(f, now=1003.0)
    assert d["active_until"] == 1003.0 and d["ended"] == 1003.0 and not PS.active(d, now=1003.0)
    assert PS.end(f, now=1010.0)["ended"] == 1003.0          # an ended session is left as it is
    d = PS.use(f, "floor", now=1011.0)
    assert d["ended"] == 0 and d["since"] == 1011.0


def test_the_kill_writes_nothing(f, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_SESSION", "0")
    assert PS.use(f, "floor") == {} and PS.end(f) == {} and not f.exists()


# ---- the phone server ----

def _server(tmp_path):
    spec = importlib.util.spec_from_file_location("phone_server_session", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CFG.update({"pairing": False, "session_file": str(tmp_path / "phone-session.json"),
                  "floor_file": str(tmp_path / "phone-floor.json"), "packs": str(tmp_path), "pack": "x",
                  "lines": str(tmp_path / "lines")})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return m, srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def _get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.load(r)


def test_the_talk_floor_starts_a_session_and_the_page_ends_it(tmp_path, f):
    m, srv, base = _server(tmp_path)
    try:
        assert _get(base + "/api/session")["active"] is False
        _post(base + "/api/floor", {"on": True})
        got = _get(base + "/api/session")
        assert got["active"] is True and got["why"] == "floor"
        _post(base + "/api/floor", {"on": False})              # a release is not a use: idle runs from the last one
        assert _get(base + "/api/session")["active"] is True
        assert _post(base + "/api/session", {"end": True})["session"]["ended"] > 0
        assert _get(base + "/api/session")["active"] is False
    finally:
        srv.shutdown()


def test_her_line_is_a_use_for_its_length_even_with_the_floor_off(tmp_path, f, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_FLOOR", "0")
    m, srv, base = _server(tmp_path)
    try:
        m._floor(9.0, line=True)
        d = PS.read(tmp_path / "phone-session.json")
        assert d["why"] == "line" and d["active_until"] - d["last"] == pytest.approx(9.0 + PS.IDLE_S, abs=0.01)
        assert not (tmp_path / "phone-floor.json").exists()
    finally:
        srv.shutdown()


def test_the_open_mic_is_not_a_use():
    src = (ROOT / "scripts" / "phone-app-server.py").read_text()
    mic = src.split('if path == "/api/mic":', 1)[1].split("if n > JSON_MAX_BYTES", 1)[0]
    assert "_session(" not in mic and "_floor(" not in mic


def test_the_path_is_the_one_the_wall_reads(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("phone_server_session_p", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import crissa_home
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.delenv("CRISSA_PHONE_SESSION", raising=False)
    assert m._session_file() == tmp_path / "phone-session.json"
    monkeypatch.setenv("CRISSA_PHONE_SESSION", str(tmp_path / "x.json"))
    assert m._session_file() == tmp_path / "x.json"


def test_the_page_ends_the_session_when_it_closes_or_hides():
    js = (ROOT / "display" / "phone-app" / "app.js").read_text()
    assert 'addEventListener("pagehide", endSession)' in js
    assert "if (document.hidden) endSession();" in js and 'sendBeacon("/api/session"' in js


def test_it_ships_in_the_phone_row():
    row = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]["crissa-phone"]
    assert "lib/phone_session.py" in row["files"]


# ---- the QR hand-off (the User 2026-10-20: "default to house connection only if the QR code is used") ----

import phone_handoff as PH  # noqa: E402


def test_a_token_is_single_use_and_expires():
    t = PH.mint(now=1000.0)
    assert t["expires"] == 1000.0 + PH.TTL_S and len(t["token"]) >= 20
    assert PH.claim(t["token"], now=1001.0) and not PH.claim(t["token"], now=1002.0)
    late = PH.mint(now=1000.0)["token"]
    assert not PH.claim(late, now=1000.0 + PH.TTL_S + 1) and not PH.claim(late, now=1000.0)
    assert not PH.claim("") and not PH.claim("nope")


def test_only_the_computer_mints_and_a_claim_is_once(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_PHONE_HANDOFF", raising=False)
    m, srv, base = _server(tmp_path)
    try:
        assert _get(base + "/api/connection")["handoff"] is True
        h = _post(base + "/api/handoff", {"by": "wall"})
        assert h["ok"] and h["token"] and h["expires"] > 0
        assert not h["url"] or h["url"].endswith("/?handoff=" + h["token"])
        assert _post(base + "/api/handoff/claim", {"token": h["token"]})["ok"] is True
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base + "/api/handoff/claim", {"token": h["token"]})
        assert e.value.code == 403
    finally:
        srv.shutdown()
    src = (ROOT / "scripts" / "phone-app-server.py").read_text()
    mint = src.split('if path == "/api/handoff":', 1)[1].split("phone_handoff.mint()", 1)[0]
    assert "if not self._loopback():" in mint and "403" in mint


def test_the_kill_keeps_todays_page(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_HANDOFF", "0")
    m, srv, base = _server(tmp_path)
    try:
        assert _get(base + "/api/connection")["handoff"] is False
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base + "/api/handoff", {"by": "wall"})
        assert e.value.code == 404
    finally:
        srv.shutdown()


def test_the_page_claims_once_strips_the_token_and_never_defaults_to_the_house():
    js = (ROOT / "display" / "phone-app" / "app.js").read_text()
    html = (ROOT / "display" / "phone-app" / "index.html").read_text()
    assert 'window.history.replaceState(null, "", location.pathname)' in js     # `history` is the chat's array
    assert 'fetch("/api/handoff/claim"' in js and 'useHouse("qr")' in js
    assert "if (!isConnected()) { openConnect(true); return; }" in js           # Send waits on a connection
    assert "const isConnected = () => (!handoffRule || (conn && conn.mode === \"house\")) && mayUse();" in js
    assert "coming in the Crissa app" in html and 'id="useHouse"' in html and 'id="leaveHouse"' in html


def test_the_hand_off_ships_in_the_phone_row():
    row = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]["crissa-phone"]
    assert "lib/phone_handoff.py" in row["files"] and "scripts/phone-session-accept.py" not in row["files"]
