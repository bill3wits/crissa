"""An ordinary answer speaks in full; only a tool-result callout is trimmed, and at a sentence (the User 2026-10-02).

Since 2026-08-12 every free-air reply went through the callout trim: two sentences, then a raw [:220] slice.
10-02 04:19 the model returned 681 chars and she said 220, ending "...persisted there, so when".
"""
from __future__ import annotations

from pathlib import Path

from crissa_bridge.ear_bridge import CALLOUT_MAX, _spoken_tool_callout

SRC = (Path(__file__).resolve().parents[3] / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")

ANSWER_0419 = (
    "Yeah, it's a real SQLite database wired to full-text search, sitting right here at state.db with FTS5 "
    "indexed. That's my canonical session store, everything I've talked about and done gets persisted there, "
    "so when I search memory I'm actually running real queries against it, not guessing. Plus on top of that "
    "I've got the skills system, structured how-to files with reference docs. Want me to pull something out "
    "of it, or were you checking the wiring?"
)


def test_an_ordinary_answer_is_spoken_whole():
    out = _spoken_tool_callout(ANSWER_0419)
    assert out == ANSWER_0419 and out.endswith("checking the wiring?")
    assert len(out) > CALLOUT_MAX


def test_an_ordinary_answer_keeps_its_offer_and_its_opener():
    text = "Let me check that. It is filed. Want me to walk through it?"
    assert _spoken_tool_callout(text) == text


def test_a_callout_ends_at_a_sentence_never_mid_word():
    out = _spoken_tool_callout(ANSWER_0419, callout=True)
    assert len(out) <= CALLOUT_MAX and out.endswith(".") and out.startswith("Yeah, it's a real SQLite database")
    assert "so when" not in out                     # the 04:19 mid-thought ending cannot happen


def test_a_callout_whose_first_sentence_runs_long_is_cut_at_a_whole_word():
    long = "The board " + "has many open cards waiting " * 12 + "today."
    out = _spoken_tool_callout(long, callout=True)
    assert len(out) <= CALLOUT_MAX + 1 and out.endswith(".")
    assert long.startswith(out[:-1]) and long[len(out) - 1] == " "    # the cut is between words


def test_a_callout_still_drops_padding():
    out = _spoken_tool_callout("Board survey complete. Five cards are open. Want me to walk through them?", callout=True)
    assert out == "Five cards are open"


def test_the_reply_path_calls_it_without_callout():
    assert "content = _spoken_tool_callout(content)\n" in SRC
