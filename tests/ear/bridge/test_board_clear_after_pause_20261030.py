"""What she leaves on the screen clears 30 s after she pauses, unless he asked her to leave it up (the User 2026-10-30:
"If she leaves it up I want it to self-clear 30s after conversation ends"; "make it so I would have to request she
leaves it posted is all"). She is told to keep his screen tidy herself."""
from __future__ import annotations

from crissa_bridge import display_call
from crissa_bridge import ear_bridge as B


def _fake(monkeypatch, after=0.05):
    cleared = []
    monkeypatch.setattr(B, "BOARD_CLEAR_AFTER_S", after)
    monkeypatch.setattr(B, "clear_session_board", lambda: cleared.append(1))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)            # the timer thread runs at once
    B.board_pin(False)
    return cleared


def _settle():
    import threading
    for t in threading.enumerate():
        if t.name == "board-clear-later":
            t.join(2)


def test_the_pause_clears_later_and_a_new_session_keeps_it(monkeypatch):
    cleared = _fake(monkeypatch)
    B.clear_session_board_later()
    _settle()
    assert cleared == [1]
    monkeypatch.setattr(B.time, "sleep", lambda s: B.board_keep())  # a session opens inside the 30 s
    B.clear_session_board_later()
    _settle()
    assert cleared == [1]


def test_leave_it_up_skips_the_clear_until_something_new(monkeypatch):
    cleared = _fake(monkeypatch)
    assert display_call.keep_heard("leave it up please")
    assert display_call.keep_heard("Keep the page on the screen")
    assert not display_call.keep_heard("take it down")
    B.board_pin(True)
    B.clear_session_board_later()
    _settle()
    assert cleared == []
    B.board_pin(False)
    B.clear_session_board_later()
    _settle()
    assert cleared == [1]


def test_zero_clears_at_the_pause_as_before(monkeypatch):
    cleared = _fake(monkeypatch, after=0)
    B.clear_session_board_later()
    assert cleared == [1]


def test_her_habit_line():
    assert "take it down" in display_call.SCREEN_HABIT_LINE and "leave it up" in display_call.SCREEN_HABIT_LINE
