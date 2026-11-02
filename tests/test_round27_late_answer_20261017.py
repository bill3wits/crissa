"""Round 27: a late answer to her question still counts (.101 2026-10-16: his answer 24 s after her no-reply line was
dropped as paused - "it just goes to paused")."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "modules" / "ear"), str(ROOT / "lib")]

from crissa_bridge import ear_bridge as eb  # noqa: E402


def test_one_turn_within_the_window_after_the_no_reply_line(monkeypatch):
    monkeypatch.setattr(eb, "LATE_ANSWER_S", 60.0)
    eb._ASKED.update(open=True, late_until=0.0)
    line = eb.say_no_reply("Crissa", speak=lambda t: None)
    assert line and eb._ASKED["late_until"] > 0
    t = eb._ASKED["late_until"] - 36.0                   # 24 s after her line
    assert eb.late_answer_open(t) is True and eb.late_answer_open(t) is False      # once


def test_past_the_window_or_killed_it_is_paused_as_before(monkeypatch):
    monkeypatch.setattr(eb, "LATE_ANSWER_S", 60.0)
    eb._ASKED.update(open=True, late_until=0.0)
    eb.say_no_reply("Crissa", speak=lambda t: None)
    assert eb.late_answer_open(eb._ASKED["late_until"] + 1) is False
    monkeypatch.setattr(eb, "LATE_ANSWER_S", 0.0)
    eb._ASKED.update(late_until=10 ** 12)
    assert eb.late_answer_open(0) is False


def test_the_loop_opens_the_session_for_it_before_the_gate():
    src = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text()
    i = src.index("late_answer_open(now)")
    assert i < src.index("gate = classify_hear(", i) and "hold_session(now" in src[i:i + 300]
