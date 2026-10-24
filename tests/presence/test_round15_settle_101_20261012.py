"""Round 15 (2026-10-12, .101: presence still 183 % at rest on round 14). Why it never settled, read on .101: the
motion test compared each tick's newest frame with the previous tick's, and a busy tick there takes ~3.5 s; .101's own
camera with the User at the desk (64x36 grey mean difference) - 0.5 s apart median 2.1, p90 9.8; 3.5 s apart median 9.4,
24 of 33 over the level of 6. And every speech onset the ear heard re-armed the turn hold, matched or not. Now: motion
over a fixed 0.5 s span inside the buffer; a settled face needs a BIG change (3x the level) to wake; only a matched
voice is a turn; a settled face ticks once a second."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from crissa_presence import idle_gate as G


class _Frame:
    def __init__(self, ts, bgr):
        self.ts, self.bgr = ts, bgr


def _scene(level: float):
    """A grey room with a 'person' block whose brightness is shifted by `level` steps (64x36 mean diff ~ level/6)."""
    img = np.full((360, 640, 3), 90, dtype=np.uint8)
    img[60:300, 160:480] = np.uint8(min(255, 120 + level))
    return img


def test_a_slow_tick_with_a_person_moving_in_place_still_settles():
    t = {"now": 1000.0}
    g = G.IdleGate(enabled=True, clock=lambda: t["now"], settle=True)
    settled = 0
    for k in range(40):                                        # 3.5 s ticks, as on .101 while busy
        base = _scene(30.0 * (k % 2))                          # the person shifts between ticks (diff ~12: over 6)
        frames = [_Frame(t["now"] - 3.5 + i * 0.1, base) for i in range(35)]   # still within the 0.5 s span
        for _ in g.pick(frames, t["now"])[:12]:
            g.saw(1, t["now"])
        settled += 1 if g.settled(t["now"]) else 0
        t["now"] += 3.5
    assert settled >= 35, settled


def test_a_big_change_inside_the_span_wakes_a_settled_face():
    t = {"now": 1000.0}
    g = G.IdleGate(enabled=True, clock=lambda: t["now"], settle=True)
    for _ in range(30):
        frames = [_Frame(t["now"] - i * 0.1, _scene(0.0)) for i in range(8)]
        for _ in g.pick(frames, t["now"]):
            g.saw(1, t["now"])
        t["now"] += 0.6
    assert g.settled(t["now"])
    big = np.full((360, 640, 3), 230, dtype=np.uint8)
    frames = [_Frame(t["now"] - i * 0.1, _scene(0.0)) for i in range(7, 0, -1)] + [_Frame(t["now"], big)]
    assert len(g.pick(frames, t["now"])) == 8 and not g.settled(t["now"])


def test_a_settled_face_ticks_once_a_second():
    t = {"now": 1000.0}
    g = G.IdleGate(enabled=True, clock=lambda: t["now"], settle=True)
    for _ in range(30):
        for _ in g.pick([_Frame(t["now"] - i * 0.1, _scene(0.0)) for i in range(6)], t["now"]):
            g.saw(1, t["now"])
        t["now"] += 0.5
    assert g.settled(t["now"]) and g.interval(0.5, t["now"]) == G.SETTLED_INTERVAL_S == 1.0


def test_only_a_matched_voice_is_a_turn():
    src = (Path(__file__).resolve().parents[2] / "modules" / "presence" / "crissa_presence" / "service.py").read_text()
    body = src[src.index("    def run_door_once(self)"):src.index("    def _idle_gate(self)")]
    assert 'lv = getattr(self, "_last_user_voice", None) or {}' in body
    assert 'if lv.get("ts") and lv.get("match", True):' in body
    assert "_last_spoke_voice" not in body[body.index("A TURN IS A MATCHED VOICE"):body.index("scan = self._idle_gate()")]
