"""Round 46 (the local review of kit crissa-063a2813, 2026-10-23): every server of a pack keeps the player's loopback
rule (lib/crissa_guard.py; its module copies are byte-equal, tests/test_round46_loopback_guard.py). Per server: a
foreign Origin, a foreign Host (DNS rebinding) and a cross-site no-Origin GET are refused 403 with no
`Access-Control-Allow-Origin: *`; a loopback request passes; CRISSA_LOOPBACK_GUARD=0 puts the old way back; a negative
Content-Length is refused before anything reads it. The house's own servers (the feed, the face shell, the phone) keep
today's way on the house (default off there). The feed's forwards to the player carry the caller's Origin; the phone's
loopback skip needs the rule, and a phone over the LAN still pairs."""
from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_home  # noqa: E402
from _kit import load as kit_load  # noqa: E402

IS_HOUSE = crissa_home.is_house()
EVIL = "https://evil.example"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _req(srv, method, path, headers=None, body=b"", cl=None, host=None):
    """One request with exactly these headers (Host included): (status, headers, body)."""
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host or "127.0.0.1:%d" % port)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if method == "POST":
        c.putheader("Content-Length", str(len(body)) if cl is None else str(cl))
    c.endheaders()
    if body:
        c.send(body)
    r = c.getresponse()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    c.close()
    return out


def _foreign_refused(srv, get_path="/round46-nope", post_path="/round46-nope"):
    """The three foreign callers are 403 and carry no ACAO *; a loopback GET passes the guard (not 403)."""
    port = srv.server_address[1]
    for kw in ({"headers": {"Origin": EVIL}},
               {"host": "evil.example:%d" % port},
               {"headers": {"Sec-Fetch-Site": "cross-site"}}):
        st, hd, _ = _req(srv, "GET", get_path, **kw)
        assert st == 403, kw
        assert hd.get("access-control-allow-origin") != "*"
    st, hd, _ = _req(srv, "POST", post_path, headers={"Origin": EVIL, "Content-Type": "application/json"}, body=b"{}")
    assert st == 403 and hd.get("access-control-allow-origin") != "*"
    st, _, _ = _req(srv, "GET", get_path, headers={"Origin": "http://127.0.0.1:8099"})
    assert st != 403
    st, _, _ = _req(srv, "GET", get_path, headers={"Sec-Fetch-Site": "cross-site", "Referer": "http://localhost:8100/"})
    assert st != 403


def _negative_length_refused(srv, path):
    st, _, _ = _req(srv, "POST", path, cl=-1)          # the old read was rfile.read(-1): this would hang to the timeout
    assert st in (400, 413)


# ---- the feed (:8099): the kit's copy --------------------------------------------------------------------------
# The house's scripts/voiceonly-home-server.py is prod-guarded (chattr +i live, its bytes pinned by
# tests/test_round17_feed_overlay_20261013.py): it keeps today's feed, which is what "off on the house" means for it.
# The kit ships crissa/overlay/scripts/voiceonly-home-server.py, which carries the guard.
def test_the_feed_is_one_file_with_the_guard():
    # the fold (2026-10-28): one feed for both editions; the guard is in it, off on the house (FEED_GUARD_DEFAULT)
    house = (ROOT / "scripts/voiceonly-home-server.py").read_text(encoding="utf-8")
    assert "crissa_guard" in house and 'FEED_GUARD_DEFAULT = "0" if crissa_home.is_house() else "1"' in house
    assert not (ROOT / "crissa/overlay/scripts/voiceonly-home-server.py").exists()


@pytest.fixture(scope="module")
def feed():
    m = kit_load("scripts/voiceonly-home-server.py", "vo_feed_r46_kit")
    srv = _serve(m.Handler)
    yield m, srv
    srv.shutdown()


def test_feed_house_default_is_off_on_the_house_and_on_in_a_pack(feed, monkeypatch):
    m, srv = feed
    assert m.FEED_GUARD_DEFAULT == ("0" if IS_HOUSE else "1")
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    monkeypatch.setattr(m, "FEED_GUARD_DEFAULT", "0")       # the house: today's feed
    assert _req(srv, "GET", "/round46-nope", headers={"Origin": EVIL})[0] == 404
    monkeypatch.setattr(m, "FEED_GUARD_DEFAULT", "1")       # a pack
    _foreign_refused(srv)


