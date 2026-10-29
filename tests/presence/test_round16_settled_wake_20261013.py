"""Round 16 (2026-10-13): a settled face wakes at once on a change between its ticks, on a newcomer, and on a session
opened on her name. Found in a sandbox on a film camera: a cut from one person to another 0.5-1 s before a 1 s settled
tick was never seen (the span test looks 0.5 s back), and a newcomer in the same seat kept the count at 1."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from crissa_presence import idle_gate as G


class _Frame:
    def __init__(self, ts, bgr):
        self.ts, self.bgr = ts, bgr


A = np.full((360, 640, 3), 90, dtype=np.uint8)
B = np.full((360, 640, 3), 200, dtype=np.uint8)


def _settle(g, t, n=1, names=("pat",)):
    for _ in range(30):
        frames = [_Frame(t["now"] - 1.1 + i * 0.1, A) for i in range(12)]
        for _ in g.pick(frames, t["now"]):
            g.saw(n, t["now"])
        g.who(names, 0, t["now"])
        t["now"] += 1.2                              # a settled tick: 1 s plus the tick's own work
    assert g.settled(t["now"])


def test_a_change_early_in_a_settled_tick_wakes_it():
    t = {"now": 1000.0}
    g = G.IdleGate(enabled=True, clock=lambda: t["now"], settle=True)
    _settle(g, t)
    # the cut came 0.8 s before this tick: the newest frame and the one 0.5 s before it are both after it
    frames = [_Frame(t["now"] - 1.15, A), _Frame(t["now"] - 1.05, A)] + \
        [_Frame(t["now"] - 0.8 + i * 0.1, B) for i in range(9)]
    assert len(g.pick(frames, t["now"])) == len(frames) and not g.settled(t["now"])


def test_without_the_switch_that_cut_is_missed_as_before(monkeypatch):
    t = {"now": 1000.0}
    g = G.IdleGate(enabled=True, clock=lambda: t["now"], settle=True)
    g.wake_new = False                               # CRISSA_PRESENCE_SETTLED_WAKE=0
    _settle(g, t)
    frames = [_Frame(t["now"] - 1.15, A), _Frame(t["now"] - 1.05, A)] + \
        [_Frame(t["now"] - 0.8 + i * 0.1, B) for i in range(9)]
    assert len(g.pick(frames, t["now"])) == 1
    assert G.settled_wake_on({"CRISSA_PRESENCE_SETTLED_WAKE": "0"}) is False and G.settled_wake_on({}) is True


def test_a_newcomer_in_the_same_seat_wakes_it_and_a_known_face_back_does_not():
    t = {"now": 1000.0}
    g = G.IdleGate(enabled=True, clock=lambda: t["now"], settle=True)
    _settle(g, t)
    assert g.who([], 1, t["now"]) is True and not g.settled(t["now"])    # an unnamed face where his was
    t["now"] += G.SETTLE_AFTER_S + 1
    g.saw(1, t["now"])                                                  # the face is still there
    assert g.settled(t["now"])
    assert g.who(["pat"], 0, t["now"]) is False and g.settled(t["now"])  # him again: seen since the room was empty
    assert g.who([], 1, t["now"]) is False                              # a flicker to unnamed: seen once already
    assert g.who(["pat", "sam"], 0, t["now"]) is True                   # a name not seen yet


def test_an_empty_room_forgets_who_it_saw():
    t = {"now": 1000.0}
    g = G.IdleGate(enabled=True, clock=lambda: t["now"], settle=True)
    _settle(g, t)
    t["now"] += G.IDLE_AFTER_S + 60                                     # nobody, nothing moving: the rest
    assert g.resting(t["now"])
    assert g.who([], 0, t["now"]) is False and g.seen_names == set()


def test_presence_tells_the_gate_who_it_named_and_a_session_on_her_name_is_a_turn():
    src = (Path(__file__).resolve().parents[2] / "modules" / "presence" / "crissa_presence" / "service.py").read_text()
    body = src[src.index("    def run_door_once(self)"):src.index("    def _idle_gate(self)")]
    assert body.index("door_names = sorted(best.keys())") < body.index("self._idle_gate().who(door_names, door_unnamed)")
    f2f = src[src.index("    def set_f2f("):]
    f2f = f2f[:f2f.index("hour_i = None")]
    assert "if on:" in f2f and "self._idle_gate().turn(now)" in f2f
