"""The phone as the second mic (the User 2026-10-19, build 1; ASK-20261019-1230 from .101): the desk mic stays the
default; while the phone's mic is open the two compete on a 2 s average of speech over each mic's own noise floor, with
a ~3 dB margin to switch; both are buffered and the winner's copy of the whole utterance goes to the ASR, one copy only;
the phone sits out while her line plays from the desk speakers, the desk while it plays on the phone.
modules/ear/crissa_ear/phone_mic.py + ear_loop/server/veto_first, lib/phone_floor, the phone server's /api/mic."""
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))
import phone_floor as PF  # noqa: E402
from crissa_ear import phone_mic as PM  # noqa: E402
from crissa_ear import veto_first as VF  # noqa: E402

SR = 16000
FR = 0.3
RNG = np.random.default_rng(7)


def _noise(db, s=FR):
    x = RNG.standard_normal(int(s * SR)).astype(np.float32)
    return x / np.sqrt(np.mean(x ** 2)) * (10 ** (db / 20.0))


def _pcm(x):
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    """No line on the air anywhere; the floor file in tmp."""
    monkeypatch.setattr(PM, "desk_air", lambda now: False)
    monkeypatch.setenv("CRISSA_PHONE_FLOOR", str(tmp_path / "phone-floor.json"))
    return tmp_path / "phone-floor.json"


def _run(pm, t, desk_db, phone_db, n, speech=True):
    """n ear frames: the phone streams its frame (it arrives LAG_S after the sound), the ear scores both."""
    for _ in range(n):
        t += FR
        if phone_db is not None:
            pm.push(_pcm(_noise(phone_db)), SR, now=t + PM.LAG_S)
        ph = pm.frame(t + PM.LAG_S, FR)
        pm.score(t, _noise(desk_db), ph, speech)
    return t


def test_the_switch_needs_the_margin_and_no_phone_score_is_the_desk():
    assert PM.decide("desk", 10.0, None) == "desk"
    assert PM.decide("desk", 10.0, 12.9) == "desk"                 # under the margin: stays
    assert PM.decide("desk", 10.0, 13.0) == "phone"
    assert PM.decide("phone", 12.9, 10.0) == "phone"               # and back the same way
    assert PM.decide("phone", 13.0, 10.0) == "desk"
    assert PM.decide("phone", None, 1.0) == "phone"


def test_a_loud_phone_in_a_quiet_room_does_not_win_on_its_level(quiet):
    """Auto gain: the phone's floor sits 20 dB over the desk's. Speech over each floor decides, not raw level."""
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)               # 6 s of room: both floors learnt
    t = _run(pm, t, -35, -25, 7)                                   # he talks at the desk: desk +25 dB, phone +15
    assert pm.winner == "desk" and pm.status(t + PM.LAG_S)["desk_avg"] > pm.status(t + PM.LAG_S)["phone_avg"] + 3


def test_the_phone_wins_when_it_hears_him_better_over_its_own_floor(quiet):
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    t = _run(pm, t, -50, -12, 7)                                   # across the room, phone in hand: desk +10, phone +28
    assert pm.winner == "phone"


def test_the_whole_utterance_comes_from_the_winner_from_its_first_word(quiet):
    """The phone's copy is buffered: the pick hands the ASR its audio from the utterance's start, even though the
    phone only overtook the desk part way in."""
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    t0 = t
    t = _run(pm, t, -50, -12, 10)
    t = _run(pm, t, -60, -40, 4, speech=False)                     # the hangover
    desk = np.zeros(int(round((t - t0) * SR)), np.float32)
    wav, meta = pm.pick(t0, t, desk)
    assert meta["mic_pick"] == "phone" and meta["mic_pick_why"] == "winner" and len(wav) == len(desk)
    head = wav[: int(1.0 * SR)]
    assert 20 * np.log10(np.sqrt(np.mean(head ** 2)) + 1e-9) > -16    # his first words are the phone's
    assert pm.picks == {"desk": 0, "phone": 1}


