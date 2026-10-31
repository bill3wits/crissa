"""Round 26: the rest of her reply starts while her clause plays (.101 10-16: 0.7-0.9 s of silence before every
sentence after the first - each piece its own house-speak, 0.61-0.72 s from start to voice)."""
from __future__ import annotations

from pathlib import Path

from crissa_bridge import prespeak as P

ROOT = Path(__file__).resolve().parents[3]


class _Proc:
    def __init__(self):
        self.pid, self.killed = 999999, False

    def poll(self):
        return 0 if self.killed else None

    def terminate(self):
        self.killed = True


def _arm(pre, airs, token=1, now_token=None, wait_s=5.0):
    spawned = []
    seq = list(airs)

    def spawn(text):
        p = _Proc()
        spawned.append((text, p))
        return p, None
    t = pre.arm("And the rest.", token, 100.0, spawn=spawn, air_at=lambda: seq.pop(0) if seq else seq_last[0],
                token_now=now_token or (lambda: token), wait_s=wait_s, sleep=lambda s: None)
    t.join(2)
    return spawned


seq_last = [None]


def test_it_starts_only_once_her_clause_is_on_the_air(monkeypatch):
    monkeypatch.setattr(P, "on", lambda: True)
    pre = P.Pre()
    seq_last[0] = 100.4
    spawned = _arm(pre, [None, 50.0, 99.0])           # no air, an old line's air, then this clause's
    assert [t for t, _ in spawned] == ["And the rest."]
    got = pre.take("And the rest.", 1)
    assert got is not None and got[0] is spawned[0][1]
    assert pre.take("And the rest.", 1) is None          # taken once


def test_another_text_or_turn_kills_it_unheard(monkeypatch):
    monkeypatch.setattr(P, "on", lambda: True)
    pre = P.Pre()
    seq_last[0] = 101.0
    spawned = _arm(pre, [])
    assert pre.take("A revised rest.", 1) is None and spawned[0][1].killed
    spawned = _arm(pre, [])
    assert pre.take("And the rest.", 2) is None and spawned[0][1].killed


def test_a_stop_before_the_air_starts_nothing_and_no_air_gives_up(monkeypatch):
    monkeypatch.setattr(P, "on", lambda: True)
    pre = P.Pre()
    seq_last[0] = 101.0
    assert _arm(pre, [], token=1, now_token=lambda: 2) == []
    seq_last[0] = None
    logged = []
    t = pre.arm("x", 1, 100.0, spawn=lambda s: (_Proc(), None), air_at=lambda: None, token_now=lambda: 1,
                log=logged.append, wait_s=0.0, sleep=lambda s: None)
    t.join(2)
    assert pre.item is None and logged == [{"event": "prespeak_skipped", "why": "clause_not_on_air"}]


def test_drop_kills_and_the_kill_switch(monkeypatch):
    monkeypatch.setattr(P, "on", lambda: True)
    pre = P.Pre()
    seq_last[0] = 101.0
    spawned = _arm(pre, [])
    pre.drop()
    assert spawned[0][1].killed and pre.item is None
    monkeypatch.setenv("EAR_BRIDGE_PRESPEAK", "0")
    monkeypatch.undo()
    monkeypatch.setenv("EAR_BRIDGE_PRESPEAK", "0")
    assert P.on() is False and P.Pre().arm("x", 1, 0.0, spawn=None, air_at=None, token_now=None) is None


def test_the_bridge_wires_it():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    assert "_pre = None if not_before else prespeak.PRE.take(spoken, token)" in src
    assert src.count("prespeak.PRE.drop()") == 2                        # abort_mouth and the turn's end
    assert "prespeak_rest(rest_after_clause(reply, first[\"clause\"]), gw[\"voice\"], token)" in src
