"""Round 27, her display tool (.101 2026-10-16, check 4): "take it down" over her reading was side talk and, as a Guest
turn, twice drew "It's gone now" with the page still up; she read a whole recipe aloud with the page on screen; and she
was put on a 409 x 230 tile where he could not see her."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "modules" / "ear"), str(ROOT / "lib")]

from crissa_bridge import display_call as dc  # noqa: E402

BRIDGE = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text()


def test_his_words_from_the_101_are_take_downs(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_TAKE_DOWN", raising=False)
    monkeypatch.delenv("CRISSA_BRIDGE_DISPLAY", raising=False)
    for t in ("Good. Right now, take it down.", "Crissa, can you take down the document?",
              "Can you remove the document? Take it down.", "Clear the screen.", "close the page please"):
        assert dc.take_down_heard(t), t
    for t in ("Show me a recipe to make.", "White bread, white yeast bread.",
              "I took the dog down to the park this morning and it was a lovely walk with everyone"):
        assert not dc.take_down_heard(t), t
    monkeypatch.setenv("EAR_BRIDGE_TAKE_DOWN", "0")
    assert not dc.take_down_heard("take it down")


def test_take_down_clears_at_the_conductor_and_says_only_what_happened():
    sent = []
    ok, res = dc.take_down("http://x/v1/display-tool",
                           post=lambda u, b: sent.append(b) or {"result": "cleared: the sheet is down"})
    assert ok and sent == [{"name": "display", "arguments": {"what": "clear"}}]
    ok, res = dc.take_down("http://x", post=lambda u, b: {"error": "NOT DONE: refused"})
    assert not ok and res.startswith("NOT DONE")


def test_the_bridge_takes_it_down_before_side_talk_and_the_name_rule():
    i = BRIDGE.index("display_call.take_down_heard(text)")
    assert i < BRIDGE.index("if name_interrupt_hit(", i) < BRIDGE.index("_side_talk_turn(t, text, now):", i)
    body = BRIDGE[BRIDGE.index("def _display_take_down("):BRIDGE.index("def session_board_fields(")]
    assert 'abort_mouth("take_down")' in body and "DOWN_LINE" in body and "NOT_DOWN_LINE" in body


def test_a_page_up_is_one_short_line_unless_he_asked_to_read_it(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_SHOWN_SHORT", raising=False)
    shown = [{"id": "a", "name": "display", "args": "{}", "result": "shown: Bread (document, 25 lines) in the sheet"}]
    assert dc.short_after(shown, "Show me a recipe for white bread") == dc.SHOWN_MAX_TOKENS
    assert dc.short_after(shown, "Show me the recipe and read it to me") is None
    assert dc.short_after([dict(shown[0], result="NOT DONE: x")], "show it") is None
    msgs = dc.follow_up([{"role": "user", "content": "show"}], "", shown)
    assert msgs[-1]["content"].endswith(dc.SHOWN_NOTE) and "do not read" in dc.SHOWN_NOTE
    assert "max_tokens" in BRIDGE[BRIDGE.index("def _display_round("):BRIDGE.index("def _display_take_down(")]
    monkeypatch.setenv("EAR_BRIDGE_SHOWN_SHORT", "0")
    assert dc.short_after(shown, "show it") is None


def test_the_tool_asks_which_before_showing_a_vague_one():
    import crissa_display
    assert "ask which first" in crissa_display.TOOL["function"]["description"]
