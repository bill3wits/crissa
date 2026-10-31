"""NN (.101 2026-10-11): the lip-sync test line showed no lips matched to the words.

What the .101 logs say (house-speak-lines.jsonl, house-speak.err, pocket.log, aligner.log): the aligner was warm three
minutes before the test line (05:13:32), and every test line logged `clock "letter"` was one cut short (signal 15)
before house-speak had read the last of the voice. A streamed voice without sentence stamps (Pocket) is read at the
player's pace, so the whole-line alignment was asked for only ~2 s before the line ENDED; "aligned" in the log
named only that tail. Two fixes, both off on the house:
  * house-speak reads such a stream on its own thread and aligns the whole line the moment the voice has made it
    (_EarlyClock; HOUSE_MOUTH_ALIGN_EARLY, on in a pack);
  * the player speaks the lip-sync test line on house-speak's buffered path (HOUSE_SPEAK_BUFFER=1), so its mouth is
    the aligned clock from her first word (CRISSA_LIPSYNC_TEST_ALIGNED, never on the house).

No voice, aligner or player runs here: the functions are taken from the two scripts' source and run against
stand-ins."""
from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SPEAK = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _take(src: str, names: tuple, ns: dict) -> dict:
    tree = ast.parse(src)
    got = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


def _speak_ns(house: bool, align_on: bool = True) -> dict:
    ns = {"os": os, "sys": sys, "Path": Path, "threading": threading, "_HOUSE": house, "_LINE": {},
          "_AIR_TEXT": "Pick up the big blue map, Bob.", "TEMPO": 1.0, "MouthAir": object,
          "mouth_align_on": lambda: align_on, "__file__": str(ROOT / "scripts" / "house-speak.py")}
    return _take(SPEAK, ("align_early_on", "_EarlyClock", "_early_clock_for"), ns)


class _Resp:
    """A chunked /stream body, as Pocket writes it (Transfer-Encoding: chunked)."""

    def __init__(self, pieces, chunked=True):
        raw = b"".join(b"%X\r\n" % len(p) + p + b"\r\n" for p in pieces) + b"0\r\n\r\n"
        self.fp = io.BytesIO(raw)
        self.headers = {"Transfer-Encoding": "chunked"} if chunked else {"Content-Length": str(len(raw))}

    def close(self):
        pass


class _Air:
    def __init__(self):
        self.armed = []
        self.air_at = time.time()

    def arm_clock(self, pcm, text, wait_s=0.0, tempo=1.0):
        self.armed.append((bytes(pcm), text, tempo))


def test_early_alignment_is_a_packs_and_the_switch_forces_it(monkeypatch):
    monkeypatch.delenv("HOUSE_MOUTH_ALIGN_EARLY", raising=False)
    assert _speak_ns(house=False)["align_early_on"]() is True
    assert _speak_ns(house=True)["align_early_on"]() is False, "the house's replies carry Turbo's sentence stamps"
    monkeypatch.setenv("HOUSE_MOUTH_ALIGN_EARLY", "1")
    assert _speak_ns(house=True)["align_early_on"]() is True
    monkeypatch.setenv("HOUSE_MOUTH_ALIGN_EARLY", "0")
    assert _speak_ns(house=False)["align_early_on"]() is False


def test_the_whole_line_is_aligned_the_moment_the_voice_has_made_it(monkeypatch):
    monkeypatch.delenv("HOUSE_MOUTH_ALIGN_EARLY", raising=False)
    ns = _speak_ns(house=False)
    pieces = [b"\x01\x00" * 2400, b"\x02\x00" * 2400, b"\x03\x00" * 1200]
    ec = ns["_early_clock_for"](_Resp(pieces))
    assert ec is not None and ns["_LINE"]["align_early"] is True
    air = _Air()
    ec.attach(air)
    got = bytearray()
    while True:                                      # the player's own pace: the bytes come out in order, whole
        b = ec.reader.read(1000, 2.0)
        if not b:
            break
        got.extend(b)
    assert bytes(got) == b"".join(pieces)
    for _ in range(100):
        if air.armed:
            break
        time.sleep(0.01)
    assert air.armed == [(b"".join(pieces), "Pick up the big blue map, Bob.", 1.0)], "one alignment, the whole line"
    assert ec.attached


