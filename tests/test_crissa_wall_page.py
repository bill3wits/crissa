"""The Wall page, her voice in it, and the port table (2026-10-05).

The player's two page handlers (_wall_get, _wall_post) are lifted out of the conductor by name and served
on a real loopback server over a real Store, so the routes tested are the routes that ship. house-speak's
page path runs its real paplay() against that server, with a stand-in page answering from a thread.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_page_audio as zpa  # noqa: E402
import crissa_ports  # noqa: E402

CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text()
HS = ROOT / "scripts" / "house-speak.py"


# ── the port table ────────────────────────────────────────────────────────────────────────────────────────

def test_ports_default_to_the_public_numbers():
    assert {k: crissa_ports.port(k, {}) for k in crissa_ports.DEFAULTS} == {
        "player": 8100, "asr": 8096, "stt": 8190, "align": 8195, "tts": 8189, "oracle": 8197, "turbo": 8186, "multilingual": 8185, "audio8": 8187, "pocket": 8188, "presence": 8219, "panel": 8097,
        "camera": 8192, "enroll": 8196, "feed": 8099, "bridge": 8194, "comfy": 8184, "demo": 8124,
        "chip": 8218, "face_shell": 8088, "browser": 8183, "phone": 8226, "knowledge": 8227}


def test_a_port_reads_its_own_variable_then_the_old_one_then_the_offset():
    env = {"CRISSA_PORT_OFFSET": "100"}
    assert crissa_ports.port("stt", env) == 8290
    assert crissa_ports.port("player", dict(env, HOUSE_CONDUCTOR_PORT="8150")) == 8150
    assert crissa_ports.port("player", dict(env, HOUSE_CONDUCTOR_PORT="8150", CRISSA_PORT_PLAYER="9000")) == 9000
    assert crissa_ports.port("align", {"CRISSA_ALIGN_PORT": "nonsense"}) == 8195
    assert crissa_ports.url("tts", {}) == "http://127.0.0.1:8189"


def test_no_two_pieces_share_a_port():
    """tts first took 8197, which the oracle has served since 2026-10-01 (caught by house-rails-d1, 10-05); 8193 is rail-voice's."""
    vals = list(crissa_ports.DEFAULTS.values())
    assert len(vals) == len(set(vals))
    import re
    # the voice and oracle scripts install on their own, so they repeat the table inline (2026-10-06): it must agree
    for rel in ("modules/oracle/scripts/crissa-oracle.py", "modules/voice/scripts/crissa-stt.py",
                "modules/voice/scripts/crissa-asr.py"):
        src = (ROOT / rel).read_text()
        got = dict((k, int(v)) for k, v in re.findall(r'"(\w+)": (\d+)', re.search(r"_DEFAULT_PORTS = \{[^}]*\}", src).group(0)))
        assert got == {k: crissa_ports.DEFAULTS[k] for k in got}, rel


def test_an_unknown_piece_is_an_error_not_a_free_default():
    with pytest.raises(KeyError):
        crissa_ports.port("sst", {})


def test_env_for_pins_every_piece_both_ways():
    e = crissa_ports.env_for({"player": 8200, "align": 8295})
    assert e == {"CRISSA_PORT_PLAYER": "8200", "CRISSA_PORT_ALIGN": "8295",
                 "HOUSE_CONDUCTOR_PORT": "8200", "CRISSA_ALIGN_PORT": "8295"}
    assert crissa_ports.port("player", e) == 8200


def test_in_use_sees_a_listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert crissa_ports.in_use(s.getsockname()[1])
    finally:
        s.close()


# ── the switch ────────────────────────────────────────────────────────────────────────────────────────────

def test_the_house_is_always_host(monkeypatch):
    monkeypatch.setattr(zpa, "_is_house", lambda: True)
    assert zpa.mode({"CRISSA_AUDIO_AT": "page"}) == "host"


def test_a_pack_is_host_unless_it_says_page(monkeypatch):
    monkeypatch.setattr(zpa, "_is_house", lambda: False)
    assert zpa.mode({}) == "host"
    assert zpa.mode({"CRISSA_AUDIO_AT": "speakers"}) == "host"
    assert zpa.mode({"CRISSA_AUDIO_AT": " Page "}) == "page"


