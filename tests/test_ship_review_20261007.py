"""Ship review 2026-10-07: the conductor answers this machine's pages only, and house-speak's voice fixes
(Turbo tags reach Turbo alone, a streamed voice asks its whole backup chain, a pack's buffered line is timed,
a cut is said)."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text()
SPEAK_PATH = ROOT / "scripts" / "house-speak.py"
SPEAK = SPEAK_PATH.read_text()


def _guard() -> dict:
    """The guard's pure functions, lifted from the conductor (importing it starts a player)."""
    tree = ast.parse(CONDUCTOR)
    want = {"_LOOPBACK_NAMES", "_loopback_url", "_loopback_hostport", "foreign_request"}
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in want)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in want for t in n.targets))]
    assert len(nodes) == len(want)
    ns: dict = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "guard", "exec"), ns)
    return ns


G = _guard()


@pytest.mark.parametrize("headers,why", [
    ({}, ""),                                                                    # curl, house-speak, the ear
    ({"Host": "127.0.0.1:8100"}, ""),
    ({"Host": "localhost:8100", "Origin": "http://localhost:8100"}, ""),        # her own /wall and /connect
    ({"Host": "[::1]:8100", "Origin": "http://[::1]:8100"}, ""),
    ({"Host": "127.0.0.1:8100", "Origin": "http://127.0.0.1:8099"}, ""),        # the glass on :8099
    ({"Host": "127.0.0.1:8100", "Sec-Fetch-Site": "same-site"}, ""),
    ({"Host": "127.0.0.1:8100", "Sec-Fetch-Site": "cross-site", "Referer": "http://localhost:8099/"}, ""),
    ({"Host": "127.0.0.1:8100", "Origin": "https://evil.example"}, "origin"),    # a fetch() from any site
    ({"Host": "127.0.0.1:8100", "Origin": "http://127.0.0.1.evil.example"}, "origin"),
    ({"Host": "127.0.0.1:8100", "Origin": "null"}, "origin"),
    ({"Host": "127.0.0.1:8100", "Sec-Fetch-Site": "cross-site"}, "cross-site"),  # a no-cors GET carries no Origin
    ({"Host": "127.0.0.1:8100", "Sec-Fetch-Site": "cross-site", "Referer": "https://evil.example/x"}, "cross-site"),
    ({"Host": "evil.example:8100"}, "host"),                                     # DNS rebinding: same-origin, no Origin
    ({"Host": "127.0.0.1.evil.example"}, "host"),
])
def test_only_this_machines_pages_reach_the_player(headers, why):
    assert G["foreign_request"](headers) == why


def test_the_guard_runs_before_any_route_and_a_pack_has_it_on():
    get = CONDUCTOR.split("    def do_GET(self) -> None:\n", 1)[1][:80]
    post = CONDUCTOR.split("    def do_POST(self) -> None:\n", 1)[1][:80]
    assert get.lstrip().startswith("if self._foreign():") and post.lstrip().startswith("if self._foreign():")
    line = [l for l in CONDUCTOR.splitlines() if l.startswith("LOOPBACK_GUARD = ")]
    assert len(line) == 1 and 'house_default("0", "1")' in line[0], "a pack ON, the house as it was"


def _speak():
    spec = importlib.util.spec_from_file_location("house_speak_ship", SPEAK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_cut_is_said_and_lands_on_a_sentence(capsys):
    hs = _speak()
    assert hs.cap_text("short.", 800, "voice") == "short."
    assert capsys.readouterr().err == ""
    line = ("One sentence here. " * 30) + "And a long tail with no stop " * 20
    out = hs.cap_text(line, 800, "voice")
    assert len(out) <= 800 and out.endswith(".")
    assert "line cut: %d -> %d chars (voice cap 800)" % (len(line), len(out)) in capsys.readouterr().err
    words = "word " * 300
    out = hs.cap_text(words, 800, "mouth")
    assert len(out) <= 800 and out.endswith("word"), "no sentence end: cut at a space, never mid-word"


def test_turbo_is_known_by_the_streamed_voice_in_a_pack(monkeypatch):
    hs = _speak()
    monkeypatch.delenv("HOUSE_SPEAK_FORCE_TURBO_TAGS", raising=False)
    monkeypatch.setitem(hs._STREAMED, "name", "turbo")
    assert hs.voice_is_turbo() is True
    monkeypatch.setitem(hs._STREAMED, "name", "pocket")
    assert hs.voice_is_turbo() is False
    monkeypatch.setenv("HOUSE_SPEAK_FORCE_TURBO_TAGS", "1")
    assert hs.voice_is_turbo() is True


def test_only_turbo_hears_the_tags():
    body = SPEAK.split("    turbo = voice_is_turbo()\n", 1)[1][:400]
    assert "spoken_plain = normalize_for_voice(text, keep_turbo_tags=False) if turbo else spoken" in body
    backup = SPEAK.split("def _try_backup()", 1)[1].split("\n    # THE VOICE SEAM", 1)[0]
    assert "_try_crissa_tts(spoken_plain," in backup and "_try_crissa_tts(spoken," not in backup
    assert "fn(spoken_plain, voice, cap_text)" in SPEAK      # a machine's own engine gets the plain line too
    assert "voice_pcm_robust(text, voice, keep_turbo_tags=turbo)" in SPEAK


def test_a_packs_buffered_line_is_timed_and_the_house_stays_untimed():
    body = SPEAK.split("def voice_pcm(", 1)[1][:400]
    assert '_voice_timeout(timeout, default="0" if _HOUSE else "90")' in body


def test_the_stream_keeps_its_own_45_s():
    body = SPEAK.split("def voice_stream_play(", 1)[1].split("\ndef ", 1)[0]
    assert '_voice_timeout(default="45")' in body, "one shared default had left the stream untimed"


def test_a_cut_line_is_kept_whole_and_the_player_is_told_once(tmp_path, monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Player(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            return

    srv = HTTPServer(("127.0.0.1", 0), Player)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        hs = _speak()
        monkeypatch.delenv("HOUSE_SPEAK_CUT_NOTE", raising=False)
        monkeypatch.setattr(hs, "_crissa_home", lambda: tmp_path)
        monkeypatch.setattr(hs, "_player_url", lambda p="": "http://127.0.0.1:%d%s" % (srv.server_port, p))
        line = "Every word she was asked to say. " * 40
        hs._CUT["text"] = line
        hs.cap_text(line, 800, "mouth")
        hs.cap_text(line, 800, "voice")                       # a second cut on the same line: logged, not re-told
    finally:
        srv.shutdown()
    files = list((tmp_path / "cut-text").glob("line-*.txt"))
    assert len(files) == 1 and files[0].read_text() == line, "the whole line, as asked"
    assert len(seen) == 1 and seen[0][0] == "/v1/cut"
    assert seen[0][1]["name"] == files[0].name and seen[0][1]["total"] == len(line) and seen[0][1]["what"] == "line"
    assert [c["where"] for c in hs._LINE["cuts"]] == ["mouth", "voice"], "the line log carries every cut"


def test_the_player_shows_a_speakers_cut_from_this_machine_only():
    route = CONDUCTOR.split('        if head == "/v1/cut":\n', 1)[1].split('        if head == "/v1/stop":', 1)[0]
    assert route.index("connect_origin_ok(self.headers)") < route.index("note_cut(")
    assert r're.fullmatch(r"[a-z]+-[0-9a-f]{12}\.txt", name)' in route and '"cut-text" / name).is_file()' in route
    assert CONDUCTOR.count('if head == "/v1/cut":') == 1
