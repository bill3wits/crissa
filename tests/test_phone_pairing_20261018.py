"""Pairing a phone with Crissa on the computer (the User 2026-10-18, the iPhone app's M1): a 6-digit code shown on
the computer pairs one phone once, the phone keeps a token, the computer keeps only its hash; with pairing on another
device reaches /api and /pack only with that token, the computer itself never needs one. Off by default."""
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import phone_pairing as PP  # noqa: E402


def _reset():
    PP._TRIES.update(n=0, until=0.0)


def test_a_code_pairs_one_phone_once_and_only_the_hash_is_kept(tmp_path):
    _reset()
    f = tmp_path / "pairing.json"
    c = PP.new_code(f, now=1000.0)
    assert len(c["code"]) == 6 and c["code"].isdigit()
    r = PP.pair(f, c["code"], "My iPhone", now=1010.0)
    assert r["ok"] and len(r["token"]) >= 40
    assert r["token"] not in f.read_text() and c["code"] not in f.read_text()
    assert PP.check(f, r["token"]) and not PP.check(f, "nope") and not PP.check(f, "")
    assert PP.pair(f, c["code"], "second", now=1020.0)["ok"] is False          # used once
    assert [d["name"] for d in PP.devices(f)] == ["My iPhone"]
    assert oct(f.stat().st_mode)[-3:] == "600"


def test_an_old_code_and_five_wrong_ones_are_refused(tmp_path):
    _reset()
    f = tmp_path / "pairing.json"
    c = PP.new_code(f, now=0.0)
    assert PP.pair(f, c["code"], "x", now=PP.CODE_TTL_S + 1)["ok"] is False
    c = PP.new_code(f, now=5000.0)
    wrong = "%06d" % ((int(c["code"]) + 1) % 1_000_000)
    for _ in range(PP.MAX_TRIES):
        PP.pair(f, wrong, "x", now=5001.0)
    locked = PP.pair(f, c["code"], "x", now=5002.0)
    assert locked["ok"] is False and locked.get("locked")                     # even the right code waits out the lock
    assert PP.pair(f, c["code"], "x", now=5002.0 + PP.LOCK_S)["ok"] is True


def test_unpair_forgets_one_or_all(tmp_path):
    _reset()
    f = tmp_path / "pairing.json"
    toks = []
    for name in ("a", "b"):
        toks.append(PP.pair(f, PP.new_code(f)["code"], name)["token"])
    one = PP.devices(f)[0]["id"]
    assert PP.unpair(f, one) == 1 and not PP.check(f, toks[0]) and PP.check(f, toks[1])
    assert PP.unpair(f) == 1 and PP.devices(f) == []


def _server(tmp_path, pairing, remote):
    spec = importlib.util.spec_from_file_location("phone_app_server_pair", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    (tmp_path / "packs").mkdir(exist_ok=True)
    m.CFG.clear()
    m.CFG.update({"packs": str(tmp_path / "packs"), "lines": str(tmp_path), "pack": "x", "port": 0,
                  "floor_file": str(tmp_path / "phone-floor.json"), "session_file": str(tmp_path / "phone-session.json"),
                  "agent_file": str(tmp_path / "none.json"), "pairing": pairing, "pair_file": str(tmp_path / "pairing.json")})
    if remote:
        m.H._loopback = lambda self: False                       # every request acts as another device on the Wi-Fi
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return m, srv


def _req(srv, path, body=None, token=""):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (srv.server_address[1], path),
                               data=json.dumps(body).encode() if body is not None else None, headers=h,
                               method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=5) as x:
            return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_pairing_off_is_as_before(tmp_path):
    _reset()
    m, srv = _server(tmp_path, pairing=False, remote=True)
    try:
        assert _req(srv, "/api/agent")[0] == 200
        assert _req(srv, "/api/pair/code")[0] == 403                          # a code is the computer's, always
    finally:
        srv.shutdown()


