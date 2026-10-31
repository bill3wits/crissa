"""The QR is the whole story for a phone (the User 2026-10-20, "get the app and mobile QR full features into 36"):
with pairing on, the desk Wall's single-use QR pairs the phone (a cookie the page's video and pack requests carry, a
token for the app's Keychain), Disconnect gives the pairing back, and a refused QR says why."""
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
import phone_pairing as PP  # noqa: E402


def test_a_refused_qr_says_why():
    t = PH.mint(now=1000.0)["token"]
    assert PH.claim_why(t, now=1001.0) == "ok" and PH.claim_why(t, now=1002.0) == "used"
    late = PH.mint(now=1000.0)["token"]
    assert PH.claim_why(late, now=1000.0 + PH.TTL_S + 1) == "expired" and PH.claim_why(late, now=1000.0 + PH.TTL_S + 2) == "expired"
    assert PH.claim_why("from-elsewhere") == "unknown" and PH.claim_why("") == "unknown"
    assert set(PH.WHY) == {"used", "expired", "unknown"} and all("new one" in v for v in PH.WHY.values())


def test_an_old_unclaimed_qr_is_remembered_as_expired():
    t = PH.mint(now=5000.0)["token"]
    PH.mint(now=5000.0 + PH.TTL_S + 1)                    # the next card sweeps it
    assert PH.claim_why(t, now=5000.0 + PH.TTL_S + 2) == "expired"


def test_the_claim_grants_and_a_phone_forgets_only_itself(tmp_path):
    f = tmp_path / "pairing.json"
    a, b = PP.grant(f, "phone (QR)"), PP.grant(f, "other")
    assert PP.check(f, a) and PP.check(f, b)
    assert PP.forget(f, a) and not PP.check(f, a) and PP.check(f, b)
    assert not PP.forget(f, a) and not PP.forget(f, "")
    assert json.loads(f.read_text())["devices"][__import__("hashlib").sha256(b.encode()).hexdigest()]["via"] == "qr"


def _server(tmp_path, pairing=True):
    spec = importlib.util.spec_from_file_location("phone_server_qr", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CFG.update({"pairing": pairing, "pair_file": str(tmp_path / "pairing.json"), "session_file": str(tmp_path / "s.json"),
                  "floor_file": str(tmp_path / "f.json"), "packs": str(tmp_path), "pack": "x", "lines": str(tmp_path / "l")})
    m.H._loopback = lambda self: getattr(self, "_test_loopback", False)       # every test request is "another device"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return m, srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _call(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.load(r), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.load(e), e.headers


def test_a_qr_claim_pairs_the_page_by_cookie_and_the_app_by_token(tmp_path):
    m, srv, base = _server(tmp_path)
    try:
        code, got, _ = _call(base + "/api/connection")
        assert code == 200 and got["pairing"] is True and got["paired"] is False   # answered before pairing
        assert _call(base + "/api/show")[0] == 401
        tok = PH.mint()["token"]
        code, got, hdr = _call(base + "/api/handoff/claim", {"token": tok})
        assert code == 200 and got["paired"] is True and "token" not in got
        cookie = hdr["Set-Cookie"]
        assert cookie.startswith(m.PAIR_COOKIE + "=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
        jar = {"Cookie": cookie.split(";", 1)[0]}
        assert _call(base + "/api/connection", headers=jar)[1]["paired"] is True
        assert _call(base + "/api/show", headers=jar)[0] == 200           # the page's requests, video included, carry it
        code, got, _ = _call(base + "/api/handoff/claim", {"token": PH.mint()["token"], "want_token": True, "name": "my phone"})
        assert got["token"] and _call(base + "/api/show", headers={"Authorization": "Bearer " + got["token"]})[0] == 200
        code, got, hdr = _call(base + "/api/handoff/leave", {}, headers=jar)
        assert got["forgot"] is True and "Max-Age=0" in hdr["Set-Cookie"]
        assert _call(base + "/api/show", headers=jar)[0] == 401
        code, got, _ = _call(base + "/api/handoff/claim", {"token": tok})
        assert code == 403 and got["why"] == "used" and got["error"] == PH.WHY["used"]
    finally:
        srv.shutdown()


def test_pairing_off_the_claim_sets_nothing(tmp_path):
    m, srv, base = _server(tmp_path, pairing=False)
    try:
        code, got, hdr = _call(base + "/api/handoff/claim", {"token": PH.mint()["token"], "want_token": True})
        assert code == 200 and got["paired"] is False and "token" not in got and not hdr.get("Set-Cookie")
        assert _call(base + "/api/connection")[1]["paired"] is None
        assert not (tmp_path / "pairing.json").exists()
    finally:
        srv.shutdown()


def test_the_page_waits_for_the_pairing_and_gives_it_back():
    js = (ROOT / "display" / "phone-app" / "app.js").read_text()
    html = (ROOT / "display" / "phone-app" / "index.html").read_text()
    assert "if (mayUse()) start();" in js and "checkConnection();\n" in js     # she loads only once she may
    assert 'fetch("/api/handoff/leave"' in js and 'id="connectWhy"' in html
    assert '"Scan the QR code on your computer to connect this phone"' in js


def test_an_empty_cells_object_is_not_a_painted_clip():
    js = (ROOT / "display" / "phone-app" / "app.js").read_text()
    assert "const hasMouths = (m) => !!(m && m.cells && Object.keys(m.cells).length);" in js
    assert "function painted(k) { return (hasMouths(packs[k]) && TALK_FLOURISH_P > 0) || k === show.talk; }" in js
    assert "if (painted(k) && hasMouths(m)) {" in js
