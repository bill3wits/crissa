"""Pairing is on in a pack (round 38, the User's "could someone else hijack her?", .101 round 37: with it off anyone on
the Wi-Fi could chat on his agent, make her speak and walk her out of the desk). The QR's single-use claim is the
pairing, every other device gets 401, this computer is always let in; the code lives 3 minutes (the User: "can we close
QR after a period?")."""
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

import phone_handoff as PH  # noqa: E402


def _mod(name="phone_server_pairing_on"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_on_in_a_pack_off_on_the_house_and_the_switch_decides(monkeypatch):
    m = _mod()
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a: False)
    assert m.pairing_default({}) is True
    assert m.pairing_default({"CRISSA_PHONE_PAIRING": "0"}) is False
    monkeypatch.setattr(crissa_home, "is_house", lambda *a: True)
    assert m.pairing_default({}) is False
    assert m.pairing_default({"CRISSA_PHONE_PAIRING": "1"}) is True


def test_the_flag_follows_the_default_and_turns_off_by_name(monkeypatch):
    src = (ROOT / "scripts" / "phone-app-server.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--pairing", action=argparse.BooleanOptionalAction, default=pairing_default(),' in src


def _server(tmp_path, loopback: bool):
    m = _mod("phone_server_pairing_on_srv")
    m.CFG.update({"pairing": True, "pair_file": str(tmp_path / "pairing.json"),
                  "session_file": str(tmp_path / "phone-session.json"), "floor_file": str(tmp_path / "phone-floor.json"),
                  "packs": str(tmp_path), "pack": "x", "lines": str(tmp_path / "lines"),
                  "agent_file": str(tmp_path / "agent.json"), "agent": "", "model": ""})
    if not loopback:
        m.H._loopback = lambda self: False         # another device on the Wi-Fi
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return m, srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _call(url, body=None, cookie=""):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers


HIJACK = [("/api/agent", None), ("/api/chat", {"text": "hi"}), ("/api/say", {"text": "hi"}), ("/api/floor", {"on": True})]


def test_another_device_with_no_pairing_gets_401_everywhere_that_matters(tmp_path):
    m, srv, base = _server(tmp_path, loopback=False)
    try:
        for path, body in HIJACK:
            assert _call(base + path, body)[0] == 401, path
        assert not (tmp_path / "phone-floor.json").exists() and not (tmp_path / "phone-session.json").exists()
    finally:
        srv.shutdown()


def test_the_qr_claim_lets_that_phone_in(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_PHONE_HANDOFF", raising=False)
    m, srv, base = _server(tmp_path, loopback=False)
    tok = sys.modules["phone_handoff"].mint()["token"]      # what the card's loopback POST /api/handoff hands out
    try:
        st, h = _call(base + "/api/handoff/claim", {"token": tok})
        assert st == 200
        cookie = h["Set-Cookie"].split(";", 1)[0]
        assert "HttpOnly" in h["Set-Cookie"]
        assert _call(base + "/api/agent", cookie=cookie)[0] == 200
        assert _call(base + "/api/agent", cookie="crissa_phone=forged")[0] == 401
    finally:
        srv.shutdown()


def test_this_computer_is_always_let_in(tmp_path):
    m, srv, base = _server(tmp_path, loopback=True)
    try:
        assert _call(base + "/api/agent")[0] == 200
        assert _call(base + "/api/floor", {"on": True})[0] == 200
    finally:
        srv.shutdown()


def test_the_code_lives_three_minutes_and_says_so():
    assert PH.TTL_S == 180.0
    assert "3 minutes" in PH.WHY["expired"]
    readme = (ROOT / "modules" / "phone" / "README.md").read_text(encoding="utf-8")
    assert "works once and for 3 minutes" in readme and "CRISSA_PHONE_PAIRING=0" in readme