def test_a_closed_phone_is_the_desk_and_forgets_its_scores(quiet):
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    t = _run(pm, t, -50, -12, 7)
    assert pm.winner == "phone"
    t = _run(pm, t, -50, None, 5)                                  # the phone stops sending (screen locked)
    assert pm.winner == "desk" and not pm.is_open(t + PM.LAG_S)
    desk = np.ones(SR, np.float32)
    wav, meta = pm.pick(t - 1.0, t + PM.LAG_S, desk)
    assert wav is desk and meta["mic_pick_why"] == "phone_closed"


def test_the_phone_sits_out_while_her_line_plays_from_the_desk(quiet, monkeypatch):
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    monkeypatch.setattr(PM, "desk_air", lambda now: True)
    t0 = t
    t = _run(pm, t, -50, -12, 7)                                   # the phone hears her loud: not scored
    assert pm.winner == "desk" and pm.frame(t + PM.LAG_S, FR) is None
    desk = np.ones(int(round((t - t0) * SR)), np.float32)
    wav, meta = pm.pick(t0, t, desk)
    assert wav is desk and meta["mic_pick_why"] == "desk_air"


def test_while_her_line_plays_on_the_phone_the_phone_copy_is_used(quiet, monkeypatch):
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    PF.take(quiet, 10.0, now=t, line=True)
    monkeypatch.setattr(PM, "desk_air", lambda now: False)
    t0 = t
    t = _run(pm, t, -30, -30, 5)
    _, meta = pm.pick(t0, t, np.zeros(int(round((t - t0) * SR)), np.float32))
    assert meta["mic_pick"] == "phone" and meta["mic_pick_why"] == "line_on_phone"


def test_a_released_push_to_talk_is_still_the_phones_utterance(quiet):
    """He lets go of the button with his last word; the ear flushes after its hangover, when the phone has stopped."""
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    t0 = t
    t = _run(pm, t, -50, -12, 10)
    t = _run(pm, t, -60, None, 6, speech=False)                    # 1.8 s of hangover, nothing from the phone
    _, meta = pm.pick(t0, t, np.zeros(int(round((t - t0) * SR)), np.float32))
    assert meta["mic_pick"] == "phone" and meta["phone_open"] is True and meta["phone_avg"] > meta["desk_avg"]


def test_a_pause_never_moves_the_winner(quiet):
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    t = _run(pm, t, -50, -12, 7)
    t = _run(pm, t, -60, -40, 10, speech=False)                    # 3 s of quiet: every score has aged out
    assert pm.winner == "phone" and pm.status(t + PM.LAG_S)["phone_avg"] is None


def test_the_phones_hiss_opens_no_utterance(quiet):
    pm = PM.PhoneMic(SR)
    t = _run(pm, 1000.0, -60, -40, 20, speech=False)
    assert not pm.heard(_noise(-40), True)                         # at its floor: the detector's yes is not enough
    assert pm.heard(_noise(-30), True) and not pm.heard(_noise(-30), False) and not pm.heard(None, True)
    assert PM.PhoneMic(SR).heard(_noise(-40), True)                # no floor learnt yet: the detector decides


def test_the_ring_places_chunks_on_the_ears_clock_back_to_back():
    pm = PM.PhoneMic(SR)
    for i in range(5):
        pm.push(_pcm(np.full(int(0.2 * SR), 0.5, np.float32)), SR, now=500.0 + 0.2 * (i + 1) + 0.01 * (i % 2))
    w = pm.window(500.0 - PM.LAG_S, 501.0 - PM.LAG_S + 0.02)
    assert np.count_nonzero(w) >= int(0.99 * SR)                   # contiguous, no holes from arrival jitter
    assert not pm.window(400.0, 401.0).any()                       # silence where it sent none
    assert pm.push(_pcm(np.zeros(3 * SR, np.float32)), SR)["ok"] is False
    pm2 = PM.PhoneMic(SR)
    pm2.push(_pcm(np.full(4800, 0.5, np.float32)), 48000, now=10.0)
    assert len(pm2.segs[0][1]) == 1600                             # 48 kHz phones come in at the ear's rate


