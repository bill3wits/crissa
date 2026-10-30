"""Round 36, 2026-10-20 (.101 ASK-20261020-0230, the User's calls): while a phone session is active her room is empty -
unpaused she walks out, paused it is a cut - and she walks back in when it ends or goes idle; the menu's "Your phone" is
a QR card carrying the phone server's hand-off; the phone pop-up is off unless switched on."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_phone_card as C  # noqa: E402
import crissa_phone_room as R  # noqa: E402


def test_the_session_file_is_the_one_signal(tmp_path):
    f = tmp_path / "phone-session.json"
    assert R.active(f) is False                                            # no file: no session
    f.write_text(json.dumps({"v": 1, "active_until": time.time() + 60}))
    assert R.active(f) is True
    f.write_text(json.dumps({"v": 1, "active_until": time.time() - 1, "ended": time.time()}))
    assert R.active(f) is False                                            # ended or idle
    f.write_text("{not json")
    assert R.active(f) is False


def test_out_and_back_once_each_and_the_log_lines():
    room = R.Room()
    out = room.step(True, False, False, now=100.0)
    assert out == ("out", "walked out") and R.line(out) == "on the phone: her room empty (walked out)"
    assert room.step(True, False, False, now=101.0) is None                # no repeat while it stays on
    assert room.step(False, False, False, now=105.0) is None, "round 37: not within 10 s of the last walk"
    back = room.step(False, False, False, now=110.5)
    assert back == ("back", "walk-in") and R.line(back) == "back from the phone: walk-in"
    assert room.step(True, False, False, now=115.0) is None and room.step(True, False, False, now=121.0) is not None
    cut = R.Room().step(True, True, False)
    assert R.line(cut) == "on the phone: her room empty (cut, paused)"


def test_setup_owns_the_picture():
    room = R.Room()
    assert room.step(True, False, True) is None and room.out is False


def test_kills_and_the_house(monkeypatch):
    monkeypatch.setattr(R.crissa_home, "is_house", lambda *a, **k: False)
    assert R.on({}) is True and R.on({"CRISSA_PHONE_EMPTY_ROOM": "0"}) is False
    assert R.popup_on({}) is False and R.popup_on({"CRISSA_PHONE_POPUP": "1"}) is True
    assert R.card_on({}) is True and R.card_on({"CRISSA_PHONE_QR_CARD": "0"}) is False
    monkeypatch.setattr(R.crissa_home, "is_house", lambda *a, **k: True)
    assert R.on({}) is False


def test_the_session_path_override(monkeypatch, tmp_path):
    assert R.session_path({"CRISSA_PHONE_SESSION": str(tmp_path / "s.json")}) == tmp_path / "s.json"


@pytest.fixture
def phone_server():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
            body = json.dumps({"ok": True, "url": "http://192.0.2.20:8226/?handoff=tok123", "token": "tok123",
                               "expires": time.time() + 600}).encode()
            self.send_response(200 if self.path == "/api/handoff" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], seen
    srv.shutdown()


VIEW = {"label": "Your phone: http://192.0.2.20:8226", "url": "http://192.0.2.20:8226",
        "how": "Open http://192.0.2.20:8226 on your phone (same Wi-Fi). If it does not open: sudo ufw allow from "
               "192.0.2.0/24 to any port 8226 proto tcp"}


def test_the_card_asks_the_phone_server_for_a_hand_off(phone_server, monkeypatch):
    base, seen = phone_server
    h = C.handoff(base)
    assert seen == [("/api/handoff", {"by": "wall"})] and h["url"].endswith("?handoff=tok123")
    monkeypatch.setattr(C, "qr_svg", lambda text: "<svg data-text='%s'></svg>" % text)
    c = C.card(VIEW, h)
    assert c["ok"] and c["qr_text"] == "http://192.0.2.20:8226/?handoff=tok123"
    page = C.page(c)
    assert "<svg" in page and "http://192.0.2.20:8226" in page
    assert "sudo ufw allow from 192.0.2.0/24 to any port 8226 proto tcp" in page
    assert "tok123" not in page.split("<div id=qr>")[0], "the token rides only in the code, not the shown address"


def test_no_phone_server_no_code_but_the_address_and_the_ufw_line():
    c = C.card(VIEW, C.handoff("http://127.0.0.1:9"))
    assert not c["ok"] and "not answering" in c["why"] and "<svg" not in C.page(c)
    assert "ufw allow" in C.page(c) and "http://192.0.2.20:8226" in C.page(c)


def test_no_phone_module_and_no_network():
    assert "no phone module" in C.card(None)["why"]
    assert "no network" in C.card({"label": "Your phone: this computer is on no network", "url": "", "how": ""})["why"]


def test_segno_draws_a_real_code_when_installed():
    pytest.importorskip("segno")
    svg = C.qr_svg("http://192.0.2.20:8226/?handoff=tok123")
    assert svg.lstrip().startswith("<svg") and "path" in svg


def test_the_player_and_the_wall_key_are_wired():
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    assert 'if head in ("/phone-card", "/phone-card/") and not IS_HOUSE and crissa_phone_room.card_on():' in src
    assert "threading.Thread(target=_phone_room_loop, args=(phone_home,)" in src     # round 37: never the first-boot clip
    assert src.index("    phone_home = rest\n") < src.index("k0 = crissa_init.boot_host_loop()")
    assert 'room.step(crissa_phone_room.active(path), _phone_paused(), crissa_init.needed())' in src
    assert 'return not bool(crissa_init.mic_state().get("on"))' in src         # a pack's pause: her microphone off
    loop = src.split("def _phone_room_loop(", 1)[1].split("\ndef face_beside_card(", 1)[0]
    # the trial cut: the line came after her ~45 s entrance, so .101's grep (and the acceptance) never saw the walk-in
    assert loop.index('action("phone", crissa_phone_room.line(act), "phone")') < loop.index("ph.play(home_key")
    assert loop.count("crissa_phone_room.line(act)") == 1
    key = (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
    assert 'url = ph_.get("url") if pop else (ph_.get("card") or "")' in key
    exp = (ROOT / "scripts" / "crissa-export.py").read_text(encoding="utf-8")
    assert "segno==1.6.6" in exp
    # the player and the wall install it; the launcher's base list (what it refuses to start without) does not carry it
    assert 'PLAYER_REQUIREMENTS = REQUIREMENTS + "segno==1.6.6\\n"' in exp
    assert '"crissa-avatar": PLAYER_REQUIREMENTS,' in exp and "WALL_REQUIREMENTS = PLAYER_REQUIREMENTS +" in exp


def test_the_code_scans_back_to_the_hand_off_address(tmp_path):
    segno = pytest.importorskip("segno")
    cv2 = pytest.importorskip("cv2")
    text = "http://192.0.2.20:8226/?handoff=tok123"
    assert C.qr_svg(text)                                                   # the card's own path draws
    png = tmp_path / "qr.png"
    segno.make(text, error="m", micro=False).save(str(png), scale=8, border=4)
    got, _pts, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(png)))
    assert got == text


def test_the_office_is_held_when_the_sequencer_is_already_on_it():
    """Cut 5 (2026-10-20): the walk-off ended ON her entrance, play() of the current clip returned False, nothing held."""
    import ast
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_phone_hold_office")

    class Ph:
        def __init__(self, key, plays):
            self.key, self._plays, self.seq = key, plays, type("S", (), {"camp": None})()

        def play(self, key, **kw):
            return self._plays

    for ph in (Ph("crissa-sit-hello", False), Ph("listen", True)):
        hold = {"key": None}
        ns = {"INIT_HOLD": hold}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "c", "exec"), ns)
        assert ns["_phone_hold_office"](ph, "crissa-sit-hello") and hold["key"] == "crissa-sit-hello"
    ns = {"INIT_HOLD": {"key": None}}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "c", "exec"), ns)
    assert not ns["_phone_hold_office"](Ph("listen", False), "crissa-sit-hello") and ns["INIT_HOLD"]["key"] is None


def test_the_walk_off_end_is_seen_even_when_it_loops():
    """Cut 6 (2026-10-20): polled once a second against a 125 ms end window, and camped on the walk-off, she looped it."""
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    loop = src.split("def _phone_room_loop(", 1)[1].split("\ndef face_beside_card(", 1)[0]
    assert 'time.sleep(0.1 if leaving["key"] else crissa_phone_room.TICK_S)' in loop
    assert 'wrapped = on_it and leaving["last"] >= 0 and int(fc[1]) < leaving["last"]' in loop
    assert 'if ph.key != leaving["key"] or wrapped or (on_it and fc[2] and fc[1] >= fc[2] - 3):' in loop


def test_no_pack_carries_the_phone_loops_provenance(tmp_path):
    """Round 36: the phone loops' rows are the release's (tools/phone_provenance.py), never a pack's."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_export_r36", ROOT / "scripts" / "crissa-export.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    src = json.loads((ROOT / "config" / "crissa-film-provenance.json").read_text(encoding="utf-8"))
    veo = "made with Google's Veo (the owner's statement, 2026-10-20)"
    wan = "made with Wan 3.0 (hosted wan.video, the owner's statement, 2026-10-21)"   # six flourishes ADDED 10-21
    rows = src["phone_films"]
    assert len(rows) == 22 and sum(r["status"] == veo for r in rows.values()) == 16
    assert {k for k, r in rows.items() if r["status"] == wan} == {
        "phone-packs/crissa-phone-fl-%s/loop.mp4" % n
        for n in ("hair-clump-up", "smile-blink", "hair-tuck", "earring-touch", "laugh-held", "hair-clump-down")}
    film = next(iter(src["films"]))
    (tmp_path / film).parent.mkdir(parents=True)
    (tmp_path / film).write_bytes(b"")
    assert ex.write_film_provenance(tmp_path) == 1
    out = json.loads((tmp_path / "FILM-PROVENANCE.json").read_text(encoding="utf-8"))
    assert "phone_films" not in out and "_about_phone" not in out and list(out["films"]) == [film]


