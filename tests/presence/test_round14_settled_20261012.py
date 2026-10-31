"""Round 14 (2026-10-12, .101: presence at 188-209 % while the User sat at the desk, and at rest). A face that stays
put is not news: once the same number of faces has been seen, nothing has moved and nobody has spoken for a while,
the door loop scans the newest frame only. Motion, a change in the count, or a voice turn bring the full rate back."""
from __future__ import annotations

import numpy as np

from crissa_presence import idle_gate as G


class _Frame:
    def __init__(self, ts, bgr):
        self.ts, self.bgr = ts, bgr


ROOM = np.full((360, 640, 3), 90, dtype=np.uint8)


def _gate(t, **kw):
    return G.IdleGate(enabled=True, clock=lambda: t["now"], **kw)


def _tick(gate, t, n_faces=1, bgr=ROOM):
    scan = gate.pick([_Frame(t["now"] - i * 0.04, bgr) for i in range(12)], t["now"])
    for _ in scan:
        gate.saw(n_faces, t["now"])
    t["now"] += 0.5
    return len(scan)


def test_a_still_face_settles_to_the_newest_frame():
    t = {"now": 1000.0}
    gate = _gate(t, settle=True)
    first = [_tick(gate, t) for _ in range(int(G.SETTLE_AFTER_S / 0.5))]
    assert all(n == 12 for n in first)                         # the full rate while it is new
    later = [_tick(gate, t) for _ in range(40)]
    assert all(n == 1 for n in later[2:]) and gate.settled(t["now"])
    assert sum(later) / 20.0 <= 2.5                            # ~2 scans a second, was 24


def test_a_turn_brings_the_full_rate_back_for_the_hold():
    t = {"now": 1000.0}
    gate = _gate(t, settle=True)
    for _ in range(40):
        _tick(gate, t)
    assert gate.settled(t["now"])
    gate.turn(t["now"])
    assert _tick(gate, t) == 12 and not gate.settled(t["now"])
    t["now"] += G.TURN_HOLD_S + 0.5
    assert _tick(gate, t) == 1


def test_a_new_face_and_motion_bring_the_full_rate_back():
    t = {"now": 1000.0}
    gate = _gate(t, settle=True)
    for _ in range(40):
        _tick(gate, t)
    assert _tick(gate, t, n_faces=2) == 1                       # this tick's scan finds the second face...
    assert _tick(gate, t, n_faces=2) == 12                      # ...and the next one is at the full rate
    for _ in range(40):
        _tick(gate, t, n_faces=2)
    moved = ROOM.copy()
    moved[100:260, 200:440] = 230
    assert _tick(gate, t, n_faces=2, bgr=moved) == 12


def test_an_empty_room_still_rests_and_the_switch_keeps_the_old_full_rate():
    t = {"now": 1000.0}
    gate = _gate(t, settle=True)
    for _ in range(int((G.IDLE_AFTER_S + 2) / 0.5)):
        _tick(gate, t, n_faces=0)
    assert gate.resting(t["now"]) and not gate.settled(t["now"])
    t2 = {"now": 1000.0}
    old = _gate(t2, settle=False)
    assert all(_tick(old, t2) == 12 for _ in range(60))
    assert G.settle_on({"CRISSA_PRESENCE_SETTLE": "0"}) is False and G.settle_on({}) is True


def test_presence_tells_the_gate_of_each_voice_turn():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "modules" / "presence" / "crissa_presence" / "service.py").read_text()
    body = src[src.index("    def run_door_once(self)"):src.index("    def _idle_gate(self)")]
    i = body.index("self._idle_gate().turn(float(lv[\"ts\"]))")
    assert i < body.index("scan = self._idle_gate().pick(frames, now)")