def test_this_tree_is_the_house_so_its_conductor_never_serves_the_page():
    assert zpa._is_house() is True        # the house manifest names state_home
    assert 'WALL_PAGE = (os.environ.get("CRISSA_WALL_PAGE", "0").strip() == "1"\n             and not crissa_page_audio._is_house())' in CONDUCTOR


# ── the store ─────────────────────────────────────────────────────────────────────────────────────────────

class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_newest_line_wins_and_the_page_sees_the_cancel():
    s = zpa.Store()
    a = s.push(b"RIFFa", lead_s=0.4)
    b = s.push(b"RIFFb", lead_s=0.4)
    got = s.poll(0, 0)
    assert got["line"]["id"] == b["id"] and a["id"] in got["stopped"]
    assert s.wav(b["id"]) == b"RIFFb"


def test_an_answer_becomes_air_and_wakes_the_waiter():
    s = zpa.Store()
    lid = s.push(b"RIFF", lead_s=0.4)["id"]
    t = time.time() + 0.3
    threading.Timer(0.1, lambda: s.ack(lid, t, page="p1")).start()
    assert s.wait_air(lid, 2.0) == pytest.approx(t)
    assert s.meta(lid)["state"] == "playing" and s.meta(lid)["page"] == "p1"


def test_the_first_page_owns_the_line():
    s = zpa.Store()
    lid = s.push(b"RIFF")["id"]
    now = time.time()
    s.ack(lid, now + 0.2, page="p1")
    assert s.ack(lid, now + 0.5, page="p2")["page"] == "p1"
    assert s.wait_air(lid, 0) == pytest.approx(now + 0.2)


def test_an_answer_far_from_now_is_refused():
    s = zpa.Store()
    lid = s.push(b"RIFF")["id"]
    with pytest.raises(ValueError):
        s.ack(lid, time.time() + 60)
    with pytest.raises(KeyError):
        s.ack("L999", time.time())


def test_no_answer_and_a_cancel_both_mean_the_host_plays():
    s = zpa.Store()
    lid = s.push(b"RIFF")["id"]
    assert s.wait_air(lid, 0.05) is None
    threading.Timer(0.05, lambda: s.cancel()).start()
    assert s.wait_air(lid, 2.0) is None


def test_only_a_page_that_can_play_counts_as_watching():
    clk = _Clock()
    s = zpa.Store(clock=clk)
    assert not s.watching()
    s.poll(0, 0, page="p", ready=False)
    assert not s.watching()
    s.poll(0, 0, page="p", ready=True)
    assert s.watching()
    clk.t += zpa.WATCH_S + 0.1
    assert not s.watching()


def test_a_line_has_a_size_limit():
    s = zpa.Store()
    with pytest.raises(ValueError):
        s.push(b"")
    with pytest.raises(ValueError):
        s.push(b"x" * (zpa.MAX_BYTES + 1))


# ── the player's routes, lifted ───────────────────────────────────────────────────────────────────────────

def _method(cls: str, name: str):
    tree = ast.parse(CONDUCTOR)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _func(name: str):
    tree = ast.parse(CONDUCTOR)
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _handler_class(store, stt_url: str = "http://127.0.0.1:9"):
    """The conductor's real page handlers on a minimal handler, dispatched as do_GET/do_POST dispatch them."""
    body = [_method("Handler", n) for n in ("_cors", "_json", "_page", "_wall_get", "_wall_post")]
    mod = ast.Module(body=[_func("connect_origin_ok"), _func("page_mic_forward"),
                           ast.ClassDef(name="_H", bases=[], keywords=[], body=body, decorator_list=[])],
                     type_ignores=[])
    ports = type("P", (), {"url": staticmethod(lambda n: stt_url)})
    ns = {"json": json, "time": time, "urllib": urllib, "ROOT": ROOT, "PAGE_AUDIO": store, "WALL_PAGE": True,
          "MIC_CHUNK_MAX": 256 * 1024, "crissa_ports": ports, "Path": Path}
    exec(compile(ast.fix_missing_locations(mod), "house-conductor.py", "exec"), ns)
    base = ns["_H"]

    class H(base, BaseHTTPRequestHandler):
        played = []

        def log_message(self, *a):
            pass

        def _director_play(self):     # the page load plays the director's work loop (15138a58f); the player's own
            H.played.append(self.path)  # method, not lifted here: the test counts the call

        def do_GET(self):
            head = self.path.split("?")[0]
            if head in ("/wall", "/wall/", "/v1/clock") or head.startswith("/v1/page-audio"):
                return self._wall_get(head)
            self._json(404, {})

        def do_POST(self):
            head = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            return self._wall_post(head, self.rfile.read(n) if n > 0 else b"")
    return H


