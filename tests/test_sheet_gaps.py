"""A pause in the voice is a pause in the sheet (the User 2026-10-12, "I agree to all fixes").

Turbo streams a line sentence by sentence and the player runs dry between them; the sheet stamped rows as if the audio
never stopped, so every row after a pause landed early. MouthAir now moves the rest of the line by the pause.
"""
from __future__ import annotations

import importlib.util
import queue
from pathlib import Path

import pytest

_SPEAK = Path(__file__).resolve().parents[1] / "scripts" / "house-speak.py"
_spec = importlib.util.spec_from_file_location("house_speak_gaps", _SPEAK)
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)

SR = 24000
HOP = int(SR * hs.MOUTH_HOP_MS / 1000.0)
D = 10 * HOP / SR                                   # ten hops of audio, seconds


class _Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


@pytest.fixture
def air(monkeypatch):
    def make(on=True, t0=1000.0):
        monkeypatch.setattr(hs.MouthAir, "_drain", lambda self: None)       # nothing reaches a wall
        monkeypatch.setattr(hs, "sheet_lead_s", lambda: 0.0)
        monkeypatch.setattr(hs, "sheet_gaps_on", lambda: on)
        clk = _Clock(t0)
        monkeypatch.setattr(hs.time, "time", clk.time)
        a = hs.MouthAir(t0 + 0.5, SR)
        a._post_lead_prefix = lambda: None
        a._viseme_batch = lambda n, **k: [3] * n
        return a, clk
    return make


def _posts(a):
    out = []
    while True:
        try:
            out.append(a._q.get_nowait())
        except queue.Empty:
            return out


def _pcm(hops):
    return b"\x10\x00" * (HOP * hops)


def test_a_dry_player_moves_the_rest_of_the_line(air):
    a, clk = air(on=True)
    a.feed(_pcm(10))                                    # the first sentence, ten hops of audio
    first = [p for p in _posts(a) if p.get("rms")]
    assert first and first[0]["pts0"] == pytest.approx(a.air_at)
    clk.t = a.air_at + D + 0.6                        # Turbo took 0.6 s past the end of it
    a.feed(_pcm(10))
    posts = _posts(a)
    rest = [p for p in posts if p.get("rms") == [0.0]]
    assert rest and rest[0]["pts0"] == pytest.approx(a.air_at + D)          # the rest row where she stopped
    moved = [p for p in posts if p.get("rms") and p["rms"] != [0.0]][0]
    heard = clk.t + hs.MOUTH_LATENCY_MS / 1000.0
    assert moved["pts0"] == pytest.approx(heard)                               # the next sentence at its own time
    assert a.sheet_end() == pytest.approx(heard + D)


def test_a_full_player_keeps_todays_stamps(air):
    a, clk = air(on=True)
    a.feed(_pcm(10))
    _posts(a)
    clk.t = a.air_at + 0.05                             # audio still queued: no pause
    a.feed(_pcm(10))
    moved = [p for p in _posts(a) if p.get("rms")][0]
    assert moved["pts0"] == pytest.approx(a.air_at + D) and a.gap_s == 0.0


def test_off_is_todays_clock(air):
    a, clk = air(on=False)
    a.feed(_pcm(10))
    _posts(a)
    clk.t = a.air_at + 0.8
    a.feed(_pcm(10))
    moved = [p for p in _posts(a) if p.get("rms")][0]
    assert moved["pts0"] == pytest.approx(a.air_at + D) and a.gap_s == 0.0
    assert a.sheet_end() == pytest.approx(a.air_at + 2 * D)


def test_the_whole_line_repost_splits_at_the_pause(air):
    a, clk = air(on=True)
    a.feed(_pcm(10))
    clk.t = a.air_at + 0.8
    a.feed(_pcm(10))
    _posts(a)
    a._viseme_batch = lambda n, **k: list(range(n))
    a._restamp()
    segs = [p for p in _posts(a) if p.get("rms")]
    assert len(segs) == 2
    assert segs[0]["pts0"] == pytest.approx(a.air_at) and len(segs[0]["rms"]) == 10
    assert segs[1]["pts0"] == pytest.approx(a.air_at + a.gap_s + D) and segs[1]["visemes"][0] == 10


def test_on_in_the_house_and_a_pack_and_a_file_turns_it_off(monkeypatch, tmp_path):
    monkeypatch.delenv("HOUSE_SPEAK_SHEET_GAPS", raising=False)
    import crissa_home
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.setattr(hs, "_HOUSE", False)
    assert hs.sheet_gaps_on() is True, "a pack's Pocket runs dry too (round 15)"
    monkeypatch.setattr(hs, "_HOUSE", True)
    assert hs.sheet_gaps_on() is True
    (tmp_path / "mouth-sheet-gaps.off").write_text("")
    assert hs.sheet_gaps_on() is False
    monkeypatch.setenv("HOUSE_SPEAK_SHEET_GAPS", "1")
    assert hs.sheet_gaps_on() is True