def test_the_walk_in_ends_on_its_wrap_and_never_plays_twice():
    """Round 37 (.101 round 36 B: after a restart the walk-in played twice): 0.1 s polls and a wrap ends it."""
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    loop = src.split("def _phone_room_loop(", 1)[1].split("\ndef face_beside_card(", 1)[0]
    back = loop.split("ph.play(office, force=True, audio=False, deliberate=True)", 1)[1]
    assert "(on_it and last_f >= 0 and int(fc[1]) < last_f)" in back and "time.sleep(0.1)" in back


def test_the_card_closes_after_two_minutes_or_once_a_phone_is_on(monkeypatch):
    """Round 38 (the User: "can we close QR after a period?")."""
    monkeypatch.delenv("CRISSA_PHONE_CARD_CLOSE_S", raising=False)
    page = C.page({"qr": "<svg/>", "address": "http://192.0.2.10:8226", "how": "", "why": ""})
    assert "closes in 2 minutes or once your phone is on" in page and "setTimeout(function(){shut(false)},120000)" in page
    assert "fetch('/phone-card?state=1'" in page
    monkeypatch.setenv("CRISSA_PHONE_CARD_CLOSE_S", "0")
    assert "<script>" not in C.page({"qr": "<svg/>", "address": "x", "how": "", "why": ""})
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    route = src.split('if head in ("/phone-card", "/phone-card/")', 1)[1].split("return\n", 1)[0]
    assert route.index('"state=1"') < route.index("crissa_phone_card.handoff("), "a state poll never mints a hand-off"