def test_the_conductor_dispatches_its_page_routes_behind_the_switches():
    assert ('if WALL_PAGE and head in ("/wall", "/wall/", "/v1/clock") or (PAGE_AUDIO is not None and '
            'head.startswith("/v1/page-audio")):\n            return self._wall_get(head)') in CONDUCTOR
    assert 'wall_raw = WALL_PAGE and (head == "/v1/mic" or head.startswith("/v1/page-audio"))' in CONDUCTOR
    assert "PAGE_AUDIO = crissa_page_audio.Store() if (WALL_PAGE and crissa_page_audio.enabled()) else None" in CONDUCTOR
    assert "    if PAGE_AUDIO is not None:      # a line playing in the Wall page stops" in CONDUCTOR


@contextlib.contextmanager
def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _req(url, data=None, headers=None, method=None, timeout=5):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_the_page_and_the_clock_are_served():
    H = _handler_class(zpa.Store())
    with _serve(H) as base:
        code, raw = _req(base + "/wall")
        assert code == 200 and b"<title>Crissa Wall</title>" in raw
        assert H.played == ["/wall"]
        code, raw = _req(base + "/v1/clock")
        got = json.loads(raw)
        assert code == 200 and abs(got["t"] - time.time()) < 2 and got["audio_at"] == "page"


def test_a_line_goes_round_the_routes():
    store = zpa.Store()
    with _serve(_handler_class(store)) as base:
        c = zpa.Client(base)
        assert c.watching() is False
        _req(base + "/v1/page-audio?v=0&wait=0&page=p1&ready=1")
        assert c.watching() is True
        lid = c.push(b"RIFFdata", 0.4, "hello")
        code, raw = _req(base + "/v1/page-audio?v=0&wait=0&page=p1")
        line = json.loads(raw)["line"]
        assert line["id"] == lid and line["state"] == "queued" and line["text"] == "hello"
        assert _req(base + "/v1/page-audio/%s.wav" % lid) == (200, b"RIFFdata")
        start = time.time() + 0.2
        threading.Timer(0.1, lambda: _req(base + "/v1/page-audio/%s/air" % lid,
                                          data=json.dumps({"start_at": start, "page": "p1"}).encode(),
                                          headers={"Content-Type": "application/json"})).start()
        assert c.wait_air(lid, 2.0) == pytest.approx(start)
        c.done(lid)
        assert store.meta(lid)["state"] == "done"


def test_a_foreign_page_cannot_write():
    with _serve(_handler_class(zpa.Store())) as base:
        code, _ = _req(base + "/v1/page-audio", data=b"RIFF", headers={"Origin": "https://evil.example"})
        assert code == 403
        code, _ = _req(base + "/v1/mic", data=b"\0\0", headers={"Origin": "https://evil.example"})
        assert code == 403
        code, _ = _req(base + "/v1/page-audio?v=0&wait=0&page=x&ready=1", headers={"Origin": "https://evil.example"})
        assert code == 403
        code, _ = _req(base + "/v1/page-audio/L1.wav", headers={"Origin": "https://evil.example"})
        assert code == 403


def test_a_bad_answer_is_a_400():
    store = zpa.Store()
    with _serve(_handler_class(store)) as base:
        lid = zpa.Client(base).push(b"RIFF", 0.4)
        code, _ = _req(base + "/v1/page-audio/%s/air" % lid, data=b'{"start_at": "soon"}')
        assert code == 400
        code, _ = _req(base + "/v1/page-audio/L77/air", data=json.dumps({"start_at": time.time()}).encode())
        assert code == 404