def test_her_line_on_the_phone_is_stamped_apart_from_the_button(tmp_path):
    f = tmp_path / "phone-floor.json"
    PF.take(f, now=100.0)                                          # the button
    assert "line_until" not in PF.read(f)
    PF.take(f, 8.0, now=101.0, line=True)                          # her line on the phone
    d = PF.read(f)
    assert d["line_since"] == 101.0 and d["line_until"] == 109.0 and d["since"] == 100.0
    PF.take(f, now=103.0)
    assert PF.read(f)["line_until"] == 109.0                       # a button refresh keeps the line's stamp
    assert VF.phone_floor_held(2.0, now=105.0, floor=PF.read(f), line=True)
    assert not VF.phone_floor_held(2.0, now=120.0, floor={"on": True, "since": 118.0, "until": 125.0,
                                                          "line_since": 101.0, "line_until": 109.0}, line=True)
    assert VF.phone_floor_held(2.0, now=120.0, floor={"on": True, "since": 118.0, "until": 125.0})


@pytest.mark.parametrize("meta, held_by, dropped", [
    ({}, "button", True),                                          # no phone mic: the floor hold, as before
    ({"phone_open": True, "mic": "near"}, "button", False),        # mic open: the compete replaces the button
    ({"phone_open": True, "mic": "near"}, "line", True),           # a desk copy while her line plays on the phone
    ({"phone_open": True, "mic": "phone"}, "line", False),         # the phone's own copy is always its turn
])
def test_the_ears_floor_rule_with_the_phone_mic_open(tmp_path, monkeypatch, meta, held_by, dropped):
    import time

    from crissa_ear import ear_loop, server, voice_live
    from crissa_ear.config import Config
    f = tmp_path / "phone-floor.json"
    monkeypatch.setenv("CRISSA_PHONE_FLOOR", str(f))
    PF.take(f, 10.0, now=time.time() - 1.0, line=held_by == "line")   # a second ago (take() rounds to the ms)
    monkeypatch.setattr(voice_live, "kick", lambda pcm, sr: None)
    monkeypatch.setattr(VF, "log_path", lambda: tmp_path / "veto-first.jsonl")     # never the state home's log

    class Decoded(Exception):
        pass

    class _Tr:
        def transcribe_array(self, wav, sr):
            raise Decoded()
    monkeypatch.setattr(server, "get_transcriber", lambda cfg=None: _Tr())
    H = server.make_handler(Config())
    h = H.__new__(H)
    ear_loop._DECODE_META.meta = dict(meta)
    try:
        if dropped:
            assert h._process_listen_array(np.zeros(SR, np.float32), SR, via="ear")["reason"] == "phone_floor"
        else:
            with pytest.raises(Decoded):
                h._process_listen_array(np.zeros(SR, np.float32), SR, via="ear")
    finally:
        ear_loop._DECODE_META.meta = None


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _post_raw(port, path, data, sr=16000):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method="POST",
                               headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(sr)})
    try:
        with urllib.request.urlopen(r, timeout=5) as x:
            return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_the_ears_route_is_off_by_default_and_takes_chunks_when_on(monkeypatch):
    from crissa_ear import server
    from crissa_ear.config import Config
    monkeypatch.setattr(PM, "_ONE", None)
    srv = _serve(server.make_handler(Config()))
    try:
        port = srv.server_address[1]
        monkeypatch.delenv("CRISSA_EAR_PHONE_MIC", raising=False)
        code, r = _post_raw(port, "/v1/phone-mic", _pcm(_noise(-30)))
        assert code == 404 and r["off"] is True and PM._ONE is None
        monkeypatch.setenv("CRISSA_EAR_PHONE_MIC", "1")
        code, r = _post_raw(port, "/v1/phone-mic", _pcm(_noise(-30)), sr=48000)
        assert code == 200 and r["ok"] and PM.get().chunks == 1 and PM.get().is_open()
        assert _post_raw(port, "/v1/phone-mic", b"\x00\x00", sr=5)[0] == 400
    finally:
        srv.shutdown()


