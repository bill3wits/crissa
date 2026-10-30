"""Round 20's speech share (the User 2026-10-14, .101 round 19: "approve with notes"):
1. one gain per voice for the session ("volume went lower and higher");
2. a first ask to read reads on ("Call me Ishmael." then nothing until "Continue.");
3. a line's first piece waits for its stamp ("missed front of sentence a couple times");
4. her mouth stops with her voice on a cut ("lips moved on after the cut")."""
from __future__ import annotations

import ast
import json
import math
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))
import loudness as L  # noqa: E402
import mouth_mailbox  # noqa: E402

SPEAK = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")
KEY = "crissa|pocket|-26.0"


def _take(src: str, names: tuple, ns: dict) -> dict:
    got = [n for n in ast.parse(src).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


def _setup_file(home: Path, gains: dict, hows: dict, at: float) -> None:
    (home / "run").mkdir(parents=True, exist_ok=True)
    (home / "run" / "setup-level.json").write_text(json.dumps({"run_id": "r1", "gains": gains, "how": hows, "at": at}))


def _tone(seconds: float, amp: float, sr: int = 24000) -> bytes:
    import array
    a = array.array("h", (int(amp * 32767 * math.sin(2 * math.pi * 220 * i / sr)) for i in range(int(seconds * sr))))
    return a.tobytes()


# ---- 1: one gain per voice for the session -------------------------------------------------------------------------

def test_1_setups_whole_line_measure_is_her_gain_in_conversation(tmp_path):
    _setup_file(tmp_path, {KEY: 0.62}, {KEY: "line"}, time.time() - 60)
    assert L.voice_gain(tmp_path, KEY) == (0.62, "line"), "Setup's measure of her, any run"
    assert L.voice_gain(tmp_path, "lila|pocket|-26.0") == (None, "")


def test_1_a_start_stands_until_a_whole_line_replaces_it_once(tmp_path):
    assert L.voice_gain_keep(tmp_path, KEY, 0.38, "start")
    assert not L.voice_gain_keep(tmp_path, KEY, 0.33, "start"), "the first start stands: no gain per line"
    assert L.voice_gain(tmp_path, KEY) == (0.38, "start")
    assert L.voice_gain_keep(tmp_path, KEY, 0.45, "line")
    assert L.voice_gain(tmp_path, KEY) == (0.45, "line")
    assert not L.voice_gain_keep(tmp_path, KEY, 0.50, "line"), "a whole line never replaces another"
    assert not L.voice_gain_keep(tmp_path, KEY, 0.30, "start")
    assert L.voice_gain(tmp_path, KEY) == (0.45, "line")


def test_1_a_newer_setup_measures_her_again(tmp_path):
    assert L.voice_gain_keep(tmp_path, KEY, 0.45, "line")
    _setup_file(tmp_path, {KEY: 0.70}, {KEY: "line"}, time.time() + 5)       # Setup (Voice ID again) after it
    assert L.voice_gain(tmp_path, KEY) == (0.70, "line")
    _setup_file(tmp_path, {KEY: 0.90}, {KEY: "start"}, time.time() + 9)      # a start never beats a whole line
    assert L.voice_gain(tmp_path, KEY) == (0.45, "line")


def test_1_the_switch_and_the_speech_count(monkeypatch):
    monkeypatch.delenv("CRISSA_VOICE_ONE_LEVEL", raising=False)
    assert L.voice_one_level_on() is True
    monkeypatch.setenv("CRISSA_VOICE_ONE_LEVEL", "0")
    assert L.voice_one_level_on() is False
    assert abs(L.voiced_s(_tone(1.0, 0.2), 24000) - 1.0) < 0.05
    assert L.voiced_s(_tone(1.0, 0.001), 24000) == 0.0


def _level_ns(tmp_path, run: str = "", house: bool = False) -> dict:
    ns = {"os": os, "sys": sys, "time": time, "threading": __import__("threading"), "_HOUSE": house,
          "PLAY_MATCH_LOUD": True, "PLAY_TARGET_DB": -26.0, "PLAY_ALLOW_CUT": True, "_VLEVEL": {"key": None, "gain": None},
          "LEVEL_LINE_CAP_S": 12.0, "_crissa_home": lambda: tmp_path, "_setup_run_id": lambda: run,
          "_setup_level_key": lambda: KEY, "_setup_level_on": lambda: True, "setup_level_keep": lambda g: None}
    return _take(SPEAK, ("_voice_level_on", "voice_level_gain", "_level_keep", "level_line"), ns)


def test_1_house_speak_keeps_her_first_start_and_reuses_it(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_VOICE_ONE_LEVEL", raising=False)
    ns = _level_ns(tmp_path)
    assert ns["voice_level_gain"]() is None
    ns["_level_keep"](0.38)                                   # her first line, measured on its start
    ns["_VLEVEL"].update(key=None, gain=None)                 # a new line is a new process
    assert ns["voice_level_gain"]() == pytest.approx(0.38), "the next line plays at the same gain"
    ns["level_line"](_tone(3.0, 0.2), 24000)                  # a whole line of hers (3 s of speech)
    g, how = L.voice_gain(tmp_path, KEY)
    assert how == "line" and g == pytest.approx(L.line_gain(_tone(3.0, 0.2), 24000, -26.0), rel=1e-3)
    ns["level_line"](_tone(3.0, 0.05), 24000)                 # another whole line: the first one stands
    assert L.voice_gain(tmp_path, KEY)[0] == pytest.approx(g)


def test_1_short_lines_setup_runs_and_the_house_keep_out(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_VOICE_ONE_LEVEL", raising=False)
    ns = _level_ns(tmp_path)
    ns["level_line"](_tone(1.0, 0.2), 24000)                  # "On it." is no measure of a voice
    assert L.voice_gain(tmp_path, KEY) == (None, "")
    assert _level_ns(tmp_path, run="r1")["voice_level_gain"]() is None, "in Setup, Setup's own level"
    assert _level_ns(tmp_path, house=True)["voice_level_gain"]() is None, "the house: each line as before"
    monkeypatch.setenv("CRISSA_VOICE_ONE_LEVEL", "0")
    ns["_level_keep"](0.5)
    assert L.voice_gain(tmp_path, KEY) == (None, ""), "the kill switch: nothing kept"


def test_1_the_wiring():
    boost = SPEAK.split("def boost_play_pcm(", 1)[1].split("\ndef ", 1)[0]
    assert "session = kept = gain = voice_level_gain()" in boost and "_level_keep(gain)" in boost
    assert "setup_level_keep(" not in boost, "every first measure goes through _level_keep"
    stream = SPEAK.split("def voice_stream_play(", 1)[1].split("\ndef ", 1)[0]
    assert "level_line_bg(bytes(buf), sr)" in stream
    assert stream.index("level_line_bg(bytes(buf), sr)") < stream.index("pc.end(bytes(buf), resp)")


# ---- 2: a first ask to read reads on -------------------------------------------------------------------------------

@pytest.fixture()
def eb():
    import crissa_bridge.ear_bridge as eb
    return eb


@pytest.mark.parametrize("text", ["You start reading from Moby Dick.", "Can you read from Moby Dick for me?",
                                  "Read me the opening of Moby Dick.", "Please read the first chapter of Dracula.",
                                  "Read a poem.", "Read it aloud, please.", "Keep reading."])
def test_2_an_ask_to_read_aloud(eb, text):
    assert eb.read_aloud_ask(text)


@pytest.mark.parametrize("text", ["Did you read my email?", "Can you read the board?", "What page are we on?",
                                  "I read that book last year."])
def test_2_a_question_with_read_in_it_is_not(eb, text):
    assert not eb.read_aloud_ask(text)


def test_2_the_first_ask_carries_the_note_and_a_continue_does_not():
    disp = BRIDGE.split("    read_turn, read_note = reading_turn(", 1)[1].split("    rmeta: dict = {}", 1)[0]
    assert "ask_note = bool(read_turn and not read_note and READ_ASK_NOTE_ON and read_aloud_ask(spoken_user))" in disp
    assert "turn_note += READ_ASK_NOTE" in disp
    assert "system_note=turn_note," in BRIDGE
    assert "brief_reading=bool(read_note)" in BRIDGE, "the note never makes a first ask a continue"


def test_2_the_note_and_its_switch(eb, monkeypatch):
    n = eb.READ_ASK_NOTE
    assert "aloud" in n and "first line" in n and "summarize" in n and "go on" in n
    assert "word for word" not in n, "never the shape of asking a model to reproduce a text (round 17)"
    assert 'os.environ.get("EAR_BRIDGE_READ_ASK_NOTE", "1")' in BRIDGE


# ---- 3: a line's first piece waits for its stamp -------------------------------------------------------------------

def _hold_ns(**over) -> dict:
    ns = {"os": os, "_HOUSE": False, "STAMP_HOLD_S": 1.0, "STAMP_HOLD_NEXT_S": 0.5, "STAMP_HOLD_FIRST_S": 0.6,
          "MOUTH_HOP_MS": 1000.0 / 48, "sheet_lead_s": lambda: 0.1}
    ns.update(over)
    return _take(SPEAK, ("stamp_hold_on", "stamp_hold_pre_s", "_StampHold"), ns)


class _Mc:
    def __init__(self):
        self.ends, self._said, self.failed, self.mismatch, self._fell_back = [1000, 2000], 0, False, False, False


class _Air:
    hop = 500


def test_3_the_default_holds_the_first_piece_only(monkeypatch):
    monkeypatch.delenv("HOUSE_SPEAK_STAMP_HOLD", raising=False)
    assert _hold_ns()["stamp_hold_on"]() == "", "round 28 (.101 2026-10-17): no hold by default"
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "first")
    ns = _hold_ns()
    mode = ns["stamp_hold_on"]()
    assert mode == "first"
    h = ns["_StampHold"](_Mc(), _Air(), None, 24000, mode)
    assert h._cap(0) == pytest.approx(0.6) and h._cap(1) == 0.0 and h._cap(2) == 0.0
    assert not h._free(0) and h._free(1), "a later piece is never waited for"
    full = ns["_StampHold"](_Mc(), _Air(), None, 24000, "all")
    assert full._cap(0) == 1.0 and full._cap(1) == 0.5, "=1: round 17's hold, every piece"
    assert ns["_StampHold"](_Mc(), _Air(), None, 24000)._cap(1) == 0.5, "a caller that names no mode: round 17's"


def test_3_the_switches(monkeypatch):
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "0")
    assert _hold_ns()["stamp_hold_on"]() == "", "=0: round 18-19's path"
    monkeypatch.delenv("HOUSE_SPEAK_STAMP_HOLD")
    assert _hold_ns(_HOUSE=True)["stamp_hold_on"]() == "", "the house never holds her voice"
    assert _hold_ns(STAMP_HOLD_FIRST_S=0.0)["stamp_hold_on"]() == ""
    assert 'STAMP_HOLD_FIRST_S = float(os.environ.get("HOUSE_SPEAK_STAMP_HOLD_FIRST_S", "0.6") or 0)' in SPEAK
    assert "_StampHold(mc, air, sink_in, sr, hold_on)" in SPEAK


# ---- 4: her mouth stops with her voice on a cut --------------------------------------------------------------------

def test_4_a_cut_drops_every_row_after_it_and_any_post_still_in_flight():
    mb = mouth_mailbox.MouthMailbox()
    now = time.time()
    mb.ingest({"air_at": now, "sr": 24000, "hop_ms": 20.0, "pts0": now, "rms": [0.1] * 200, "visemes": [3] * 200,
               "pcm16": "AAAA", "speaker": "crissa"})
    mb.ingest({"air_at": now, "pts0": now + 3.0, "pcm16": "BBBB"})
    assert len(mb.snapshot()["frames"]) == 200
    cut = now + 1.0
    mb.ingest({"air_at": now, "air_until": cut, "cut": True, "speaker": "crissa"})
    snap = mb.snapshot()
    assert snap["frames"] and max(f[0] for f in snap["frames"]) < cut and snap["air_until"] == pytest.approx(cut)
    assert all(r[0] < cut for r in snap["pcm"])
    mb.ingest({"air_at": now, "pts0": now + 2.0, "rms": [0.1] * 10, "pcm16": "CCCC"})     # a post still in flight
    snap = mb.snapshot()
    assert max(f[0] for f in snap["frames"]) < cut and all(r[0] < cut for r in snap["pcm"])
    later = now + 10
    mb.ingest({"air_at": later, "pts0": later, "rms": [0.1] * 10})                         # her next line
    assert len(mb.snapshot()["frames"]) == 10, "a new line is never cut by the last one"


def test_4_a_post_without_the_mark_changes_nothing():
    mb = mouth_mailbox.MouthMailbox()
    now = time.time()
    mb.ingest({"air_at": now, "pts0": now, "rms": [0.1] * 100})
    mb.ingest({"air_at": now, "air_until": now + 0.5})                                     # a line's own close
    assert len(mb.snapshot()["frames"]) == 100, "the house's close (and any post without `cut`) keeps its rows"


def test_4_the_signal_posts_the_cut_in_a_pack_only(monkeypatch):
    posted = []

    class _U:
        class request:
            @staticmethod
            def Request(url, data=None, headers=None, method=None):
                return (url, data)

            @staticmethod
            def urlopen(req, timeout=None):
                posted.append(json.loads(req[1]))
                return type("R", (), {"close": lambda self: None})()
    air = type("A", (), {"air_at": 123.0})()
    ns = {"os": os, "time": time, "json": json, "urllib": _U, "_HOUSE": False, "MOUTH_URL": "http://m/v1/mouth",
          "_LIVE_AIR": {"air": air}, "_AIR_WHO": "crissa", "_LINE": {}}
    _take(SPEAK, ("cut_sheet_on", "_mouth_cut"), ns)
    monkeypatch.delenv("HOUSE_SPEAK_CUT_SHEET", raising=False)
    ns["_mouth_cut"]()
    assert posted and posted[0]["cut"] is True and posted[0]["air_at"] == 123.0 and posted[0]["speaker"] == "crissa"
    posted.clear()
    ns["_HOUSE"] = True
    ns["_mouth_cut"]()
    assert not posted, "the house never sends a cut"
    ns["_HOUSE"] = False
    monkeypatch.setenv("HOUSE_SPEAK_CUT_SHEET", "0")
    ns["_mouth_cut"]()
    assert not posted, "the kill switch"
    term = SPEAK.split("def _air_sigterm(", 1)[1].split("\ndef ", 1)[0]
    assert "_mouth_cut()" in term and term.index("_mouth_cut()") < term.index("os._exit(143)")
    assert '_LIVE_AIR["air"] = self' in SPEAK