def test_the_microphone_reaches_the_voice_module():
    seen = []

    class Stt(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.path, self.headers.get("X-Sample-Rate"), self.rfile.read(n)))
            raw = b'{"ok": true, "frames": 1}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with _serve(Stt) as stt, _serve(_handler_class(zpa.Store(), stt_url=stt)) as base:
        code, raw = _req(base + "/v1/mic", data=b"\x01\x00" * 480, headers={"X-Sample-Rate": "16000"})
        assert code == 200 and json.loads(raw)["frames"] == 1
        assert seen == [("/v1/mic", "16000", b"\x01\x00" * 480)]
        code, _ = _req(base + "/v1/mic", data=b"\x01\x00\x02")     # odd: not 16-bit
        assert code == 400


def test_no_voice_module_is_a_503_not_a_crash():
    with _serve(_handler_class(zpa.Store(), stt_url="http://127.0.0.1:9")) as base:
        code, raw = _req(base + "/v1/mic", data=b"\0\0")
        assert code == 503 and b"not listening" in raw


def test_host_mode_has_no_page_audio_routes():
    with _serve(_handler_class(None)) as base:
        code, _ = _req(base + "/v1/page-audio", data=b"RIFF")
        assert code == 404
        code, _ = _req(base + "/v1/mic", data=b"\0\0")
        assert code == 404


# ── house-speak's page path ───────────────────────────────────────────────────────────────────────────────