def test_no_early_alignment_without_a_chunked_body_or_an_aligner(monkeypatch):
    monkeypatch.delenv("HOUSE_MOUTH_ALIGN_EARLY", raising=False)
    ns = _speak_ns(house=False)
    assert ns["_early_clock_for"](_Resp([b"\x00\x00" * 10], chunked=False)) is None, "the body is read as before"
    ns = _speak_ns(house=False, align_on=False)
    ec = ns["_early_clock_for"](_Resp([b"\x05\x00" * 100]))
    air = _Air()
    ec.attach(air)
    while ec.reader.read(1000, 2.0):
        pass
    time.sleep(0.1)
    assert air.armed == [], "no aligner: the letter clock, as before"
    assert ns["_early_clock_for"].__doc__ and _speak_ns(house=True)["_early_clock_for"](_Resp([b"\x00\x00"])) is None


def test_the_stream_path_hands_the_reader_and_skips_the_late_alignment():
    i = SPEAK.index("early = _early_clock_for(resp, marks) if pc is None else None")   # round 11: Pocket marks
    assert SPEAK.index("pc = _piece_clock_for(text[:800], resp, sr)") < i < SPEAK.index("first = _http_read(resp, read_bytes)")
    assert "elif early is not None:\n                early.attach(air)" in SPEAK
    j = SPEAK.index("elif air is not None and early is not None and early.attached:")
    assert j < SPEAK.index("elif air is not None and mouth_align_on() and buf:\n            air.arm_clock(bytes(buf)")
    # the line log says when the alignment landed, in a pack only (the house's log is unchanged)
    assert 'if not _HOUSE:\n                        # how far into the line' in SPEAK


def _spawn_ns(house: bool, env_on: str | None = None) -> tuple[dict, list]:
    seen: list = []

    class _Popen:
        def __init__(self, cmd, env=None, **kw):
            seen.append(env)
            self.pid = 4242 + len(seen)

    class _Sub:
        DEVNULL = subprocess.DEVNULL
        STDOUT = subprocess.STDOUT
        Popen = _Popen

    class _Talk:
        @staticmethod
        def speak_env():
            return {}
    ns = {"os": os, "sys": sys, "ROOT": ROOT, "time": time, "subprocess": _Sub, "crissa_talk": _Talk,
          "speak_timing_env": lambda: {}, "reap_wedged_speakers": lambda: None,
          "_speak_log_open": lambda: (subprocess.DEVNULL, -1), "_SPEAKERS": [], "_SPEAK_AT": {}, "_SPEAK_LOG_AT": {},
          "IS_HOUSE": house}
    tree = ast.parse(CONDUCTOR)
    assigns = [n for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_ALIGN_FIRST"]
    assert len(assigns) == 1
    exec(compile(ast.Module(body=assigns, type_ignores=[]), "src", "exec"), ns)
    _take(CONDUCTOR, ("lipsync_test_aligned_on", "_spawn_speak"), ns)
    return ns, seen


def test_the_test_line_and_only_it_is_aligned_before_her_first_word(monkeypatch):
    monkeypatch.delenv("CRISSA_LIPSYNC_TEST_ALIGNED", raising=False)
    monkeypatch.delenv("HOUSE_SPEAK_BUFFER", raising=False)
    ns, seen = _spawn_ns(house=False)
    assert ns["lipsync_test_aligned_on"]()
    ns["_ALIGN_FIRST"]["Pick up the big blue map."] = 1.0
    ns["_spawn_speak"]("Pick up the big blue map.", "crissa")
    ns["_spawn_speak"]("What is two plus two? Four.", "crissa")
    assert seen[0].get("HOUSE_SPEAK_BUFFER") == "1", "the test line: made, aligned, then played"
    assert "HOUSE_SPEAK_BUFFER" not in seen[1], "a reply still streams"
    assert ns["_ALIGN_FIRST"] == {}, "read once"
    monkeypatch.setenv("CRISSA_LIPSYNC_TEST_ALIGNED", "0")
    assert not ns["lipsync_test_aligned_on"]()
    monkeypatch.delenv("CRISSA_LIPSYNC_TEST_ALIGNED")
    assert not _spawn_ns(house=True)[0]["lipsync_test_aligned_on"](), "never on the house"


def test_the_speak_route_marks_the_lip_sync_line():
    i = CONDUCTOR.index('if head == "/v1/speak"')
    block = CONDUCTOR[i:CONDUCTOR.index('if head == "/v1/announce"', i)]
    assert 'if str(body.get("by") or "") == "lip sync" and lipsync_test_aligned_on():' in block
    assert block.index("_ALIGN_FIRST[text] = time.time()") < block.index("LANE.say(text, who, queue=queued)")
    # both Walls post the test line with that name (lib/crissa_wall_key, display/crissa-wall.html)
    assert '"by": "lip sync"' in (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
    assert 'by: "lip sync"' in (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