def _phone_server(tmp_path, ear_url, pairing=False, remote=False):
    spec = importlib.util.spec_from_file_location("phone_app_server_mic", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    (tmp_path / "packs").mkdir(exist_ok=True)
    m.CFG.clear()
    m.CFG.update({"packs": str(tmp_path / "packs"), "lines": str(tmp_path), "pack": "x", "port": 0,
                  "agent_file": str(tmp_path / "none.json"), "pairing": pairing, "pair_file": str(tmp_path / "p.json"),
                  "floor_file": str(tmp_path / "phone-floor.json"), "ear": ear_url})
    if remote:
        m.H._loopback = lambda self: False
    return m, _serve(m.H)


def test_the_phone_server_relays_the_open_mic_to_the_ear_and_takes_no_floor(tmp_path, monkeypatch):
    got = []

    class Ear(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.path, self.headers.get("X-Sample-Rate"), self.rfile.read(int(self.headers["Content-Length"]))))
            b = b'{"ok": true, "s": 0.2}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    ear = _serve(Ear)
    m, srv = _phone_server(tmp_path, "http://127.0.0.1:%d/v1/phone-mic" % ear.server_address[1])
    try:
        port = srv.server_address[1]
        monkeypatch.delenv("CRISSA_PHONE_MIC", raising=False)
        assert _post_raw(port, "/api/mic", b"\x01\x00" * 3200)[0] == 404 and not got
        monkeypatch.setenv("CRISSA_PHONE_MIC", "1")
        code, r = _post_raw(port, "/api/mic", b"\x01\x00" * 3200, sr=48000)
        assert code == 200 and r["ok"] and got == [("/v1/phone-mic", "48000", b"\x01\x00" * 3200)]
        assert _post_raw(port, "/api/mic", b"\x00" * (m.MIC_MAX_BYTES + 2))[0] == 413
        assert not (tmp_path / "phone-floor.json").exists()        # the open mic never holds the floor
    finally:
        srv.shutdown()
        ear.shutdown()


def test_a_paired_server_takes_the_mic_only_from_a_paired_phone(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_MIC", "1")
    m, srv = _phone_server(tmp_path, "http://127.0.0.1:9/v1/phone-mic", pairing=True, remote=True)
    try:
        assert _post_raw(srv.server_address[1], "/api/mic", b"\x00\x00" * 100)[0] == 401
    finally:
        srv.shutdown()


def test_an_ear_that_does_not_answer_is_a_502_never_a_hang(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_MIC", "1")
    m, srv = _phone_server(tmp_path, "http://127.0.0.1:9/v1/phone-mic")
    try:
        code, r = _post_raw(srv.server_address[1], "/api/mic", b"\x00\x00" * 100)
        assert code == 502 and "did not answer" in r["error"]
    finally:
        srv.shutdown()


def test_the_ear_loop_hears_both_and_hands_the_winners_copy_to_the_decode():
    src = (ROOT / "modules" / "ear" / "crissa_ear" / "ear_loop.py").read_text()
    frame = src[src.index("has_speech = bool(spans)"):src.index("onset.open_frames = self._open_frames_now()")]
    assert "_pm.frame(t_cap, frame)" in frame and "_pm.score(t_cap, wav, _ph, has_speech)" in frame
    assert "has_speech = _pm.heard(_ph, bool(detect_speech_spans(" in frame     # either mic opens an utterance
    assert ") or has_speech" in frame
    flush = src[src.index("def _flush_utterance("):src.index("def _maybe_release_cache")]
    i = flush.index(".pick(_t1 - dur, _t1, wav)")
    assert i < flush.index("pcm = audio_mod.float_to_pcm16(wav).tobytes()")       # the print and the decode see it
    assert 'meta.update(mic="phone"' in flush


def test_it_ships_where_it_runs():
    rows = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]
    assert "modules/ear/crissa_ear/phone_mic.py" in rows["crissa-ear"]["files"]
    assert "scripts/phone-mic-standin.py" in rows["crissa-phone"]["files"]
