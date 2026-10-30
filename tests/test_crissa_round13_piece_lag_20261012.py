"""Round 13, the .101 lips under load (2026-10-12, RESULTS-20261012-0844 "The reading (08:48)"): a 226-character reading
spoke 3 of its 5 Pocket pieces before their stamps (piece 2 for 2.2 s): "lips falling behind on reading". A piece is
stamped only once Pocket has made all of it, and on a busy CPU Pocket is barely ahead of the player.

  * the rest of the line restarts at the last stamp (MouthAir._make_rest_clock / _rest_class): the unstamped words
    start where the stamped ones end, advance on voiced hops only, at the prefix's own pace;
  * a slow Pocket waits a beat for its first stamp (_pocket_first_wait): when her last Pocket line was slow or had a
    later piece late, her first word waits for the first piece's stamp, up to HOUSE_POCKET_FIRST_WAIT_S.

No voice, aligner or player runs here: the functions are taken from house-speak's source and run on stand-ins."""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SPEAK = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")


def _take(names: tuple, ns: dict) -> dict:
    tree = ast.parse(SPEAK)
    got = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


def _methods(names: tuple, ns: dict) -> type:
    tree = ast.parse(SPEAK)
    cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MouthAir"][0]
    body = [m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name in names]
    assert sorted(m.name for m in body) == sorted(names)
    mod = ast.Module(body=[ast.ClassDef(name="Air", bases=[], keywords=[], body=body, decorator_list=[],
                                        type_params=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), "src", "exec"), ns)
    return ns["Air"]


TEXT = "Call me Ishmael. Some years ago, never mind how long precisely."


def _air(house=False):
    ns = {"os": os, "sys": sys, "_HOUSE": house, "_AIR_TEXT": TEXT, "MOUTH_HOP_MS": 20.833333}
    Air = _methods(("_make_rest_clock", "_rest_class"), ns)
    a = Air()
    a.sr = 24000
    # 2 s of prefix (96 hops): 0.6 s quiet, voice, a quiet tail; then the rest: quiet 10 hops, then voiced
    a._rms_all = [0.0] * 29 + [0.5] * 55 + [0.0] * 12 + [0.0] * 10 + [0.6] * 80
    return a


def test_the_rest_of_the_line_starts_at_the_last_stamp_and_moves_on_voiced_hops_only(monkeypatch):
    monkeypatch.delenv("HOUSE_MOUTH_REST_CLOCK", raising=False)
    a = _air()
    pcm = b"\x00\x00" * 48000                         # the stamped prefix: 2 s
    rc = a._make_rest_clock(pcm, "Call me Ishmael.", 1.0)
    assert rc is not None and rc["from"] == 96
    import mouth_viseme as MV
    want = [v for v in MV.text_to_visemes("Some years ago, never mind how long precisely.") if v not in ("rest", "rest-open")]
    assert rc["vis"] == want
    got = [a._rest_class(rc, i) for i in range(96, 186)]
    assert got[:10] == ["rest-open"] * 10, "her lead-in quiet: the clock waits"
    assert got[10] == want[0], "the first voiced hop is the first unstamped word's first shape"
    assert got[-1] in want and got.index(got[-1]) > 10
    assert a._rest_class(rc, 999) is None, "a hop not heard yet falls back"


def test_the_rest_clock_is_a_packs_and_has_a_switch(monkeypatch):
    pcm = b"\x00\x00" * 48000
    assert _air(house=True)._make_rest_clock(pcm, "Call me Ishmael.", 1.0) is None
    monkeypatch.setenv("HOUSE_MOUTH_REST_CLOCK", "0")
    assert _air()._make_rest_clock(pcm, "Call me Ishmael.", 1.0) is None
    monkeypatch.delenv("HOUSE_MOUTH_REST_CLOCK")
    assert _air()._make_rest_clock(pcm, TEXT, 1.0) is None, "nothing left unstamped"


def test_the_partial_branch_asks_the_rest_clock_first():
    i = SPEAK.index("                    elif self._clock_partial:")
    j = SPEAK.index('rc = getattr(self, "_rest_clock", None)', i)
    k = SPEAK.index("if letter is None:", i)
    assert i < j < k
    assert "self._rest_clock = self._make_rest_clock(pcm, text, tempo) if partial else None" in SPEAK


def _wait_ns(tmp_path, house=False):
    ns = {"os": os, "sys": sys, "json": json, "time": time, "Path": Path, "_HOUSE": house, "_LINE": {},
          "LINE_LOG": tmp_path / "logs" / "house-speak-lines.jsonl", "POCKET_FIRST_WAIT_S": 0.3,
          "POCKET_SLOW_PACE": 1.6, "POCKET_PACE_AGE_S": 600.0, "POCKET_LATE_S": 0.25}
    return _take(("_pocket_pace_file", "save_pocket_pace", "last_pocket_pace", "_pocket_first_wait"), ns)


class _MC:
    def __init__(self):
        self.first = threading.Event()


def _waited(ns) -> float:
    t0 = time.time()
    ns["_pocket_first_wait"](_MC(), None, 0.0, 0.0, 24000)
    return time.time() - t0


def test_a_slow_or_late_last_line_waits_for_the_first_stamp_and_a_fast_one_does_not(tmp_path):
    ns = _wait_ns(tmp_path)
    assert _waited(ns) < 0.1, "no line yet: no wait"
    ns["save_pocket_pace"](2.4, 0.0)
    assert _waited(ns) < 0.1, "a fast Pocket never waits"
    ns["save_pocket_pace"](2.0, 2.21)                  # .101's reading: fast overall, its second piece 2.2 s late
    assert 0.25 < _waited(ns) < 0.6 and ns["_LINE"]["first_wait_s"] >= 0.25
    ns["save_pocket_pace"](1.3, 0.0)
    assert 0.25 < _waited(ns) < 0.6
    mc = _MC(); mc.first.set()
    t0 = time.time(); ns["_pocket_first_wait"](mc, None, 0.0, 0.0, 24000)
    assert time.time() - t0 < 0.1, "already stamped: no wait"
    d = json.loads((tmp_path / "logs" / "pocket-pace.json").read_text())
    d["at"] = time.time() - 700
    (tmp_path / "logs" / "pocket-pace.json").write_text(json.dumps(d))
    assert _waited(ns) < 0.1, "an old line says nothing about now"


def test_the_wait_is_a_packs_and_is_asked_before_her_air_time():
    i = SPEAK.index("    mc = getattr(early, \"mc\", None) if early is not None else None")
    assert SPEAK.index("        _pocket_first_wait(mc, early.reader, t_open, first_hop_ms, sr)", i) < SPEAK.index(
        "    air_at = time.time() + LEAD_SILENCE_MS / 1000.0 + MOUTH_LATENCY_MS / 1000.0", i)
    # round 17: the wait before the pad is the fallback when the stamp hold is off (HOUSE_SPEAK_STAMP_HOLD=0)
    assert "    if mc is not None and not _HOUSE and not hold_on:\n        _pocket_first_wait(" in SPEAK
    assert 'save_pocket_pace(self._save_after, max(late) if late else 0.0)' in SPEAK