def test_feed_guard_on_refuses_and_the_kill_restores(feed, monkeypatch):
    m, srv = feed
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    _foreign_refused(srv)
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    assert _req(srv, "GET", "/round46-nope", headers={"Origin": EVIL})[0] == 404
    assert _req(srv, "GET", "/round46-nope", host="evil.example")[0] == 404


def test_feed_house_names_count_as_this_machine(feed, monkeypatch):
    m, srv = feed
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    monkeypatch.setattr(m, "FEED_GUARD_NAMES", ("wall.home.arpa", "panel.home.arpa"))
    assert _req(srv, "GET", "/round46-nope", host="wall.home.arpa:8099",
                headers={"Origin": "http://panel.home.arpa:8099"})[0] == 404


def test_feed_negative_length_refused(feed, monkeypatch):
    m, srv = feed
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    _negative_length_refused(srv, "/v1/pose/cue")


class _Player:
    """A stand-in player that keeps the headers of every request it is sent."""
    def __init__(self):
        from http.server import BaseHTTPRequestHandler
        seen = self.seen = []

        class P(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                seen.append((self.command, self.path, dict(self.headers)))
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                b = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            do_GET = do_POST = _answer
        self.srv = _serve(P)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]


def test_feed_forwards_the_callers_origin_to_the_player(feed, monkeypatch):
    m, srv = feed
    p = _Player()
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    monkeypatch.setattr(m, "PLAYER_INIT_URL", p.url)
    monkeypatch.setattr(m, "init_apply", lambda act, body=None: m._player_init("/v1/init", dict(body or {}, action=act)))
    st, _, _ = _req(srv, "POST", "/v1/init", headers={"Origin": "http://127.0.0.1:8099",
                                                      "Content-Type": "application/json"}, body=b'{"action": "x"}')
    assert st == 200
    assert p.seen[-1][1] == "/v1/init" and p.seen[-1][2].get("Origin") == "http://127.0.0.1:8099"
    # a caller with no Origin (curl, the Tk wall) forwards none: the player sees what the feed saw
    _req(srv, "POST", "/v1/init", headers={"Content-Type": "application/json"}, body=b'{"action": "x"}')
    assert "Origin" not in p.seen[-1][2]
    # with the feed's own guard killed, a foreign caller's Origin still reaches the player, whose guard then holds
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    _req(srv, "POST", "/v1/init", headers={"Origin": EVIL, "Content-Type": "application/json"}, body=b'{}')
    assert p.seen[-1][2].get("Origin") == EVIL
    # the forward's own kill
    monkeypatch.setattr(m, "FORWARD_ORIGIN", False)
    _req(srv, "POST", "/v1/init", headers={"Origin": EVIL, "Content-Type": "application/json"}, body=b'{}')
    assert "Origin" not in p.seen[-1][2]
    p.srv.shutdown()


def test_feed_security_forward_carries_the_origin_too():
    src = (ROOT / "scripts/voiceonly-home-server.py").read_text(encoding="utf-8")   # one file since the fold (2026-10-28)
    i = src.index("def security_set(")
    assert "caller_headers()" in src[i:src.index("\ndef ", i + 10)]


# ---- the phone (:8226) -------------------------------------------------------------------------------------------
@pytest.fixture()
def phone(tmp_path):
    m = _load("scripts/phone-app-server.py", "phone_app_server_r46")
    (tmp_path / "packs").mkdir()
    m.CFG.clear()
    m.CFG.update({"packs": str(tmp_path / "packs"), "lines": str(tmp_path), "pack": "x", "port": 0,
                  "floor_file": str(tmp_path / "phone-floor.json"), "session_file": str(tmp_path / "phone-session.json"),
                  "agent_file": str(tmp_path / "none.json"), "pairing": True, "pair_file": str(tmp_path / "pairing.json")})

    class Lan(m.H):                       # every request from another device on the Wi-Fi
        def setup(self):
            super().setup()
            self.client_address = ("192.0.2.5", 50000)
    here, lan = _serve(m.H), _serve(Lan)
    yield m, here, lan
    here.shutdown()
    lan.shutdown()


def test_phone_house_default_is_off_on_the_house(phone, monkeypatch):
    m, here, _ = phone
    assert m.GUARD_DEFAULT == ("0" if IS_HOUSE else "1")
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    monkeypatch.setattr(m, "GUARD_DEFAULT", "0")
    assert _req(here, "GET", "/api/packs", host="evil.example")[0] == 200       # today's server, on the house