def _house_speak():
    spec = importlib.util.spec_from_file_location("house_speak_page", HS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wav(path: Path, secs: float = 0.3, sr: int = 24000) -> bytes:
    pcm = b"\x10\x00" * int(sr * secs)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00" * int(sr * 0.4) * 2 + pcm)
    return pcm


def _quiet(hs, monkeypatch, played):
    monkeypatch.setattr(hs, "pa_yield", lambda *a, **k: False)
    monkeypatch.setattr(hs, "hold_until_not_before", lambda *a, **k: None)
    monkeypatch.setattr(hs, "_speech_lock_cm", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(hs, "MOUTH_AIR", False)
    monkeypatch.setattr(hs, "_za", lambda: None)
    airs = []
    monkeypatch.setattr(hs, "_mouth_air", lambda on, **k: airs.append((on, k)))

    class R:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, *a, **k):
        played.append(list(argv))
        return R()
    monkeypatch.setattr(hs.subprocess, "run", fake_run)
    monkeypatch.setattr(hs, "_boost_paplay", lambda *a, **k: None)
    return airs


def test_a_pack_line_plays_in_the_page_and_takes_its_air_from_the_page(monkeypatch, tmp_path):
    hs = _house_speak()
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setattr(zpa, "_is_house", lambda: False)
    monkeypatch.setenv("CRISSA_AUDIO_AT", "page")
    store = zpa.Store()
    path = tmp_path / "line.wav"
    pcm = _wav(path)
    page_said = {}

    def page():                           # a stand-in page: watches, takes the line, answers 0.2 s ahead
        v = 0
        while "start" not in page_said:
            got = store.poll(v, 0.2, page="p1", ready=True)
            v = got["v"]
            if got["line"] and got["line"]["state"] == "queued":
                page_said["wav"] = store.wav(got["line"]["id"])
                page_said["start"] = time.time() + 0.2
                store.ack(got["line"]["id"], page_said["start"], page="p1")
    store.poll(0, 0, page="p1", ready=True)
    th = threading.Thread(target=page, daemon=True)
    th.start()
    played = []
    airs = _quiet(hs, monkeypatch, played)
    with _serve(_handler_class(store)) as base:
        monkeypatch.setenv("HOUSE_CONDUCTOR", base)
        t0 = time.time()
        hs.paplay(path, "sink", pcm=pcm, sr=24000)
        took = time.time() - t0
    assert not any(a and a[0] == "paplay" for a in played)    # the host played nothing
    assert page_said["wav"] == path.read_bytes()                # the page got the file the host would have read
    on = [k for o, k in airs if o][0]
    assert on["air_at"] == pytest.approx(page_said["start"] + hs.LEAD_SILENCE_MS / 1000.0)
    assert took >= 0.2 + 0.7 - 0.05                             # held to the end of the line (0.4 pad + 0.3 voice)
    assert hs._LINE["path"] == "page"
    assert [m["state"] for m in (store.meta(k) for k in store.order)] == ["done"]


def test_no_page_watching_and_the_host_plays_it(monkeypatch, tmp_path):
    hs = _house_speak()
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setattr(zpa, "_is_house", lambda: False)
    monkeypatch.setenv("CRISSA_AUDIO_AT", "page")
    store = zpa.Store()
    path = tmp_path / "line.wav"
    pcm = _wav(path)
    played = []
    _quiet(hs, monkeypatch, played)
    with _serve(_handler_class(store)) as base:
        monkeypatch.setenv("HOUSE_CONDUCTOR", base)
        hs.paplay(path, "sink", pcm=pcm, sr=24000)
    assert any(a and a[0] == "paplay" for a in played)
    assert store.order == []                                    # nothing was pushed


def test_a_page_that_does_not_answer_hands_the_line_back(monkeypatch, tmp_path):
    hs = _house_speak()
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setattr(zpa, "_is_house", lambda: False)
    monkeypatch.setenv("CRISSA_AUDIO_AT", "page")
    monkeypatch.setattr(hs, "PAGE_ACK_S", 0.3)
    # the page stays watching for the whole test: on a loaded CI runner the gap between this one poll and the line
    # outlived the 4 s window, nothing was sent, and the store had no line to cancel (1.0.4 tag run, 1.0.5 main run)
    monkeypatch.setattr(zpa, "WATCH_S", 600.0)
    store = zpa.Store()
    store.poll(0, 0, page="p1", ready=True)                     # watching, but never answers
    path = tmp_path / "line.wav"
    pcm = _wav(path)
    played = []
    _quiet(hs, monkeypatch, played)
    with _serve(_handler_class(store)) as base:
        monkeypatch.setenv("HOUSE_CONDUCTOR", base)
        hs.paplay(path, "sink", pcm=pcm, sr=24000)
    assert any(a and a[0] == "paplay" for a in played)
    assert store.meta(store.order[0])["state"] == "cancelled"


def test_the_house_never_asks_for_a_page(monkeypatch, tmp_path):
    hs = _house_speak()
    assert hs._HOUSE is True
    monkeypatch.setenv("CRISSA_AUDIO_AT", "page")
    assert hs._page_client() is None
    called = []
    monkeypatch.setattr(hs, "_page_send", lambda *a, **k: called.append(a))
    path = tmp_path / "line.wav"
    pcm = _wav(path)
    played = []
    _quiet(hs, monkeypatch, played)
    hs.paplay(path, "sink", pcm=pcm, sr=24000)
    assert called == [] and any(a and a[0] == "paplay" for a in played)


# ── one switch for what leaves (kill-search, 2026-10-05) ──────────────────────────────────────────────────────

def test_the_page_calls_go_through_the_one_switch():
    """Loopback by design, still routed through crissa_egress so the switch covers every call (house-rails-d1)."""
    for rel in ("lib/crissa_page_audio.py", "scripts/crissa.py"):
        assert "urllib.request.urlopen(" not in (ROOT / rel).read_text(), rel
    i = CONDUCTOR.index("def page_mic_forward(")
    block = CONDUCTOR[i:i + 1200]
    assert 'crissa_egress.urlopen(req, 2.0, "the voice module")' in block and "urllib.request.urlopen(" not in block


def test_with_kill_search_on_the_loopback_still_works_and_off_the_lan_is_refused(monkeypatch):
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    import crissa_egress
    assert crissa_egress.killed()
    store = zpa.Store()
    with _serve(_handler_class(store)) as base:
        c = zpa.Client(base)
        assert c.push(b"RIFF", 0.4) is not None                 # 127.0.0.1: on the LAN, allowed
    far = zpa.Client("http://203.0.113.9:8100", timeout=0.5)    # TEST-NET-3: never on anyone's LAN
    t0 = time.time()
    assert far.push(b"RIFF", 0.4) is None and far.watching() is False
    assert time.time() - t0 < 0.4                               # refused before any connection was tried
