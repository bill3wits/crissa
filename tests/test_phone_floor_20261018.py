"""The phone has the floor, or the desk does (the User 2026-10-18 18:2x, "one or the other from that button"): the phone's
talk button takes the floor and keeps it while her answer plays on the phone; while it is held the computer's ear drops
what it hears before decoding it, so the desk Crissa never answers the phone. lib/phone_floor + the phone server's
/api/floor + the kit ear's veto_first.phone_floor_held."""
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
sys.path.insert(0, str(ROOT / "modules" / "ear"))
import phone_floor as PF  # noqa: E402
from crissa_ear import veto_first as VF  # noqa: E402


def test_a_take_holds_for_its_time_a_refresh_extends_and_a_release_leaves_a_short_tail(tmp_path):
    f = tmp_path / "phone-floor.json"
    d = PF.take(f, now=100.0)
    assert PF.held(d, 100.0) and d["since"] == 100.0 and d["until"] == 100.0 + PF.TTL_S
    assert not PF.held(PF.read(f), 100.0 + PF.TTL_S + 0.1)                    # a quiet holder loses it by itself
    PF.take(f, 30.0, now=101.0)                                                # her line: its length and a little
    assert PF.take(f, now=102.0)["until"] == 131.0                             # a refresh never shortens a longer hold
    assert PF.read(f)["since"] == 100.0                                        # one hold, one start
    r = PF.release(f, now=110.0)
    assert r["until"] == 110.0 + PF.TAIL_S and not PF.held(r, 110.0 + PF.TAIL_S + 0.01)
    assert PF.take(f, now=200.0)["since"] == 200.0                             # a new hold after it lapsed
    assert PF.take(f, 10_000.0, now=300.0)["until"] == 300.0 + PF.MAX_HOLD_S


def test_the_desk_ear_drops_what_it_heard_while_the_phone_had_the_floor(monkeypatch):
    fl = {"on": True, "since": 100.0, "until": 110.0}
    assert VF.phone_floor_held(3.0, now=105.0, floor=fl)                      # he spoke while the button was held
    assert VF.phone_floor_held(3.0, now=114.0, floor=fl)                      # flushed after: its words began inside
    assert not VF.phone_floor_held(2.0, now=120.0, floor=fl)                  # after the floor: the desk's turn
    assert not VF.phone_floor_held(3.0, now=99.0, floor=fl)                   # before the phone took it
    assert not VF.phone_floor_held(3.0, now=105.0, floor=dict(fl, on=False))
    assert not VF.phone_floor_held(3.0, now=105.0, floor={})
    monkeypatch.setenv("CRISSA_EAR_PHONE_FLOOR", "0")
    assert not VF.phone_floor_held(3.0, now=105.0, floor=fl)


def test_the_ear_reads_the_file_the_phone_server_writes(tmp_path, monkeypatch):
    f = tmp_path / "phone-floor.json"
    monkeypatch.setenv("CRISSA_PHONE_FLOOR", str(f))
    assert not VF.phone_floor_held(2.0, now=1000.0)                            # no file: the desk hears as before
    PF.take(f, now=1000.0)
    assert VF.phone_floor_held(2.0, now=1001.0)
    f.write_text("not json")
    assert not VF.phone_floor_held(2.0, now=1001.0)                            # unreadable: fail open


def test_the_ear_drops_it_before_the_decode():
    src = (ROOT / "modules" / "ear" / "crissa_ear" / "server.py").read_text()
    i = src.index("veto_first.phone_floor_held(_fd)")
    assert i < src.index("result = tr.transcribe_array(wav, sr)") and '"reason": "phone_floor"' in src


def _server(tmp_path, pairing=False, remote=False):
    spec = importlib.util.spec_from_file_location("phone_app_server_floor", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    (tmp_path / "packs").mkdir(exist_ok=True)
    m.CFG.clear()
    m.CFG.update({"packs": str(tmp_path / "packs"), "lines": str(tmp_path), "pack": "x", "port": 0,
                  "agent_file": str(tmp_path / "none.json"), "pairing": pairing, "pair_file": str(tmp_path / "pairing.json"),
                  "floor_file": str(tmp_path / "phone-floor.json"), "session_file": str(tmp_path / "phone-session.json")})
    if remote:
        m.H._loopback = lambda self: False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return m, srv


def _post(srv, path, body):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (srv.server_address[1], path), data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=5) as x:
            return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_the_phones_button_takes_and_gives_back_the_floor(tmp_path):
    m, srv = _server(tmp_path)
    f = tmp_path / "phone-floor.json"
    try:
        code, r = _post(srv, "/api/floor", {"on": True})
        assert code == 200 and r["ok"] and PF.held(PF.read(f))
        code, r = _post(srv, "/api/floor", {"on": False})
        assert code == 200 and r["floor"]["until"] <= PF.read(f)["since"] + 60
        assert not PF.held(PF.read(f), PF.read(f)["until"] + 0.01)
    finally:
        srv.shutdown()


def test_a_paired_server_takes_the_floor_only_from_a_paired_phone(tmp_path):
    m, srv = _server(tmp_path, pairing=True, remote=True)
    try:
        assert _post(srv, "/api/floor", {"on": True})[0] == 401
        assert not (tmp_path / "phone-floor.json").exists()
    finally:
        srv.shutdown()


def test_voice_in_chat_and_a_line_take_the_floor_on_the_servers_side_and_the_switch_turns_it_off(tmp_path, monkeypatch):
    m, srv = _server(tmp_path)
    srv.shutdown()
    f = tmp_path / "phone-floor.json"
    m._floor(12.0)
    assert PF.read(f)["until"] - PF.read(f)["since"] >= 11.9
    src = (ROOT / "scripts" / "phone-app-server.py").read_text()
    assert src.count("    _floor()\n") == 2                                     # _chat and _hear
    assert '_floor(float(sh.get("dur") or 0.0) + 3.0, line=True)' in src       # her line, before the phone plays it
    f.unlink()
    monkeypatch.setenv("CRISSA_PHONE_FLOOR", "0")
    assert m._floor() == {} and not f.exists()


def test_the_web_apps_send_holds_the_floor_until_her_answer_has_played():
    js = (ROOT / "display" / "phone-app" / "app.js").read_text()
    body = js[js.index("async function send(text)"):]
    assert "holdFloor();" in body[:body.index("try {")]
    hold = js[js.index("function holdFloor()"):js.index("async function send(text)")]
    assert '$("send").disabled || line || waiting' in hold and "floor(false)" in hold


def test_the_floor_ships_with_the_phone():
    pk = json.loads((ROOT / "config" / "crissa-packages.json").read_text())
    rows = pk.get("packages") or pk
    assert "lib/phone_floor.py" in rows["crissa-phone"]["files"]
