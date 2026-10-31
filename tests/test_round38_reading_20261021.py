"""Round 38 (.101 round 37: the launch demo's reading beat). "Can you display that document" closed her reading as a
new subject, and then three plain asks to read on were refused - NO_RESUME_NOTE ("do not continue it") and READ_ASK_NOTE
("read it aloud") were both in the turn, and the model took the refusal. Now the document is part of the reading, and an
ask to read carries only the read note. Kill EAR_BRIDGE_ASK_WINS=0."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "ear"))
sys.path.insert(0, str(ROOT / "lib"))
from crissa_bridge import ear_bridge as eb  # noqa: E402

BRIDGE = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("text", ["Can you display that document", "Show me the doc", "Put the text up on the screen"])
def test_the_document_is_part_of_the_reading(text):
    assert eb.about_reading(text, {"title": "Moby-Dick"}), "a turn about the shown document never closes the reading"


def test_another_subject_still_closes_it():
    assert not eb.about_reading("What's the weather in Boston?", {"title": "Moby-Dick"})


def test_an_ask_to_read_is_the_only_note():
    disp = BRIDGE.split("    read_turn, read_note = reading_turn(", 1)[1].split("    rmeta: dict = {}", 1)[0]
    assert "if ask_note and ASK_WINS:" in disp and "        turn_note = READ_ASK_NOTE\n" in disp
    assert disp.index("if ask_note and ASK_WINS:") < disp.index("turn_note += READ_ASK_NOTE")   # the kill keeps both
    assert eb.ASK_WINS is True


GENESIS = ("In the beginning God created the heaven and the earth. And the earth was without form, and void; and "
           "darkness was upon the face of the deep. And the Spirit of God moved upon the face of the waters. ")


@pytest.fixture
def mem(monkeypatch):
    monkeypatch.setattr(eb, "_READING_STATE", {"closed_at": 0.0, "why": ""})
    monkeypatch.setattr(eb.cancels, "latest", lambda: {})
    monkeypatch.setattr(eb, "_log", lambda *a, **k: None)
    m = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    monkeypatch.setattr(eb, "_CONVO", m)
    m.add("I start reading from Genesis", "Genesis, chapter one. " + GENESIS, reading=True, stopped=True)
    return m


def test_101s_turns_reopen_the_closed_reading_where_she_stopped(mem, monkeypatch):
    """bridge.log 15:12-15:16 on .101: the display turn no longer closes it; and when a reading WAS closed, each of
    his go-ons reopens it at her place - never the refusal."""
    assert eb.about_reading("Can you display that document for me? And what was your tool?", {"title": "Genesis"})
    eb.close_reading("new_subject")                     # as round 37 did on that turn
    assert eb.reading_record(mem) is None
    for turn in ("Will you start reading again from where you left off?",
                 "Yes, you will. Please read from Dennis's Genesis. Continue where he left off."):
        eb.close_reading("new_subject")
        is_read, note = eb.reading_turn(turn, mem)
        assert is_read and note and "go on" in note.lower(), (turn, note)
        assert eb.reading_record(mem) is not None, "open again after his go-on"
    monkeypatch.setattr(eb, "GO_ON_REOPENS", False)
    eb.close_reading("new_subject")
    assert eb.reading_turn("Continue where you left off.", mem)[1] == "", "the kill: round 37's closed reading"


def test_the_note_in_his_words():
    n = eb.NO_RESUME_NOTE
    assert "Don't wander back into it on your own" in n and "unless they ask you to read or go on; then do it" in n
    assert "never" in n and "simply refuse" in n and "Do not continue it" not in n