def test_phone_loopback_skip_needs_the_rule(phone, monkeypatch):
    m, here, _ = phone
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    port = here.server_address[1]
    assert _req(here, "GET", "/api/packs")[0] == 200                             # the computer itself
    assert _req(here, "GET", "/api/pair/code")[0] == 200
    for kw in ({"host": "evil.example:%d" % port}, {"headers": {"Sec-Fetch-Site": "cross-site"}}):
        assert _req(here, "GET", "/api/packs", **kw)[0] == 401                    # another device: it needs pairing
        assert _req(here, "GET", "/api/pair/code", **kw)[0] == 403               # the computer's own keys: refused
    st, hd, _ = _req(here, "GET", "/api/packs", headers={"Origin": EVIL})
    assert st == 403 and hd.get("access-control-allow-origin") != "*"            # another site's page
    st, _, _ = _req(here, "POST", "/api/handoff", headers={"Origin": EVIL}, body=b"{}")
    assert st == 403
    assert _req(here, "POST", "/api/handoff", host="evil.example", body=b"{}")[0] == 403
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")                             # the kill
    assert _req(here, "GET", "/api/packs", host="evil.example")[0] == 200
    assert _req(here, "GET", "/api/pair/code", headers={"Origin": EVIL})[0] == 200


def test_phone_lan_pairing_still_works(phone, monkeypatch):
    m, here, lan = phone
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    lan_host = "192.0.2.10:8226"
    assert _req(lan, "GET", "/api/packs", host=lan_host)[0] == 401
    assert _req(lan, "GET", "/api/pair/code", host=lan_host)[0] == 403
    assert _req(lan, "GET", "/", host=lan_host)[0] in (200, 404)                 # the page loads to pair
    code = json.loads(_req(here, "GET", "/api/pair/code")[2])["code"]
    st, _, b = _req(lan, "POST", "/api/pair", host=lan_host, headers={"Origin": "http://" + lan_host},
                    body=json.dumps({"code": code, "name": "iPhone"}).encode())
    assert st == 200
    tok = json.loads(b)["token"]
    assert _req(lan, "GET", "/api/packs", host=lan_host, headers={"Authorization": "Bearer " + tok})[0] == 200
    st, _, b = _req(lan, "GET", "/api/connection", host=lan_host, headers={"Authorization": "Bearer " + tok})
    assert st == 200 and json.loads(b)["paired"] is True
    # the QR browser page (same-origin: Origin = its LAN Host) and the native app (no Origin) on the paired path
    assert _req(lan, "GET", "/api/packs", host=lan_host,
                headers={"Authorization": "Bearer " + tok, "Origin": "http://" + lan_host})[0] == 200
    assert _req(lan, "POST", "/api/floor", host=lan_host, headers={"Authorization": "Bearer " + tok},
                body=b'{"on": false}')[0] == 200
    assert _req(lan, "POST", "/api/floor", host=lan_host, body=b'{"on": false}')[0] == 401     # unpaired: pairing


def test_phone_negative_length_and_no_body_before_pairing(phone, monkeypatch):
    m, here, lan = phone
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    _negative_length_refused(here, "/api/say")
    _negative_length_refused(lan, "/api/say")
    # an unpaired device that promises a body and sends none is answered 401 at once: nothing is read first
    assert _req(lan, "POST", "/api/say", host="192.0.2.10:8226", cl=50)[0] == 401
    assert _req(here, "POST", "/api/say", cl=m.JSON_MAX_BYTES + max(m.HEAR_MAX_BYTES, m.MIC_MAX_BYTES))[0] == 413


# ---- the face shell (:8088) --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def face():
    # round 46: the house's face shell is prod-guarded (chattr +i live); the kit ships crissa/overlay's guarded copy
    import _kit
    m = _kit.load("scripts/face-shell-server.py", "face_shell_r46")
    srv = _serve(m.Handler)
    yield m, srv
    srv.shutdown()


def test_face_shell_guard(face, monkeypatch):
    m, srv = face
    assert m.GUARD_DEFAULT == ("0" if m.IS_HOUSE else "1")
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "1")
    _foreign_refused(srv, post_path="/v1/stage")
    for p in ("/v1/stage", "/v1/mode", "/v1/chalk"):
        assert _req(srv, "POST", p, headers={"Origin": EVIL}, body=b"{}")[0] == 403
    st, _, b = _req(srv, "POST", "/v1/chalk", headers={"Origin": "http://127.0.0.1:8099"},
                    body=json.dumps({"kind": "clear", "target": "chalk"}).encode())
    assert st == 200 and json.loads(b)["ok"]
    _negative_length_refused(srv, "/v1/chalk")
    _negative_length_refused(srv, "/v1/mode")
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    assert _req(srv, "POST", "/v1/chalk", headers={"Origin": EVIL},
                body=json.dumps({"kind": "clear", "target": "chalk"}).encode())[0] == 200