def test_pairing_on_another_device_needs_its_token(tmp_path):
    _reset()
    m, srv = _server(tmp_path, pairing=True, remote=True)
    try:
        code, r = _req(srv, "/api/agent")
        assert code == 401 and r.get("pair") is True
        assert _req(srv, "/api/pair/code")[0] == 403
        c = PP.new_code(Path(m.CFG["pair_file"]))["code"]                      # shown on the computer
        code, r = _req(srv, "/api/pair", {"code": c, "name": "phone"})
        assert code == 200 and r["ok"]
        assert _req(srv, "/api/agent", token=r["token"])[0] == 200
        assert _req(srv, "/api/agent", token="wrong")[0] == 401
        assert _req(srv, "/api/mb/abc", {"x": 1}, token=r["token"])[0] == 403   # only her voice on the computer posts a sheet
    finally:
        srv.shutdown()


def test_the_computer_itself_never_needs_a_token_and_gets_the_code(tmp_path):
    _reset()
    m, srv = _server(tmp_path, pairing=True, remote=False)
    try:
        assert _req(srv, "/api/agent")[0] == 200
        code, r = _req(srv, "/api/pair/code")
        assert code == 200 and len(r["code"]) == 6 and r["pairing"] is True
        assert _req(srv, "/api/pair/devices")[1]["devices"] == []
    finally:
        srv.shutdown()


def test_voice_in_relays_the_audio_to_this_computers_stt_and_returns_the_words(tmp_path):
    """The iPhone app's push to talk / open mic (the User 2026-10-18): POST /api/hear with 16-bit mono PCM; the phone
    server sends it to the /stt service on the computer and answers the words. Behind the pairing gate like any /api."""
    from http.server import BaseHTTPRequestHandler
    got = {}

    class STT(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.update(n=len(self.rfile.read(n)), rate=self.headers.get("X-Sample-Rate"))
            b = json.dumps({"text": " hello there ", "decode_ms": 5}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    stt = ThreadingHTTPServer(("127.0.0.1", 0), STT)
    threading.Thread(target=stt.serve_forever, daemon=True).start()
    _reset()
    m, srv = _server(tmp_path, pairing=True, remote=True)
    m.CFG["asr"] = "http://127.0.0.1:%d/stt" % stt.server_address[1]
    try:
        pcm = b"\x00\x01" * 16000
        r = urllib.request.Request("http://127.0.0.1:%d/api/hear" % srv.server_address[1], data=pcm, method="POST",
                                   headers={"X-Sample-Rate": "16000"})
        try:
            urllib.request.urlopen(r, timeout=5)
            assert False, "an unpaired phone was heard"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        tok = PP.pair(Path(m.CFG["pair_file"]), PP.new_code(Path(m.CFG["pair_file"]))["code"], "p")["token"]
        r.add_header("Authorization", "Bearer " + tok)
        with urllib.request.urlopen(r, timeout=5) as x:
            out = json.load(x)
        assert out["ok"] and out["text"] == "hello there" and got == {"n": 32000, "rate": "16000"}
    finally:
        srv.shutdown(); stt.shutdown()


def test_bonjour_uses_the_systems_own_announcer_and_says_when_there_is_none(monkeypatch):
    import phone_bonjour as PB
    mac = PB.command(8226, "Crissa on desk", which=lambda t: "/usr/bin/dns-sd" if t == "dns-sd" else None)
    assert mac == ["dns-sd", "-R", "Crissa on desk", "_crissa._tcp", "local", "8226", "path=/", "app=crissa"]
    lin = PB.command(8226, "Crissa on desk", which=lambda t: "/usr/bin/x" if t == "avahi-publish-service" else None)
    assert lin[:4] == ["avahi-publish-service", "Crissa on desk", "_crissa._tcp", "8226"]
    assert PB.command(8226, which=lambda t: None) is None
    said = []
    monkeypatch.setattr(PB, "command", lambda port, name="": None)
    assert PB.start(8226, log=said.append) is None and "pairs by this computer's address" in said[0]
    monkeypatch.setenv("CRISSA_PHONE_BONJOUR", "0")
    assert PB.start(8226, log=said.append) is None and len(said) == 1
    src = (ROOT / "scripts" / "phone-app-server.py").read_text()
    assert "if a.pairing:" in src and "phone_bonjour.start(a.port)" in src           # only in paired mode