def test_face_shell_house_default_off(face, monkeypatch):
    m, srv = face
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    monkeypatch.setattr(m, "GUARD_DEFAULT", "0")
    assert _req(srv, "GET", "/round46-nope", headers={"Origin": EVIL})[0] == 404


# ---- the panel (:8097) -------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def panel():
    m = _load("scripts/crissa-panel-server.py", "crissa_panel_r46")
    srv = _serve(m.Handler)
    m.CFG = {"port": srv.server_address[1], "host": "127.0.0.1"}
    yield m, srv
    srv.shutdown()


def test_panel_guard(panel, monkeypatch):
    m, srv = panel
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)                  # on by default: the kit's alone
    _foreign_refused(srv)
    _negative_length_refused(srv, "/v1/action")
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    assert _req(srv, "GET", "/round46-nope", host="evil.example")[0] == 404


class _Hd:
    def __init__(self, headers):
        self.headers = headers


def test_panel_same_origin_needs_a_loopback_host(panel, monkeypatch):
    m, _ = panel
    rebind = _Hd({"Host": "evil.example:8097", "Origin": "http://evil.example:8097"})
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    assert m.Handler._same_origin(rebind) is False                               # DNS rebinding: same-origin, not here
    assert m.Handler._same_origin(_Hd({"Host": "127.0.0.1:8097", "Origin": "http://127.0.0.1:8097"})) is True
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    assert m.Handler._same_origin(rebind) is True                                # the kill: the old rule


# ---- the module servers ------------------------------------------------------------------------------------------
def _module_handler(rel, name):
    m = _load(rel, name)
    return m, (m.make_handler() if hasattr(m, "make_handler") else m.H)


@pytest.mark.parametrize("rel,post", [
    ("modules/voice/scripts/crissa-stt.py", "/stt"),
    ("modules/voice/scripts/crissa-asr.py", "/stt"),
    ("modules/voice/scripts/crissa-pocket.py", "/stream"),
    ("modules/voice/scripts/crissa-tts.py", "/v1/audio/speech"),
    ("modules/voice/scripts/crissa-turbo.py", "/stream"),
])
def test_voice_module_servers(rel, post, monkeypatch):
    m, h = _module_handler(rel, "r46_" + Path(rel).stem.replace("-", "_"))
    srv = _serve(h)
    try:
        monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)              # on by default: a module is the kit's
        _foreign_refused(srv, post_path=post)
        _negative_length_refused(srv, post)
        monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
        assert _req(srv, "GET", "/round46-nope", headers={"Origin": EVIL})[0] == 404
    finally:
        srv.shutdown()


def test_multilingual_serves_turbos_guarded_handler():
    src = (ROOT / "modules/voice/scripts/crissa-multilingual.py").read_text(encoding="utf-8")
    assert "crissa-turbo.py" in src and "class H(" not in src


def test_oracle_server(monkeypatch):
    m = _load("modules/oracle/scripts/crissa-oracle.py", "r46_oracle")
    got = {}

    class Orc:
        state = {"faces": []}

        def __init__(self, **k):
            pass

        def run(self):
            pass

        def stop(self):
            pass
    monkeypatch.setenv("CRISSA_NO_CAMERA", "1")                                  # main() sets it; restored after
    monkeypatch.setattr(m.setup, "face_env_python", lambda: None)
    monkeypatch.setattr(m.service, "Oracle", Orc)
    monkeypatch.setattr(m, "_bind", lambda name, port, h: got.setdefault("H", h) and None)
    assert m.main(["--no-camera"]) == 2
    srv = _serve(got["H"])
    try:
        monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
        port = srv.server_address[1]
        for kw in ({"headers": {"Origin": EVIL}}, {"host": "evil.example:%d" % port},
                   {"headers": {"Sec-Fetch-Site": "cross-site"}}):
            st, hd, _ = _req(srv, "GET", "/v1/oracle", **kw)
            assert st == 403 and hd.get("access-control-allow-origin") != "*"
        assert _req(srv, "GET", "/v1/oracle")[0] == 200
        monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
        assert _req(srv, "GET", "/v1/oracle", headers={"Origin": EVIL})[0] == 200
    finally:
        srv.shutdown()
